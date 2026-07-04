"""Servicio de entrevista agnóstico al canal (núcleo síncrono).

Coordina el motor durable (InterviewRunner) con la persistencia de negocio
(Supabase) y la notificación al reclutador. Lo invocan los adapters de canal
(Telegram hoy, WhatsApp después) en un hilo worker, porque tanto el grafo como
supabase-py son síncronos.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from agente.graph import InterviewRunner
from agente.prompts import (
    NO_OPEN_VACANCY,
    SCHEDULING_CONFIRMED,
    SCHEDULING_CONFIRMED_ONSITE,
    VACANCY_UNAVAILABLE,
)
from agente.state import (
    PHASE_AWAITING_DOCS,
    PHASE_CLOSED,
    PHASE_FINISHED,
    PHASE_INTERVIEWING,
    PHASE_SCHEDULED,
    PHASE_SCHEDULING,
    InterviewState,
)
from channels.base import InboundMessage
from db import repositories
from core.logging_config import get_logger
from integrations.scheduling import (
    MeetingResult,
    compute_free_slots,
    get_scheduler,
    human_slot_long,
)

# Callback que notifica al reclutador cuando termina una entrevista.
#   notify(vacancy, candidate, conversation, scorecard) -> None
RecruiterNotifier = Callable[[dict, dict, dict, dict], None]
# Callback que notifica la reunión agendada (email + Telegram al reclutador).
#   notify(vacancy, candidate, meeting, recruiter) -> None
MeetingNotifier = Callable[[dict, dict, dict, dict], None]

logger = get_logger("agente.service")


def _advisory_key(thread_id: str) -> int:
    """Entero de 64 bits con signo (cabe en el bigint de Postgres) derivado del thread_id.

    `pg_advisory_lock` toma una key numérica; el thread_id es "canal:chat". blake2b da un
    digest estable → misma conversación, misma key en cualquier proceso/réplica."""
    digest = hashlib.blake2b(thread_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_document_b64(local_path: str) -> tuple[str, int]:
    """Lee un archivo de disco → (base64, tamaño en bytes). ('', 0) si no se puede leer."""
    if not local_path:
        return ("", 0)
    import base64
    from pathlib import Path

    try:
        data = Path(local_path).read_bytes()
        return (base64.b64encode(data).decode("ascii"), len(data))
    except Exception:  # noqa: BLE001 — sin archivo, degradamos a solo-metadata
        return ("", 0)


@dataclass
class TurnResult:
    messages: list[str] = field(default_factory=list)
    show_consent_buttons: bool = False
    finished: bool = False
    declined: bool = False


def _vacancy_subset(vacancy: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": vacancy.get("title", ""),
        "intro_message": vacancy.get("intro_message", ""),
        "details_message": vacancy.get("details_message", ""),
        "company_info": vacancy.get("company_info", ""),
        "semaphore_thresholds": vacancy.get("semaphore_thresholds")
        or {"green_min": 75, "yellow_min": 50},
    }


def _to_qspec(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row["id"],
        "position": row["position"],
        "text": row["text"],
        "criterion": row.get("criterion", ""),
        "weight": float(row.get("weight", 1.0)),
        "max_follow_ups": int(row.get("max_follow_ups", 0)),
        "cv_field": row.get("cv_field"),
        "label": row.get("label") or "",
    }


class InterviewService:
    def __init__(
        self,
        runner: InterviewRunner,
        *,
        notify_recruiter: Optional[RecruiterNotifier] = None,
        notify_meeting: Optional[MeetingNotifier] = None,
        scheduler: Optional[Any] = None,
        settings: Optional[Any] = None,
        database_url: Optional[str] = None,
    ):
        self.runner = runner
        self.notify_recruiter = notify_recruiter
        self.notify_meeting = notify_meeting
        self.scheduler = scheduler
        self.settings = settings
        # Serialización por thread (audit A3): el barrido de inactividad y un mensaje del
        # candidato pueden invocar el motor del MISMO thread desde dos hilos worker a la
        # vez; LangGraph no serializa, así que dos send() concurrentes pisarían el
        # checkpoint. Un lock por thread_id (el dict crece un puntero por conversación
        # vista en la vida del proceso — despreciable) elimina la carrera EN EL PROCESO.
        self._thread_locks: dict[str, threading.Lock] = {}
        self._thread_locks_guard = threading.Lock()
        # Lock distribuido (roadmap v2, paso 3 · auditoria_v2 Riesgo 3): con `replicas>1`
        # (webhook), dos updates del mismo chat pueden caer en pods distintos → el lock
        # in-process no basta. Con `database_url`, además tomamos un advisory lock de
        # Postgres por thread_id (patrón del scheduler). Sin él, o ante fallo de la DB,
        # se degrada al lock local. Pool perezoso (no abre conexión hasta el primer turno).
        self.database_url = database_url or None
        self._lock_pool: Any = None
        self._lock_pool_guard = threading.Lock()

    def _thread_lock(self, thread_id: str) -> threading.Lock:
        with self._thread_locks_guard:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    def _get_lock_pool(self):
        """Pool psycopg dedicado a advisory locks (perezoso). None si no hay database_url
        o si el pool no pudo abrirse (degrada a lock local, sin reintentar)."""
        if not self.database_url:
            return None
        if self._lock_pool is not None:
            return self._lock_pool
        with self._lock_pool_guard:
            if self._lock_pool is None and self.database_url:
                try:
                    from psycopg_pool import ConnectionPool

                    self._lock_pool = ConnectionPool(
                        self.database_url, min_size=0, max_size=4,
                        kwargs={"autocommit": True}, open=True,
                    )
                except Exception:  # noqa: BLE001 — sin pool, se sigue con el lock local
                    logger.warning(
                        "Lock distribuido: no se pudo abrir el pool; se usa solo el lock local",
                        exc_info=True,
                    )
                    self.database_url = None
        return self._lock_pool

    def _acquire_advisory(self, thread_id: str):
        """Toma el advisory lock de Postgres para el thread; devuelve la conexión que lo
        sostiene, o None si no hay pool / falla (degradación a lock local)."""
        pool = self._get_lock_pool()
        if pool is None:
            return None
        try:
            conn = pool.getconn(timeout=5.0)
        except Exception:  # noqa: BLE001 — pool ocupado/caído: seguimos con el lock local
            logger.warning("Lock distribuido: sin conexión disponible; solo lock local")
            return None
        try:
            conn.execute("select pg_advisory_lock(%s)", (_advisory_key(thread_id),))
            return conn
        except Exception:  # noqa: BLE001
            logger.warning("Lock distribuido: fallo al adquirir; solo lock local", exc_info=True)
            with contextlib.suppress(Exception):
                pool.putconn(conn)
            return None

    def _release_advisory(self, conn, thread_id: str) -> None:
        if conn is None:
            return
        try:
            conn.execute("select pg_advisory_unlock(%s)", (_advisory_key(thread_id),))
        except Exception:  # noqa: BLE001
            logger.warning("Lock distribuido: fallo al liberar", exc_info=True)
        finally:
            pool = self._lock_pool
            if pool is not None:
                try:
                    pool.putconn(conn)
                except Exception:  # noqa: BLE001
                    with contextlib.suppress(Exception):
                        conn.close()

    @contextlib.contextmanager
    def _conversation_lock(self, thread_id: str):
        """Serializa un turno de la conversación: lock local (intra-proceso) SIEMPRE +
        advisory lock de Postgres (entre réplicas) si hay database_url. Cede exactamente
        una vez; ante cualquier fallo de la DB queda al menos el lock local."""
        with self._thread_lock(thread_id):
            conn = self._acquire_advisory(thread_id)
            try:
                yield
            finally:
                self._release_advisory(conn, thread_id)

    def process(self, inbound: InboundMessage) -> TurnResult:
        # t0 ANTES del lock: la espera por otro turno en curso también es latencia
        # que percibe el candidato (O-3).
        t0 = time.perf_counter()
        with self._conversation_lock(inbound.thread_id):
            return self._process(inbound, turn_started=t0)

    def _process(self, inbound: InboundMessage, turn_started: float | None = None) -> TurnResult:
        resolved = self._resolve_context(inbound)
        if isinstance(resolved, TurnResult):
            return resolved
        vacancy, candidate, conv = resolved

        # Contexto para el tracing LangSmith (no-op si el LLM no lo soporta): así los
        # runs dejan de ser invocaciones sueltas y se agrupan por conversación.
        set_ctx = getattr(self.runner.llm, "set_context", None)
        if callable(set_ctx):
            set_ctx(
                vacancy_id=vacancy.get("id"),
                candidate_id=candidate.get("id"),
                conversation_id=conv.get("id"),
            )

        state = self.runner.get_state(inbound.thread_id)
        first_contact = not state or not state.get("phase")

        if first_contact:
            questions = [_to_qspec(q) for q in repositories.get_vacancy_questions(vacancy["id"])]
            new_state = self.runner.start(
                inbound.thread_id,
                _vacancy_subset(vacancy),
                questions,
                cv_profile=candidate.get("cv_profile") or {},
            )
        else:
            if inbound.text:
                repositories.add_message(conv["id"], "user", inbound.text)
            elif inbound.button:
                label = "Acepto" if inbound.button == "accept" else "No interesado"
                repositories.add_message(conv["id"], "user", f"[{label}]")
            new_state = self.runner.send(
                inbound.thread_id, text=inbound.text, button=inbound.button, document=inbound.document
            )

        self._persist_save_document(candidate, conv, new_state)
        self._sync_business(vacancy, candidate, conv, new_state)
        # Si el candidato acaba de elegir horario, crea la reunión y agrega la confirmación.
        self._finalize_scheduling(vacancy, candidate, conv, new_state)
        self._persist_outbound(conv, new_state)
        self._record_usage(vacancy, candidate, conv, turn_started=turn_started)
        # El candidato acaba de interactuar: reinicia el reloj de inactividad.
        repositories.update_conversation(
            conv["id"], {"last_activity_at": _now_iso(), "reminders_sent": 0}
        )
        return self._result(new_state)

    def _resolve_context(
        self, inbound: InboundMessage
    ) -> tuple[dict, dict, dict] | TurnResult:
        """Resuelve (vacante, candidato, conversación) del mensaje entrante.

        Routing multi-tenant (auditoría A1), en orden:
          1. Conversación existente del thread → SU vacante/candidato (sticky: un mensaje
             a mitad de proceso nunca se cruza a otra vacante/tenant, ni vía deep-link).
          2. Deep-link `t.me/<bot>?start=<vacancy_id>` → esa vacante si existe y está
             abierta; si no, se avisa que la convocatoria no está disponible (sin crear
             candidato — engancharlo a otra vacante sería un cruce entre tenants).
          3. Sin payload → primera vacante abierta (retrocompatible, demo mono-vacante).
        """
        conv = repositories.get_conversation_by_thread(inbound.thread_id)
        if conv:
            candidate = repositories.get_candidate(conv["candidate_id"])
            vacancy = repositories.get_vacancy(conv["vacancy_id"])
            if candidate and vacancy:
                return vacancy, candidate, conv

        if inbound.start_payload:
            vacancy = self._vacancy_from_payload(inbound.start_payload)
            if not vacancy or vacancy.get("status") != "open":
                return TurnResult(messages=[VACANCY_UNAVAILABLE])
        else:
            vacancy = repositories.get_default_open_vacancy()
            if not vacancy:
                return TurnResult(messages=[NO_OPEN_VACANCY])

        candidate = repositories.get_or_create_candidate(
            vacancy["id"], inbound.channel, inbound.chat_id, inbound.display_name
        )
        conv = repositories.get_or_create_conversation(
            candidate["id"], vacancy["id"], inbound.thread_id
        )
        return vacancy, candidate, conv

    @staticmethod
    def _vacancy_from_payload(payload: str) -> Optional[dict]:
        """Vacante apuntada por el deep-link; None si el payload no es un UUID o no existe."""
        import uuid

        try:
            uuid.UUID(payload.strip())
        except (ValueError, AttributeError, TypeError):
            return None
        try:
            return repositories.get_vacancy(payload.strip())
        except Exception:  # noqa: BLE001 — un payload raro no debe tumbar el turno
            return None

    def finalize_inactive(self, thread_id: str) -> TurnResult:
        """Cierra una conversación colgada por inactividad (la dispara el scheduler).

        Avanza el motor con `timeout=True`, proyecta el estado a Supabase (status
        `no_response` si la entrevista quedó sin responder) y devuelve el mensaje de cierre."""
        with self._conversation_lock(thread_id):
            return self._finalize_inactive(thread_id)

    def _finalize_inactive(self, thread_id: str) -> TurnResult:
        conv = repositories.get_conversation_by_thread(thread_id)
        if not conv:
            return TurnResult()
        candidate = repositories.get_candidate(conv["candidate_id"]) or {}
        vacancy = repositories.get_vacancy(conv["vacancy_id"]) or {}
        new_state = self.runner.send(thread_id, timeout=True)
        self._sync_business(vacancy, candidate, conv, new_state)
        self._persist_outbound(conv, new_state)
        self._record_usage(vacancy, candidate, conv)
        return self._result(new_state)

    def _persist_save_document(self, candidate: dict, conv: dict, state: InterviewState) -> None:
        """Persiste el documento que el motor marcó este turno (CV/CUL): contenido DURABLE en
        Postgres (sobrevive redeploys) + metadata en candidates.documents. Si no se puede leer el
        archivo, degrada a solo-metadata (comportamiento previo)."""
        doc = state.get("save_document")
        if not doc:
            return
        doc_type = doc.get("type", "doc")
        filename = doc.get("filename", "")
        mime = doc.get("mime", "application/pdf")
        content_b64, size = _read_document_b64(doc.get("local_path", ""))
        # Umbral de contenido en DB (audit D2): un PDF grande vía PostgREST infla el JSON
        # ~35% en base64; sobre el umbral el archivo queda solo en disco (stored="disk").
        max_db = int(
            getattr(getattr(self, "settings", None), "document_db_max_bytes", 0)
            or 5 * 1024 * 1024
        )
        stored = "none"
        if content_b64 and size > max_db:
            content_b64 = ""
            stored = "disk"
        if content_b64:
            try:
                repositories.save_document_content(
                    candidate["id"], doc_type=doc_type, filename=filename,
                    content_b64=content_b64, mime=mime, size_bytes=size, conversation_id=conv["id"],
                )
                stored = "db"
            except Exception:  # noqa: BLE001 — si la DB falla, al menos queda el disco
                stored = "disk"
        repositories.add_candidate_document(
            candidate["id"],
            {
                "type": doc_type,
                "filename": filename,
                "file_id": doc.get("file_id", ""),
                "local_path": doc.get("local_path", ""),
                "mime": mime,
                "size_bytes": size,
                "stored": stored,
                "received_at": _now_iso(),
            },
        )
        repositories.add_message(conv["id"], "user", f"[Documento: {filename or 'archivo'}]")

    def initiate_contact(self, candidate: dict, vacancy: dict) -> TurnResult:
        """Inicia la conversación con un candidato (contacto saliente del reclutador).

        Crea/recupera el estado de entrevista sembrando su CV y devuelve el saludo + bandera
        de botones para que el canal lo envíe. No marca `invited` (eso lo hace el endpoint
        tras confirmar el envío)."""
        thread_id = f"{candidate['channel']}:{candidate['channel_user_id']}"
        with self._conversation_lock(thread_id):
            conv = repositories.get_or_create_conversation(candidate["id"], vacancy["id"], thread_id)
            questions = [_to_qspec(q) for q in repositories.get_vacancy_questions(vacancy["id"])]
            state = self.runner.start(
                thread_id, _vacancy_subset(vacancy), questions, cv_profile=candidate.get("cv_profile") or {}
            )
            self._sync_business(vacancy, candidate, conv, state)
            self._persist_outbound(conv, state)
            self._record_usage(vacancy, candidate, conv)
            # Arranca el reloj de inactividad desde el momento del contacto.
            repositories.update_conversation(
                conv["id"], {"last_activity_at": _now_iso(), "reminders_sent": 0}
            )
            return self._result(state)

    # ── Agendamiento de entrevista (fase 2) ──────────────────────────────────

    def _resolve_recruiter(self, vacancy: dict) -> dict[str, Any]:
        """Contacto RR.HH. de la vacante (coordina/firma los mensajes en todas las etapas)."""
        rid = vacancy.get("recruiter_id")
        if rid:
            return repositories.get_recruiter(rid) or {}
        return {}

    def _resolve_interviewer(self, vacancy: dict, stage: str) -> dict[str, Any]:
        """Persona entrevistada según la etapa: hr→RR.HH., lead→líder, manager→gerencia.

        Si la vacante no tiene asignado el líder/gerencia, cae al contacto RR.HH."""
        field_by_stage = {
            "hr": "recruiter_id",
            "lead": "lead_recruiter_id",
            "manager": "manager_recruiter_id",
        }
        rid = vacancy.get(field_by_stage.get(stage, "recruiter_id"))
        if rid:
            return repositories.get_recruiter(rid) or {}
        return self._resolve_recruiter(vacancy)

    def _scheduler(self):
        return self.scheduler or get_scheduler(self.settings)

    def initiate_scheduling(
        self, candidate: dict, vacancy: dict, *, stage: str = "hr", modality: str = "virtual"
    ) -> TurnResult:
        """Abre la coordinación de la entrevista de una etapa: consulta la disponibilidad del
        entrevistador (RR.HH./líder/gerencia) en su calendario, propone 2-3 horarios y proyecta
        el estado.

        `stage` = "hr" | "lead" | "manager"; `modality` = "virtual" | "onsite".
        La dispara el endpoint de decisión/avance de etapa. El saludo + opciones los envía el bot."""
        thread_id = f"{candidate['channel']}:{candidate['channel_user_id']}"
        with self._conversation_lock(thread_id):
            conv = repositories.get_or_create_conversation(candidate["id"], vacancy["id"], thread_id)
            cfg = repositories.get_app_setting("scheduling", {}, vacancy.get("tenant_id")) or {}
            recruiter = self._resolve_recruiter(vacancy)
            interviewer = self._resolve_interviewer(vacancy, stage)
            calendar_id = interviewer.get("calendar_id") or recruiter.get("calendar_id") or "primary"
            now = datetime.now(timezone.utc)
            horizon = int(cfg.get("horizon_days", 7) or 7)
            busy: list[tuple[datetime, datetime]] = []
            try:
                busy = self._scheduler().busy_intervals(calendar_id, now, now + timedelta(days=horizon + 1))
            except Exception:  # noqa: BLE001 — sin disponibilidad legible, proponemos igual
                busy = []
            slots = [s.isoformat() for s in compute_free_slots(busy, cfg, now=now)]
            state = self.runner.send(
                thread_id,
                start_scheduling=slots,
                recruiter=recruiter,
                stage=stage,
                modality=modality,
                interviewer=interviewer,
            )
            self._sync_business(vacancy, candidate, conv, state)
            self._persist_outbound(conv, state)
            repositories.update_conversation(
                conv["id"], {"last_activity_at": _now_iso(), "reminders_sent": 0}
            )
            return self._result(state)

    def _finalize_scheduling(self, vacancy: dict, candidate: dict, conv: dict, state: InterviewState) -> None:
        """Crea la reunión (Calendar + Sheets) tras la elección del candidato y notifica.

        Multi-etapa: idempotente por (conversación, etapa). Solo actúa si la fase es `scheduled`,
        hay horario elegido y aún no existe la reunión de esa etapa. Presencial (`onsite`): sin
        enlace Meet, con ubicación y confirmación distinta."""
        if state.get("phase") != PHASE_SCHEDULED or not state.get("meeting_slot"):
            return
        stage = state.get("scheduling_stage") or "hr"
        modality = state.get("modality") or "virtual"
        if repositories.get_meeting_by_conversation_stage(conv["id"], stage):
            return
        recruiter = self._resolve_recruiter(vacancy)
        interviewer = self._resolve_interviewer(vacancy, stage)
        try:
            start = datetime.fromisoformat(state["meeting_slot"])
        except Exception:  # noqa: BLE001
            return
        dur = int(vacancy.get("meeting_duration_minutes") or 45)
        end = start + timedelta(minutes=dur)
        cv = candidate.get("cv_profile") or {}
        candidate_email = str(cv.get("email", "")).strip()
        candidate_phone = str(cv.get("phone", "")).strip()
        recruiter_email = str(recruiter.get("email", "")).strip()
        recruiter_phone = str(recruiter.get("phone", "")).strip()
        recruiter_name = str(recruiter.get("name", "")).strip()
        interviewer_name = str(interviewer.get("name", "")).strip() or recruiter_name
        interviewer_email = str(interviewer.get("email", "")).strip()
        onsite = modality == "onsite"
        location = str(interviewer.get("location", "") or vacancy.get("location", "")).strip() if onsite else ""
        # Invitados: candidato + entrevistador (+ RR.HH. si es distinto).
        attendees = [e for e in (candidate_email, interviewer_email, recruiter_email) if e]
        stage_label = {"hr": "RR.HH.", "lead": "Líder del proyecto", "manager": "Gerencia"}.get(stage, "")
        summary = f"Entrevista {stage_label} — {vacancy.get('title', '')} — {candidate.get('name', '')}".strip()
        description = (
            f"Entrevista de selección ({stage_label}) para {vacancy.get('title', '')}.\n\n"
            f"Candidato: {candidate.get('name', '')}\n"
            f"  • Correo: {candidate_email or '—'}\n"
            f"  • Teléfono: {candidate_phone or '—'}\n\n"
            f"Entrevistador: {interviewer_name or '—'} ({interviewer.get('role', '') or '—'})\n"
            f"  • Correo: {interviewer_email or '—'}\n\n"
            f"Contacto RR.HH.: {recruiter_name or '—'}\n"
            f"  • Correo: {recruiter_email or '—'}\n"
            f"  • Teléfono: {recruiter_phone or '—'}"
            + (f"\n\nModalidad: presencial\nLugar: {location}" if onsite else "\n\nModalidad: virtual")
        )
        # Registro-PRIMERO (auditoría G2): la intención queda en la DB ANTES de crear el
        # evento externo. Si el proceso muere a mitad, el guard de idempotencia de arriba ve
        # la fila y el reintento NO duplica el evento de Calendar; la reconciliación alerta
        # la reunión sin enlace. Tras crear el evento se completan link/event_id/sheet_row.
        meeting = repositories.save_meeting({
            "conversation_id": conv["id"],
            "candidate_id": candidate["id"],
            "vacancy_id": vacancy["id"],
            "stage": stage,
            "modality": modality,
            "location": location,
            "scheduled_at": start.isoformat(),
            "end_at": end.isoformat(),
            "meet_link": "",
            "event_id": "",
            "sheet_row": "",
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "recruiter_email": recruiter_email,
            "recruiter_phone": recruiter_phone,
            "recruiter_name": recruiter_name,
            "status": "scheduled",
        })
        backend = self._scheduler()
        try:
            result = backend.create_meeting(
                calendar_id=interviewer.get("calendar_id") or recruiter.get("calendar_id") or "primary",
                summary=summary, start=start, end=end, attendees=attendees, description=description,
                modality=modality, location=location,
            )
        except Exception:  # noqa: BLE001 — el horario ya quedó registrado aunque falle el evento
            result = MeetingResult(start=start, end=end)
        sheet_row = ""
        sheet_id = getattr(self.settings, "meeting_sheet_id", "") if self.settings else ""
        if sheet_id:
            try:
                sheet_row = backend.append_sheet_row(
                    sheet_id,
                    getattr(self.settings, "meeting_sheet_tab", "Reuniones"),
                    [human_slot_long(start), stage_label, vacancy.get("title", ""), candidate.get("name", ""),
                     candidate_email, candidate_phone, interviewer_name, interviewer_email,
                     recruiter_name, recruiter_email, recruiter_phone,
                     "presencial" if onsite else "virtual", result.meet_link or location],
                )
            except Exception:  # noqa: BLE001
                sheet_row = ""
        if result.meet_link or result.event_id or sheet_row:
            try:
                meeting = repositories.update_meeting(
                    meeting["id"],
                    {"meet_link": result.meet_link, "event_id": result.event_id, "sheet_row": sheet_row},
                )
            except Exception:  # noqa: BLE001 — la reunión ya existe; el link va igual al candidato
                meeting = {**meeting, "meet_link": result.meet_link, "event_id": result.event_id}
        # Confirmación al candidato (la envía el bot junto con el resto del turno).
        first = str(candidate.get("name", "")).split(" ")[0]
        if onsite:
            state["outbound"].append(
                SCHEDULING_CONFIRMED_ONSITE.format(
                    name=first or "",
                    date=human_slot_long(start),
                    location=location or "te confirmamos la dirección en breve",
                    interviewer=interviewer_name or "nuestro equipo",
                    contact=recruiter_name or "el equipo de Talento",
                )
            )
        else:
            state["outbound"].append(
                SCHEDULING_CONFIRMED.format(
                    name=first or "",
                    date=human_slot_long(start),
                    link=result.meet_link or "te lo compartimos en breve",
                )
            )
        if self.notify_meeting:
            try:
                self.notify_meeting(vacancy, candidate, meeting, recruiter)
            except Exception:  # noqa: BLE001 — no romper la conversación por la notificación
                pass

    def _record_usage(
        self, vacancy: dict, candidate: dict, conv: dict, turn_started: float | None = None
    ) -> None:
        """Vuelca el uso de tokens acumulado este turno (si el LLM está instrumentado)."""
        from agente.prompts import PROMPT_VERSION

        model = getattr(self.runner.llm, "model", "") or ""
        # Latencia end-to-end del turno del candidato (O-3): fila sintética stage="turn"
        # sin tokens; no depende de que el LLM esté instrumentado.
        if turn_started is not None:
            # min 1 ms: el guard de record_usage descarta filas sin señal (todo en 0).
            turn_ms = max(1, int((time.perf_counter() - turn_started) * 1000))
            repositories.record_usage(
                repositories.TURN_STAGE, model, {"calls": 1, "duration_ms": turn_ms},
                vacancy_id=vacancy.get("id"),
                candidate_id=candidate.get("id"),
                conversation_id=conv.get("id"),
                prompt_version=PROMPT_VERSION,
            )
        drain = getattr(self.runner.llm, "drain", None)
        if not callable(drain):
            return
        # Routing de costos (paso 5): el modelo puede diferir por etapa; se registra el real.
        drain_models = getattr(self.runner.llm, "drain_models", None)
        models = drain_models() if callable(drain_models) else {}
        for stage, tokens in (drain() or {}).items():
            repositories.record_usage(
                stage, models.get(stage, model), tokens,
                vacancy_id=vacancy.get("id"),
                candidate_id=candidate.get("id"),
                conversation_id=conv.get("id"),
                prompt_version=PROMPT_VERSION,
            )
        # Trazas con contenido (O-1): prompt/respuesta por llamada, si el metering las capturó.
        drain_traces = getattr(self.runner.llm, "drain_traces", None)
        if callable(drain_traces):
            repositories.record_traces(
                drain_traces() or [],
                vacancy_id=vacancy.get("id"),
                candidate_id=candidate.get("id"),
                conversation_id=conv.get("id"),
                model=model,
                prompt_version=PROMPT_VERSION,
            )

    # ── Proyección a Supabase ────────────────────────────────────────────────

    def _sync_business(self, vacancy: dict, candidate: dict, conv: dict, state: InterviewState) -> None:
        phase = state.get("phase")

        # Candidato: consentimiento / estado.
        cand_update: dict[str, Any] = {}
        if state.get("consented") is True:
            cand_update["consent"] = True
            # Registra el momento del consentimiento una sola vez (Ley 29733 / retención).
            if not candidate.get("consent_at"):
                cand_update["consent_at"] = _now_iso()
        if phase == PHASE_CLOSED:
            # "no_response" = se cerró por inactividad; distinto de "declined" (rechazó explícito).
            closed_status = "no_response" if state.get("closed_reason") == "no_response" else "declined"
            cand_update.update(consent=False, status=closed_status)
        elif phase == PHASE_INTERVIEWING:
            cand_update["status"] = "interviewing"
        elif phase in (PHASE_AWAITING_DOCS, PHASE_FINISHED):
            # La entrevista ya terminó (scorecard generado); en awaiting_docs solo faltan documentos.
            cand_update["status"] = "finished"
        elif phase in (PHASE_SCHEDULING, PHASE_SCHEDULED):
            # Multi-etapa: el status distingue la etapa (hr/lead/manager) + si está agendada.
            stage = state.get("scheduling_stage") or "hr"
            prefix = {"hr": "", "lead": "lead_", "manager": "mgr_"}.get(stage, "")
            suffix = "scheduled" if phase == PHASE_SCHEDULED else "scheduling"
            cand_update["status"] = f"{prefix}{suffix}"
        if cand_update:
            repositories.update_candidate(candidate["id"], cand_update)

        # Conversación: estado + índice de pregunta. Si la fase cambió, queda registrada
        # la transición con timestamp (audit G4: tiempo-por-estado, reconstrucción del flujo).
        new_conv_state = phase or "greeting"
        if new_conv_state != (conv.get("state") or ""):
            repositories.add_state_transition(conv["id"], conv.get("state") or "", new_conv_state)
        repositories.update_conversation(
            conv["id"],
            {"state": new_conv_state, "current_question_idx": state.get("current_idx", 0)},
        )

        # Respuestas evaluadas (idempotente).
        for ans in state.get("answers") or []:
            repositories.upsert_answer(
                conv["id"],
                ans["question_id"],
                ans.get("raw_answer", ""),
                ans.get("score"),
                ans.get("justification", ""),
                ans.get("follow_up_count", 0),
            )

        # Scorecard final → guardar y notificar al reclutador (una sola vez, apenas exista;
        # no espera a la etapa de documentos, para que el reclutador lo vea al terminar la entrevista).
        scorecard = state.get("scorecard")
        if scorecard and not repositories.get_scorecard(conv["id"]):
            repositories.save_scorecard(conv["id"], scorecard)
            if self.notify_recruiter:
                try:
                    self.notify_recruiter(vacancy, candidate, conv, scorecard)
                except Exception:  # noqa: BLE001 — no romper la conversación por el email
                    pass

    def _persist_outbound(self, conv: dict, state: InterviewState) -> None:
        for msg in state.get("outbound") or []:
            repositories.add_message(conv["id"], "assistant", msg)

    @staticmethod
    def _result(state: InterviewState) -> TurnResult:
        return TurnResult(
            messages=list(state.get("outbound") or []),
            show_consent_buttons=bool(state.get("show_consent_buttons")),
            finished=state.get("phase") == PHASE_FINISHED,
            declined=state.get("phase") == PHASE_CLOSED,
        )
