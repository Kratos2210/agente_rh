"""Endpoints de candidatos: pipeline global, detalle, documentos, contacto, decisión,
reuniones, exámenes, asistencia, avance de etapa y derecho al olvido."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, field_validator

from api.auth import get_current_user, require_role
from api.deps import (
    _audit,
    _candidate_row_from_embed,
    _page_params,
    _require_candidate_in_tenant,
    _with_cost,
)
from api.runtime import _DEFAULT_MEDICAL, _DEFAULT_SCHEDULING, _now_iso, _state, current_settings
from api.scheduler import _bot_send, _contact_candidate, _deliver_onboarding_kit
from core.logging_config import get_logger
from db import repositories as repo
from notifications import outbox
from notifications.candidate import DECISION_ADVANCE, DECISION_HIRED, DECISION_REJECT

logger = get_logger(__name__)
router = APIRouter()

# Raíz donde el bot guarda los documentos descargados de Telegram (CV/CUL).
_UPLOADS_ROOT = Path("uploads").resolve()


class DecisionIn(BaseModel):
    decision: str  # "advance" | "reject"


class PsychExamIn(BaseModel):
    """Credenciales del examen psicológico que RR.HH. obtiene de la plataforma externa."""
    link: str
    code: str = ""
    key: str = ""


class MedicalExamIn(BaseModel):
    """Cita del examen médico ocupacional que RR.HH. programa (auditoría v3, Parte B)."""
    clinic: str
    scheduled_at: str  # fecha+hora legible para el candidato (la escribe RR.HH.)
    address: str = ""
    instructions: str = ""

    @field_validator("clinic", "scheduled_at")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("clinic y scheduled_at son obligatorios")
        return v.strip()


class MedicalResultIn(BaseModel):
    """Resultado del examen médico: apto contrata, no_apto rechaza. Dato sensible de salud."""
    result: str  # "apto" | "no_apto"
    notes: str = ""

    @field_validator("result")
    @classmethod
    def _result_valid(cls, v: str) -> str:
        if v not in ("apto", "no_apto"):
            raise ValueError("result debe ser 'apto' o 'no_apto'")
        return v


class StartDateIn(BaseModel):
    """Fecha del primer día de trabajo (dispara el envío automático del kit de onboarding)."""
    start_date: str  # ISO date (YYYY-MM-DD)

    @field_validator("start_date")
    @classmethod
    def _date_valid(cls, v: str) -> str:
        from datetime import date

        try:
            date.fromisoformat(v.strip())
        except ValueError as e:
            raise ValueError("start_date debe ser una fecha ISO (YYYY-MM-DD)") from e
        return v.strip()


class AttendanceIn(BaseModel):
    stage: str  # "hr" | "lead" | "manager"
    attended: str  # "attended" | "no_show"
    reschedule: bool = False  # si no asistió: reabrir horarios (True) o cerrar (False)

    @field_validator("stage")
    @classmethod
    def _stage_valid(cls, v: str) -> str:
        if v not in ("hr", "lead", "manager"):
            raise ValueError("stage debe ser 'hr', 'lead' o 'manager'")
        return v

    @field_validator("attended")
    @classmethod
    def _attended_valid(cls, v: str) -> str:
        if v not in ("attended", "no_show"):
            raise ValueError("attended debe ser 'attended' o 'no_show'")
        return v


class AdvanceStageIn(BaseModel):
    """Feedback + decisión de una etapa. Al aprobar 'hr'/'lead' se agenda la etapa siguiente."""
    stage: str  # "hr" | "lead" | "manager"
    decision: str  # "approved" | "rejected"
    feedback: str = ""
    modality: str = "onsite"  # modalidad de la SIGUIENTE etapa (lead: elegible; manager: forzado onsite)

    @field_validator("stage")
    @classmethod
    def _stage_valid(cls, v: str) -> str:
        if v not in ("hr", "lead", "manager"):
            raise ValueError("stage debe ser 'hr', 'lead' o 'manager'")
        return v

    @field_validator("decision")
    @classmethod
    def _decision_valid(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("decision debe ser 'approved' o 'rejected'")
        return v

    @field_validator("modality")
    @classmethod
    def _modality_valid(cls, v: str) -> str:
        if v not in ("virtual", "onsite"):
            raise ValueError("modality debe ser 'virtual' o 'onsite'")
        return v


@router.get("/api/candidates")
def list_all_candidates(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Candidatos de las vacantes abiertas del tenant (Pipeline global), paginado.

    D1: 2 consultas fijas (vacantes + candidatos con embeds). U1: `q` busca por
    nombre; `limit/offset` paginan; `total` permite armar los controles en la UI."""
    titles = {v["id"]: v["title"] for v in repo.list_vacancies(status="open", tenant_id=user["tenant_id"])}
    limit, offset = _page_params(limit, offset)
    rows, total = repo.list_candidate_rows(
        list(titles), search=q.strip(), limit=limit, offset=offset
    )
    items = [
        {**_candidate_row_from_embed(c), "vacancy_title": titles.get(c.get("vacancy_id"), "")}
        for c in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/metrics")
def global_metrics_endpoint(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return _with_cost(repo.global_metrics(tenant_id=user["tenant_id"]), user["tenant_id"])


# Campos internos del candidato que NO deben salir en las respuestas de la API (P1: PII/IDs
# de canal). El dashboard no los usa; `cv_profile`/`documents` sí se conservan (se muestran).
_CANDIDATE_PRIVATE_FIELDS = ("channel_user_id",)


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Copia del candidato sin los identificadores internos de canal (Telegram chat id)."""
    return {k: v for k, v in candidate.items() if k not in _CANDIDATE_PRIVATE_FIELDS}


def _close_conversation_on_reject(candidate_id: str, reason: str = "rejected") -> None:
    """Cierra la conversación del candidato (estado terminal) al rechazarlo, para que los barridos
    de inactividad dejen de recordarle/pedir documentos: el proceso ya terminó para él. Best-effort:
    no tumba la decisión si falla ni si no hay conversación."""
    from agente.state import PHASE_CLOSED

    try:
        conv = repo.get_conversation_by_candidate(candidate_id)
        if conv and conv.get("state") != PHASE_CLOSED:
            repo.update_conversation(conv["id"], {"state": PHASE_CLOSED, "closed_reason": reason})
    except Exception:  # noqa: BLE001 — cerrar la conversación no debe tumbar el rechazo
        logger.exception("No se pudo cerrar la conversación del candidato %s tras el rechazo", candidate_id)


def _psych_exam_for_role(exam: dict[str, Any] | None, role: str) -> dict[str, Any] | None:
    """Credenciales del examen psicológico según el rol (auditoría S3).

    El link+código+clave son credenciales de acceso a una plataforma externa: solo
    recruiter+ las ve completas; un viewer ve que el examen fue enviado (cuándo y por
    quién) pero con las credenciales enmascaradas."""
    from api.auth import role_allows

    if not exam or role_allows(role, "recruiter"):
        return exam
    return {**exam, **{k: "•••" for k in ("link", "code", "key") if exam.get(k)}}


def _medical_exam_for_role(exam: dict[str, Any] | None, role: str) -> dict[str, Any] | None:
    """Examen médico según el rol (patrón S3). El RESULTADO es dato sensible de salud
    (Ley 29733): un viewer ve la cita (clínica/fecha) pero no el resultado ni sus notas."""
    from api.auth import role_allows

    if not exam or role_allows(role, "recruiter"):
        return exam
    return {**exam, **{k: "•••" for k in ("result", "result_notes") if exam.get(k)}}


@router.get("/api/candidates/{candidate_id}")
def get_candidate_detail(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    conv = repo.get_conversation_by_candidate(candidate_id)
    scorecard = repo.get_scorecard(conv["id"]) if conv else None
    messages = repo.get_messages(conv["id"]) if conv else []

    # Backfill de etiquetas en scorecards ya guardados (per_criterion en orden de posición).
    if scorecard and scorecard.get("per_criterion"):
        questions = repo.get_vacancy_questions(candidate["vacancy_id"])
        labels = [q.get("label") or "" for q in questions]
        for i, pc in enumerate(scorecard["per_criterion"]):
            if not pc.get("label") and i < len(labels):
                pc["label"] = labels[i]

    # S3: las credenciales del examen psicológico se enmascaran para viewer (van
    # tanto en el objeto candidato como en la clave dedicada `psych_exam`). El resultado
    # del examen médico (dato de salud) se enmascara igual.
    psych_exam = _psych_exam_for_role(candidate.get("psych_exam"), user.get("role", ""))
    medical_exam = _medical_exam_for_role(candidate.get("medical_exam"), user.get("role", ""))
    public = _public_candidate(candidate)
    if "psych_exam" in public:
        public["psych_exam"] = psych_exam
    if "medical_exam" in public:
        public["medical_exam"] = medical_exam
    return {
        "candidate": public,
        "vacancy": {"id": vacancy["id"], "title": vacancy["title"]} if vacancy else None,
        "thresholds": (vacancy or {}).get("semaphore_thresholds") or {"green_min": 75, "yellow_min": 50},
        "scorecard": scorecard,
        "transcript": messages,
        "meetings": repo.list_meetings_by_candidate(candidate_id),
        "stage_feedback": repo.list_stage_feedback(candidate_id),
        "psych_exam": psych_exam,
        "medical_exam": medical_exam,
        "start_date": candidate.get("start_date"),
        "onboarding": candidate.get("onboarding"),
        # G4: transiciones de fase con timestamp (tiempo-por-estado del proceso).
        "transitions": repo.list_state_transitions(conv["id"]) if conv else [],
    }


def _resolve_document_path(doc: dict[str, Any]) -> Path | None:
    """Resuelve la ruta en disco de un documento, segura dentro de uploads/.

    Usa local_path; si falta o no existe (docs previos), busca el filename bajo uploads/**.
    Devuelve None si no hay un archivo válido y contenido dentro de la carpeta uploads."""
    candidates: list[Path] = []
    if doc.get("local_path"):
        candidates.append(Path(doc["local_path"]))
    filename = doc.get("filename") or ""
    if filename:
        candidates.extend(_UPLOADS_ROOT.glob(f"**/{filename}"))
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        # Anti path-traversal: debe quedar dentro de uploads/ y existir.
        if rp.is_file() and _UPLOADS_ROOT in rp.parents:
            return rp
    return None


@router.get("/api/candidates/{candidate_id}/documents/{doc_type}")
def download_candidate_document(
    candidate_id: str, doc_type: str, user: dict[str, Any] = Depends(get_current_user)
):
    """Sirve el PDF de un documento recibido (cv | cul). Fuente durable: Postgres; fallback: disco."""
    candidate, _ = _require_candidate_in_tenant(candidate_id, user)
    # 1) Contenido durable en la DB (sobrevive redeploys) — fuente de verdad.
    row = repo.get_document_content(candidate_id, doc_type)
    if row and row.get("content_b64"):
        import base64

        try:
            data = base64.b64decode(row["content_b64"])
        except Exception:  # noqa: BLE001
            data = b""
        if data:
            fname = row.get("filename") or f"{doc_type}.pdf"
            return Response(
                content=data,
                media_type=row.get("mime") or "application/pdf",
                headers={"Content-Disposition": f'inline; filename="{fname}"'},
            )
    # 2) Fallback: archivo en disco (documentos previos al almacenamiento durable).
    doc = next((d for d in (candidate.get("documents") or []) if d.get("type") == doc_type), None)
    if not doc:
        raise HTTPException(404, "Documento no encontrado")
    path = _resolve_document_path(doc)
    if not path:
        raise HTTPException(404, "El archivo no está disponible en el servidor")
    return FileResponse(
        path, media_type="application/pdf", filename=doc.get("filename") or path.name
    )


@router.post("/api/candidates/{candidate_id}/contact")
def contact_candidate(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    """Disparador manual del primer contacto. Idempotente: solo desde `prescreen_passed`."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if candidate["status"] != "prescreen_passed":
        raise HTTPException(409, "El candidato ya fue contactado o no está apto para contactar.")
    # Contacto manual de RR.HH.: acción humana explícita, válida a cualquier hora (force).
    result = _contact_candidate(candidate, vacancy, current_settings(), force=True)
    _audit(user, "candidate.contact", entity_type="candidate", entity_id=candidate_id, summary=candidate.get("name", ""))
    # No exponer el chat_id crudo de Telegram en la respuesta HTTP (P1). Queda en logs.
    return {k: v for k, v in result.items() if k != "chat_id"}


@router.post("/api/candidates/{candidate_id}/decision")
def decide_candidate(
    candidate_id: str,
    payload: DecisionIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if payload.decision not in (DECISION_ADVANCE, DECISION_REJECT):
        raise HTTPException(400, "decision debe ser 'advance' o 'reject'")
    settings = current_settings()

    if payload.decision == DECISION_REJECT:
        repo.update_candidate(candidate_id, {"status": "rejected"})
        _close_conversation_on_reject(candidate_id)
        notified = outbox.deliver_candidate_notify(
            settings, candidate, payload.decision, tenant_id=user["tenant_id"]
        )
        _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
               summary=f"rechazar · {candidate.get('name', '')}")
        return {"status": "rejected", "notified": notified}

    # advance: si el agendamiento está activo, abre la coordinación del horario (en vez del
    # aviso genérico); si no, conserva el comportamiento anterior (notifica "avanza").
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"]) or {}
    service = _state.get("service")
    if sched.get("enabled") and service:
        vacancy = repo.get_vacancy(candidate["vacancy_id"])
        if not vacancy:
            raise HTTPException(404, "Vacante no encontrada")
        result = service.initiate_scheduling(candidate, vacancy)
        chat = str(candidate.get("channel_user_id") or "")
        sent = _bot_send(int(chat), result.messages) if chat.lstrip("-").isdigit() else False
        _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
               summary=f"avanzar → agendar · {candidate.get('name', '')}")
        return {
            "status": "scheduling",
            "scheduling_started": True,
            "messages_sent": sent,
            "messages": result.messages,
        }

    repo.update_candidate(candidate_id, {"status": "advanced"})
    notified = outbox.deliver_candidate_notify(
        settings, candidate, payload.decision, tenant_id=user["tenant_id"]
    )
    _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
           summary=f"avanzar · {candidate.get('name', '')}")
    return {"status": "advanced", "notified": notified}


@router.get("/api/candidates/{candidate_id}/meeting")
def get_candidate_meeting(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any] | None:
    """Reunión más reciente del candidato (o null si aún no hay)."""
    _require_candidate_in_tenant(candidate_id, user)
    return repo.get_meeting_by_candidate(candidate_id)


@router.get("/api/candidates/{candidate_id}/meetings")
def list_candidate_meetings(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """Reuniones del candidato, una por etapa (hr / lead / manager)."""
    _require_candidate_in_tenant(candidate_id, user)
    return repo.list_meetings_by_candidate(candidate_id)


def _send_scheduling_messages(candidate: dict[str, Any], messages: list[str]) -> bool:
    """Envía por el bot vivo los mensajes de coordinación (propuesta de horarios)."""
    chat = str(candidate.get("channel_user_id") or "")
    return _bot_send(int(chat), messages) if chat.lstrip("-").isdigit() else False


# Modalidad forzada por etapa siguiente (gerencia es 100% presencial).
_NEXT_STAGE = {"hr": "lead", "lead": "manager"}


@router.post("/api/candidates/{candidate_id}/psych-exam")
def send_psych_exam(
    candidate_id: str,
    payload: PsychExamIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Fase 1: envía por correo el examen psicológico (link+código+clave) y lo registra."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    # R3 (auditoría): idempotencia — reenviar las MISMAS credenciales duplica el correo
    # (doble click). Con credenciales nuevas sí se permite (reemplazo legítimo).
    prev = candidate.get("psych_exam") or {}
    if prev and (prev.get("link"), prev.get("code"), prev.get("key")) == (
        payload.link, payload.code, payload.key
    ):
        raise HTTPException(409, "Ese examen ya fue enviado a este candidato.")
    settings = current_settings()
    exam = {
        "link": payload.link,
        "code": payload.code,
        "key": payload.key,
        "sent_at": _now_iso(),
        "sent_by": user.get("email") or "",
    }
    sent = outbox.deliver_psych_exam(settings, vacancy, candidate, exam, conversation_id=None)
    repo.update_candidate(candidate_id, {"psych_exam": exam})
    _audit(user, "candidate.psych_exam", entity_type="candidate", entity_id=candidate_id,
           summary=f"examen psicológico enviado · {candidate.get('name', '')}")
    return {"sent": sent, "psych_exam": exam}


# ── Examen médico pre-contratación (auditoría v3, Parte B) ─────────────────────────

# Estados desde los que RR.HH. puede programar (o reprogramar) la cita médica.
_MEDICAL_SCHEDULABLE = ("medical_pending", "medical_scheduled")


@router.post("/api/candidates/{candidate_id}/medical-exam")
def send_medical_exam(
    candidate_id: str,
    payload: MedicalExamIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Programa la cita del examen médico (fecha + clínica) y la notifica por correo + Telegram."""
    from agente.prompts import NOTIFY_MEDICAL_EXAM

    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if candidate.get("status") not in _MEDICAL_SCHEDULABLE:
        raise HTTPException(409, "El candidato no está en la fase de examen médico.")
    prev = candidate.get("medical_exam") or {}
    # Idempotencia (doble click): la MISMA cita no se reenvía; una cita distinta sí (reprogramar).
    if prev and (prev.get("clinic"), prev.get("address"), prev.get("scheduled_at"), prev.get("instructions")) == (
        payload.clinic, payload.address, payload.scheduled_at, payload.instructions
    ):
        raise HTTPException(409, "Esa cita ya fue enviada a este candidato.")
    settings = current_settings()
    exam = {
        "clinic": payload.clinic,
        "address": payload.address,
        "scheduled_at": payload.scheduled_at,
        "instructions": payload.instructions,
        "sent_at": _now_iso(),
        "sent_by": user.get("email") or "",
    }
    email_sent = outbox.deliver_medical_exam(settings, vacancy, candidate, exam)
    text = NOTIFY_MEDICAL_EXAM.format(
        name=candidate.get("name") or "",
        clinic=exam["clinic"],
        address=exam["address"] or "—",
        scheduled_at=exam["scheduled_at"],
        instructions=(f"ℹ️ Indicaciones: {exam['instructions']}\n" if exam["instructions"] else ""),
    )
    telegram_sent = outbox.deliver_candidate_text(
        settings, candidate, text, tenant_id=user["tenant_id"]
    )
    repo.update_candidate(candidate_id, {"medical_exam": exam, "status": "medical_scheduled"})
    # El summary NO lleva datos clínicos (la cita/resultado son datos de salud — Ley 29733).
    _audit(user, "candidate.medical_exam", entity_type="candidate", entity_id=candidate_id,
           summary=f"cita de examen médico enviada · {candidate.get('name', '')}")
    return {"email_sent": email_sent, "telegram_sent": telegram_sent, "medical_exam": exam,
            "status": "medical_scheduled"}


@router.post("/api/candidates/{candidate_id}/medical-result")
def record_medical_result(
    candidate_id: str,
    payload: MedicalResultIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Registra el resultado del examen médico: apto contrata (fin del proceso), no_apto rechaza.

    El resultado es INMUTABLE: la transición (hired/rejected) ya notificó al candidato;
    re-registrar duplicaría notificaciones. Corregir un error = borrado/erasure, no re-emitir."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    exam = candidate.get("medical_exam") or {}
    if candidate.get("status") != "medical_scheduled" or not exam:
        raise HTTPException(409, "El candidato no tiene un examen médico agendado.")
    if exam.get("result"):
        raise HTTPException(409, "El resultado del examen médico ya fue registrado.")
    settings = current_settings()
    conv = repo.get_conversation_by_candidate(candidate_id)
    exam = {
        **exam,
        "result": payload.result,
        "result_notes": payload.notes,
        "result_at": _now_iso(),
        "result_by": user.get("email") or "",
    }
    status = "hired" if payload.result == "apto" else "rejected"
    repo.update_candidate(candidate_id, {"medical_exam": exam, "status": status})
    if status == "rejected":
        _close_conversation_on_reject(candidate_id, reason="medical_no_apto")
    notified = outbox.deliver_candidate_notify(
        settings, candidate, DECISION_HIRED if payload.result == "apto" else DECISION_REJECT,
        conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
    )
    # Summary sin el resultado: es dato de salud y la bitácora la leen todos los admin.
    _audit(user, "candidate.medical_result", entity_type="candidate", entity_id=candidate_id,
           summary=f"resultado registrado · {candidate.get('name', '')}")
    return {"status": status, "notified": notified}


# ── Onboarding: fecha de ingreso + kit de materiales (auditoría v3, Parte C) ───────

@router.post("/api/candidates/{candidate_id}/start-date")
def set_start_date(
    candidate_id: str,
    payload: StartDateIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Fija la fecha del primer día de trabajo; el scheduler enviará el kit ese día."""
    candidate, _ = _require_candidate_in_tenant(candidate_id, user)
    if candidate.get("status") != "hired":
        raise HTTPException(409, "Solo se puede fijar la fecha de ingreso de un contratado.")
    repo.update_candidate(candidate_id, {"start_date": payload.start_date})
    _audit(user, "candidate.start_date", entity_type="candidate", entity_id=candidate_id,
           summary=f"ingreso {payload.start_date} · {candidate.get('name', '')}")
    return {"start_date": payload.start_date}


@router.post("/api/candidates/{candidate_id}/onboarding")
def send_onboarding_now(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    """Respaldo manual: envía el kit de onboarding ahora (idempotente por onboarding.sent_at)."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if candidate.get("status") != "hired":
        raise HTTPException(409, "Solo se puede enviar el kit a un contratado.")
    if (candidate.get("onboarding") or {}).get("sent_at"):
        raise HTTPException(409, "El kit de onboarding ya fue enviado a este candidato.")
    kit = (vacancy or {}).get("onboarding_kit") or {}
    if not (kit.get("welcome") or kit.get("materials")):
        raise HTTPException(409, "La vacante no tiene un kit de onboarding configurado.")
    result = _deliver_onboarding_kit(current_settings(), candidate, vacancy, kit, sent_by=user.get("email") or "")
    _audit(user, "candidate.onboarding", entity_type="candidate", entity_id=candidate_id,
           summary=f"kit de onboarding enviado · {candidate.get('name', '')}")
    return result


@router.post("/api/candidates/{candidate_id}/attendance")
def mark_attendance(
    candidate_id: str,
    payload: AttendanceIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """RR.HH. marca la asistencia a la entrevista de una etapa. `no_show` puede reagendar o cerrar."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    settings = current_settings()
    conv = repo.get_conversation_by_candidate(candidate_id)
    if not conv:
        raise HTTPException(404, "El candidato no tiene una conversación")
    meeting = repo.get_meeting_by_conversation_stage(conv["id"], payload.stage)
    if not meeting:
        raise HTTPException(404, f"No hay reunión agendada en la etapa '{payload.stage}'")
    repo.set_meeting_attendance(meeting["id"], payload.attended)

    if payload.attended == "attended":
        _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
               summary=f"asistió ({payload.stage}) · {candidate.get('name', '')}")
        return {"attendance": "attended", "status": candidate.get("status")}

    # No asistió: reagendar (reabre horarios de la misma etapa) o cerrar (no_show + notifica).
    service = _state.get("service")
    if payload.reschedule and service:
        result = service.initiate_scheduling(
            candidate, vacancy, stage=payload.stage, modality=meeting.get("modality") or "virtual"
        )
        sent = _send_scheduling_messages(candidate, result.messages)
        _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
               summary=f"no asistió → reagendar ({payload.stage}) · {candidate.get('name', '')}")
        return {"attendance": "no_show", "rescheduled": True, "messages_sent": sent, "messages": result.messages}

    repo.update_candidate(candidate_id, {"status": "no_show"})
    _close_conversation_on_reject(candidate_id, reason="no_show")
    notified = outbox.deliver_candidate_notify(
        settings, candidate, DECISION_REJECT, conversation_id=conv["id"], tenant_id=user["tenant_id"]
    )
    _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
           summary=f"no asistió → cerrar ({payload.stage}) · {candidate.get('name', '')}")
    return {"attendance": "no_show", "status": "no_show", "notified": notified}


@router.post("/api/candidates/{candidate_id}/advance-stage")
def advance_stage(
    candidate_id: str,
    payload: AdvanceStageIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Registra el feedback + decisión de una etapa. Aprobar 'hr'/'lead' agenda la etapa siguiente;
    aprobar 'manager' contrata; rechazar cierra y notifica."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    settings = current_settings()
    conv = repo.get_conversation_by_candidate(candidate_id)
    repo.save_stage_feedback({
        "candidate_id": candidate_id,
        "conversation_id": conv["id"] if conv else None,
        "stage": payload.stage,
        "feedback": payload.feedback,
        "decision": payload.decision,
        "decided_by": user.get("id"),
        "decided_email": user.get("email") or "",
    })
    _audit(user, "candidate.stage_decision", entity_type="candidate", entity_id=candidate_id,
           summary=f"{payload.stage}: {payload.decision} · {candidate.get('name', '')}")

    if payload.decision == "rejected":
        repo.update_candidate(candidate_id, {"status": "rejected"})
        _close_conversation_on_reject(candidate_id, reason=f"{payload.stage}_rejected")
        notified = outbox.deliver_candidate_notify(
            settings, candidate, DECISION_REJECT,
            conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
        )
        return {"status": "rejected", "notified": notified}

    # Aprobado.
    next_stage = _NEXT_STAGE.get(payload.stage)
    if next_stage is None:
        # Aprobar en 'manager': con el examen médico activo (setting por-tenant, auditoría v3)
        # falta la cita médica antes de contratar; apagado = contrata directo (retrocompat).
        medical_cfg = repo.get_app_setting("medical_exam", _DEFAULT_MEDICAL, user["tenant_id"]) or {}
        if medical_cfg.get("enabled"):
            from agente.prompts import NOTIFY_MEDICAL_PENDING

            repo.update_candidate(candidate_id, {"status": "medical_pending"})
            notified = outbox.deliver_candidate_text(
                settings, candidate,
                NOTIFY_MEDICAL_PENDING.format(name=candidate.get("name") or ""),
                conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
            )
            return {"status": "medical_pending", "notified": notified}
        repo.update_candidate(candidate_id, {"status": "hired"})
        notified = outbox.deliver_candidate_notify(
            settings, candidate, DECISION_HIRED,
            conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
        )
        return {"status": "hired", "notified": notified}

    # Agenda la etapa siguiente (lead: modalidad elegida por RR.HH.; manager: forzado presencial).
    modality = "onsite" if next_stage == "manager" else payload.modality
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"]) or {}
    service = _state.get("service")
    if not (sched.get("enabled") and service):
        raise HTTPException(409, "El agendamiento no está activo: no se puede coordinar la etapa siguiente")
    result = service.initiate_scheduling(candidate, vacancy, stage=next_stage, modality=modality)
    sent = _send_scheduling_messages(candidate, result.messages)
    status = {"lead": "lead_scheduling", "manager": "mgr_scheduling"}[next_stage]
    return {
        "status": status,
        "scheduling_started": True,
        "stage": next_stage,
        "modality": modality,
        "messages_sent": sent,
        "messages": result.messages,
    }


@router.delete("/api/candidates/{candidate_id}")
def erase_candidate(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Derecho al olvido (Ley 29733): borra el candidato y todos sus datos (cascada) +
    checkpoint + PII residual (payloads del outbox y resúmenes de auditoría — audit S4)."""
    _require_candidate_in_tenant(candidate_id, user)
    conv = repo.get_conversation_by_candidate(candidate_id)
    if conv and conv.get("langgraph_thread_id"):
        repo.delete_langgraph_checkpoint(conv["langgraph_thread_id"])
    # S4: los envíos encolados llevan correos completos y la bitácora de auditoría el
    # nombre; se purgan ANTES del delete (la FK cascade de 0022 cubre el outbox, esta
    # llamada explícita cubre entornos sin la migración). El registro nuevo del borrado
    # no incluye el nombre a propósito.
    try:
        repo.delete_outbox_by_candidate(candidate_id)
        repo.scrub_audit_for_entity(candidate_id)
        # Trazas LLM (O-1): la FK cascade de 0024 las cubre; explícito para entornos sin ella.
        repo.delete_llm_traces_by_candidate(candidate_id)
    except Exception:  # noqa: BLE001 — la purga extra no debe frenar el erasure
        pass
    repo.delete_candidate(candidate_id)
    _audit(user, "candidate.delete", entity_type="candidate", entity_id=candidate_id,
           summary="borrado (derecho al olvido)")
    return {"deleted": True}


@router.get("/api/candidates/{candidate_id}/traces")
def list_candidate_traces(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Trazas LLM crudas del candidato (prompt/respuesta por llamada — O-1, replay/debug).

    Solo admin: los prompts contienen las respuestas del candidato (PII) y el texto
    íntegro de los prompts del sistema. Vacío si `LLM_TRACE_ENABLED` está apagado."""
    _require_candidate_in_tenant(candidate_id, user)
    return {"items": repo.list_llm_traces(candidate_id)}
