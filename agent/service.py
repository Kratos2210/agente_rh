"""Servicio de entrevista agnóstico al canal (núcleo síncrono).

Coordina el motor durable (InterviewRunner) con la persistencia de negocio
(Supabase) y la notificación al reclutador. Lo invocan los adapters de canal
(Telegram hoy, WhatsApp después) en un hilo worker, porque tanto el grafo como
supabase-py son síncronos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from agent.graph import InterviewRunner
from agent.prompts import SCHEDULING_CONFIRMED
from agent.state import (
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
    ):
        self.runner = runner
        self.notify_recruiter = notify_recruiter
        self.notify_meeting = notify_meeting
        self.scheduler = scheduler
        self.settings = settings

    def process(self, inbound: InboundMessage) -> TurnResult:
        vacancy = repositories.get_default_open_vacancy()
        if not vacancy:
            return TurnResult(messages=["En este momento no hay vacantes activas. ¡Gracias por tu interés!"])

        candidate = repositories.get_or_create_candidate(
            vacancy["id"], inbound.channel, inbound.chat_id, inbound.display_name
        )
        conv = repositories.get_or_create_conversation(
            candidate["id"], vacancy["id"], inbound.thread_id
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
        self._record_usage(vacancy, candidate, conv)
        # El candidato acaba de interactuar: reinicia el reloj de inactividad.
        repositories.update_conversation(
            conv["id"], {"last_activity_at": _now_iso(), "reminders_sent": 0}
        )
        return self._result(new_state)

    def finalize_inactive(self, thread_id: str) -> TurnResult:
        """Cierra una conversación colgada por inactividad (la dispara el scheduler).

        Avanza el motor con `timeout=True`, proyecta el estado a Supabase (status
        `no_response` si la entrevista quedó sin responder) y devuelve el mensaje de cierre."""
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
        stored = "none"
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
        rid = vacancy.get("recruiter_id")
        if rid:
            return repositories.get_recruiter(rid) or {}
        return {}

    def _scheduler(self):
        return self.scheduler or get_scheduler(self.settings)

    def initiate_scheduling(self, candidate: dict, vacancy: dict) -> TurnResult:
        """Abre la coordinación de la entrevista: consulta la disponibilidad del reclutador
        en su calendario, propone 2-3 horarios al candidato y proyecta el estado.

        La dispara el endpoint de decisión (advance). El saludo + opciones se envían por el bot."""
        thread_id = f"{candidate['channel']}:{candidate['channel_user_id']}"
        conv = repositories.get_or_create_conversation(candidate["id"], vacancy["id"], thread_id)
        cfg = repositories.get_app_setting("scheduling", {}, vacancy.get("tenant_id")) or {}
        recruiter = self._resolve_recruiter(vacancy)
        calendar_id = recruiter.get("calendar_id") or "primary"
        now = datetime.now(timezone.utc)
        horizon = int(cfg.get("horizon_days", 7) or 7)
        busy: list[tuple[datetime, datetime]] = []
        try:
            busy = self._scheduler().busy_intervals(calendar_id, now, now + timedelta(days=horizon + 1))
        except Exception:  # noqa: BLE001 — sin disponibilidad legible, proponemos igual
            busy = []
        slots = [s.isoformat() for s in compute_free_slots(busy, cfg, now=now)]
        state = self.runner.send(thread_id, start_scheduling=slots, recruiter=recruiter)
        self._sync_business(vacancy, candidate, conv, state)
        self._persist_outbound(conv, state)
        repositories.update_conversation(
            conv["id"], {"last_activity_at": _now_iso(), "reminders_sent": 0}
        )
        return self._result(state)

    def _finalize_scheduling(self, vacancy: dict, candidate: dict, conv: dict, state: InterviewState) -> None:
        """Crea la reunión (Calendar + Sheets) tras la elección del candidato y notifica.

        Idempotente: solo si la fase es `scheduled`, hay horario elegido y aún no hay reunión."""
        if state.get("phase") != PHASE_SCHEDULED or not state.get("meeting_slot"):
            return
        if repositories.get_meeting_by_conversation(conv["id"]):
            return
        recruiter = self._resolve_recruiter(vacancy)
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
        attendees = [e for e in (candidate_email, recruiter_email) if e]
        summary = f"Entrevista — {vacancy.get('title', '')} — {candidate.get('name', '')}".strip()
        description = (
            f"Entrevista de selección para {vacancy.get('title', '')}.\n\n"
            f"Candidato: {candidate.get('name', '')}\n"
            f"  • Correo: {candidate_email or '—'}\n"
            f"  • Teléfono: {candidate_phone or '—'}\n\n"
            f"Reclutador: {recruiter_name or '—'}\n"
            f"  • Correo: {recruiter_email or '—'}\n"
            f"  • Teléfono: {recruiter_phone or '—'}"
        )
        backend = self._scheduler()
        try:
            result = backend.create_meeting(
                calendar_id=recruiter.get("calendar_id") or "primary",
                summary=summary, start=start, end=end, attendees=attendees, description=description,
            )
        except Exception:  # noqa: BLE001 — registra el horario aunque falle la creación del evento
            result = MeetingResult(start=start, end=end)
        sheet_row = ""
        sheet_id = getattr(self.settings, "meeting_sheet_id", "") if self.settings else ""
        if sheet_id:
            try:
                sheet_row = backend.append_sheet_row(
                    sheet_id,
                    getattr(self.settings, "meeting_sheet_tab", "Reuniones"),
                    [human_slot_long(start), vacancy.get("title", ""), candidate.get("name", ""),
                     candidate_email, candidate_phone, recruiter_name, recruiter_email,
                     recruiter_phone, result.meet_link],
                )
            except Exception:  # noqa: BLE001
                sheet_row = ""
        meeting = {
            "conversation_id": conv["id"],
            "candidate_id": candidate["id"],
            "vacancy_id": vacancy["id"],
            "scheduled_at": start.isoformat(),
            "end_at": end.isoformat(),
            "meet_link": result.meet_link,
            "event_id": result.event_id,
            "sheet_row": sheet_row,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "recruiter_email": recruiter_email,
            "recruiter_phone": recruiter_phone,
            "recruiter_name": recruiter_name,
            "status": "scheduled",
        }
        repositories.save_meeting(meeting)
        # Confirmación al candidato (la envía el bot junto con el resto del turno).
        first = str(candidate.get("name", "")).split(" ")[0]
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

    def _record_usage(self, vacancy: dict, candidate: dict, conv: dict) -> None:
        """Vuelca el uso de tokens acumulado este turno (si el LLM está instrumentado)."""
        drain = getattr(self.runner.llm, "drain", None)
        if not callable(drain):
            return
        model = getattr(self.runner.llm, "model", "")
        for stage, tokens in (drain() or {}).items():
            repositories.record_usage(
                stage, model, tokens,
                vacancy_id=vacancy.get("id"),
                candidate_id=candidate.get("id"),
                conversation_id=conv.get("id"),
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
        elif phase == PHASE_SCHEDULING:
            cand_update["status"] = "scheduling"
        elif phase == PHASE_SCHEDULED:
            cand_update["status"] = "scheduled"
        if cand_update:
            repositories.update_candidate(candidate["id"], cand_update)

        # Conversación: estado + índice de pregunta.
        repositories.update_conversation(
            conv["id"],
            {"state": phase or "greeting", "current_question_idx": state.get("current_idx", 0)},
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
