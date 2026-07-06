"""Outbox durable de envíos salientes (Fase 1 — no perder candidatos).

Cada notificación externa se intenta **en línea**; si falla, se **encola** en la
tabla `outbox` y el scheduler la reintenta con **backoff exponencial** hasta agotar
los intentos (**dead-letter**). El resultado (enviado / encolado / muerto) SIEMPRE
se registra — nunca es fire-and-forget silencioso (audit #4 y #6).

Flujo:
  - `deliver_*` (en los call sites): intenta enviar ya; en fallo encola `pending`.
  - `drain` (en el scheduler, con el advisory lock): procesa lo vencido, reintenta
    con backoff y marca `failed` (dead-letter) al agotar.

Los handlers realizan el envío real y **lanzan** ante fallo (`email.send_email` /
`candidate.post_telegram`), lo que dispara el reintento.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from channels.base import CHANNEL_TELEGRAM
from core.config import Settings
from core.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 6
# Backoff exponencial por intento (segundos): 1m, 5m, 15m, 1h, 3h, 6h (cap en el último).
_BACKOFF = [60, 300, 900, 3600, 10800, 21600]


# ── Política de reintentos (pura, testeable) ──────────────────────────────────

def backoff_seconds(attempt: int) -> int:
    """Espera antes del intento `attempt` (1-based). Cap en el último valor de la tabla."""
    attempt = max(1, attempt)
    return _BACKOFF[min(attempt, len(_BACKOFF)) - 1]


def next_state_after_failure(attempts: int, max_attempts: int) -> tuple[str, int]:
    """Tras un fallo con `attempts` acumulados → (status, delay_seconds).

    Si se agotaron los intentos → ('failed', 0) (dead-letter); si no →
    ('pending', backoff(attempts)) para reintentar más tarde."""
    if attempts >= max_attempts:
        return ("failed", 0)
    return ("pending", backoff_seconds(attempts))


# ── Handlers: realizan el envío real y LANZAN ante fallo ──────────────────────

def _handle_email(settings: Settings, payload: dict[str, Any]) -> None:
    from notifications.email import send_email

    send_email(settings, payload["recipients"], payload["subject"], payload["text"], payload["html"])


def _handle_telegram(settings: Settings, payload: dict[str, Any]) -> None:
    from notifications.candidate import post_telegram

    post_telegram(settings, payload["chat_id"], payload["text"])


_HANDLERS: dict[str, Callable[[Settings, dict[str, Any]], None]] = {
    "scorecard_email": _handle_email,
    "meeting_email": _handle_email,
    "meeting_recruiter_telegram": _handle_telegram,
    "candidate_notify": _handle_telegram,
    "psych_exam_email": _handle_email,
    # Correo operativo genérico (O-2 presupuesto, O-4 SLAs): payload = recipients/subject/text/html.
    "ops_email": _handle_email,
    # Cierre del proceso (auditoría v3): cita del examen médico + kit de onboarding.
    "medical_exam_email": _handle_email,
    "onboarding_email": _handle_email,
    # Texto arbitrario al candidato por Telegram (médico/onboarding): kind propio para
    # trazabilidad en /observabilidad (candidate_notify queda solo para decisiones).
    "candidate_text": _handle_telegram,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Entrega (intento en línea + encolado en fallo) ────────────────────────────

def deliver(
    settings: Settings,
    kind: str,
    payload: dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """Intenta enviar `kind` ya; si falla, lo encola para reintento durable. Devuelve si se envió."""
    from db import repositories as repo

    handler = _HANDLERS[kind]
    try:
        handler(settings, payload)
        logger.info("Outbox: %s enviado en línea", kind)
        return True
    except Exception as e:  # noqa: BLE001 — encolar para reintento durable
        attempts = 1
        status, delay = next_state_after_failure(attempts, max_attempts)
        logger.warning("Outbox: %s falló en línea (%s) → encolado (%s)", kind, e, status)
        repo.enqueue_outbox(
            {
                "kind": kind,
                "payload": payload,
                "status": status,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "next_attempt_at": (_now() + timedelta(seconds=delay)).isoformat(),
                "last_error": str(e)[:2000],
                "tenant_id": tenant_id,
                "candidate_id": candidate_id,
                "conversation_id": conversation_id,
            }
        )
        return False


def drain(settings: Settings, now: Optional[datetime] = None, limit: int = 50) -> dict[str, int]:
    """Procesa los envíos pendientes vencidos: reintenta, reprograma con backoff o dead-letter.

    Lo corre el scheduler (con el advisory lock → un solo proceso). Devuelve el reporte."""
    from db import repositories as repo

    now = now or _now()
    report = {"processed": 0, "sent": 0, "retry": 0, "dead": 0}
    for row in repo.list_due_outbox(now.isoformat(), limit):
        report["processed"] += 1
        kind = row.get("kind")
        handler = _HANDLERS.get(kind)
        if handler is None:
            repo.update_outbox(row["id"], {"status": "failed", "last_error": f"kind desconocido: {kind}", "updated_at": now.isoformat()})
            report["dead"] += 1
            continue
        try:
            handler(settings, row.get("payload") or {})
            repo.update_outbox(row["id"], {"status": "sent", "updated_at": now.isoformat()})
            report["sent"] += 1
        except Exception as e:  # noqa: BLE001
            attempts = int(row.get("attempts", 0) or 0) + 1
            status, delay = next_state_after_failure(attempts, int(row.get("max_attempts", DEFAULT_MAX_ATTEMPTS)))
            repo.update_outbox(
                row["id"],
                {
                    "status": status,
                    "attempts": attempts,
                    "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
                    "last_error": str(e)[:2000],
                    "updated_at": now.isoformat(),
                },
            )
            if status == "failed":
                report["dead"] += 1
                logger.error("Outbox: %s agotó reintentos (dead-letter) id=%s: %s", kind, row.get("id"), e)
            else:
                report["retry"] += 1
    return report


# ── Builders de conveniencia por tipo de notificación ─────────────────────────

def deliver_scorecard_email(
    settings: Settings, vacancy: dict, candidate: dict, scorecard: dict, *, conversation_id: Optional[str] = None
) -> bool:
    """Entrega (o encola) el scorecard al reclutador por correo. False si SMTP no está configurado."""
    from notifications.email import build_scorecard_email

    built = build_scorecard_email(settings, vacancy, candidate, scorecard)
    if built is None:
        return False
    recipients, subject, text, html = built
    return deliver(
        settings,
        "scorecard_email",
        {"recipients": recipients, "subject": subject, "text": text, "html": html},
        tenant_id=vacancy.get("tenant_id"),
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )


def _meeting_recruiter_text(vacancy: dict, candidate: dict, meeting: dict) -> str:
    cand_c = " · ".join(x for x in (meeting.get("candidate_email"), meeting.get("candidate_phone")) if x)
    stage_label = {"hr": "RR.HH.", "lead": "Líder del proyecto", "manager": "Gerencia"}.get(
        meeting.get("stage", "hr"), ""
    )
    onsite = meeting.get("modality") == "onsite"
    where = meeting.get("location", "") if onsite else meeting.get("meet_link", "")
    return (
        f"📅 Entrevista agendada ({stage_label})\n{candidate.get('name', '')} · {vacancy.get('title', '')}\n"
        f"{meeting.get('scheduled_at', '')}\n"
        f"{'📍 ' + where if onsite else where}"
        + (f"\nContacto candidato: {cand_c}" if cand_c else "")
    )


def deliver_meeting(
    settings: Settings, vacancy: dict, candidate: dict, meeting: dict, recruiter: dict, *, conversation_id: Optional[str] = None
) -> bool:
    """Entrega/encola el correo de la reunión (candidato+reclutador) + el aviso Telegram al reclutador.

    Son dos envíos independientes: cada uno se reintenta por su cuenta si falla."""
    from notifications.candidate import can_send_telegram
    from notifications.email import build_meeting_email

    ok = True
    built = build_meeting_email(settings, vacancy, candidate, meeting, recruiter)
    if built is not None:
        recipients, subject, text, html = built
        ok = deliver(
            settings,
            "meeting_email",
            {"recipients": recipients, "subject": subject, "text": text, "html": html},
            tenant_id=vacancy.get("tenant_id"),
            candidate_id=candidate.get("id"),
            conversation_id=conversation_id,
        ) and ok
    chat = str(recruiter.get("telegram_chat_id") or "").strip()
    if chat and can_send_telegram(settings, chat):
        ok = deliver(
            settings,
            "meeting_recruiter_telegram",
            {"chat_id": chat, "text": _meeting_recruiter_text(vacancy, candidate, meeting)},
            tenant_id=vacancy.get("tenant_id"),
            candidate_id=candidate.get("id"),
            conversation_id=conversation_id,
        ) and ok
    return ok


def deliver_psych_exam(
    settings: Settings, vacancy: dict, candidate: dict, exam: dict, *, conversation_id: Optional[str] = None
) -> bool:
    """Entrega/encola el correo de exámenes psicológicos al candidato. False si SMTP/email sin configurar."""
    from notifications.email import build_psych_exam_email

    built = build_psych_exam_email(settings, vacancy, candidate, exam)
    if built is None:
        return False
    recipients, subject, text, html = built
    return deliver(
        settings,
        "psych_exam_email",
        {"recipients": recipients, "subject": subject, "text": text, "html": html},
        tenant_id=vacancy.get("tenant_id"),
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )


def deliver_candidate_notify(
    settings: Settings, candidate: dict, decision: str, *, conversation_id: Optional[str] = None, tenant_id: Optional[str] = None
) -> bool:
    """Entrega/encola la notificación de decisión (avanza/rechaza) al candidato por Telegram."""
    from notifications.candidate import can_send_telegram, render_message

    chat = candidate.get("channel_user_id")
    if candidate.get("channel") != CHANNEL_TELEGRAM or not can_send_telegram(settings, chat):
        return False
    return deliver(
        settings,
        "candidate_notify",
        {"chat_id": str(chat), "text": render_message(decision, candidate.get("name") or "")},
        tenant_id=tenant_id,
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )


def deliver_candidate_text(
    settings: Settings, candidate: dict, text: str, *, conversation_id: Optional[str] = None, tenant_id: Optional[str] = None
) -> bool:
    """Entrega/encola un texto arbitrario al candidato por Telegram (cita médica, onboarding).

    Mismo gate que deliver_candidate_notify; kind propio `candidate_text` para trazabilidad."""
    from notifications.candidate import can_send_telegram

    chat = candidate.get("channel_user_id")
    if candidate.get("channel") != CHANNEL_TELEGRAM or not can_send_telegram(settings, chat):
        return False
    return deliver(
        settings,
        "candidate_text",
        {"chat_id": str(chat), "text": text},
        tenant_id=tenant_id,
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )


def deliver_medical_exam(
    settings: Settings, vacancy: dict, candidate: dict, exam: dict, *, conversation_id: Optional[str] = None
) -> bool:
    """Entrega/encola el correo de la cita del examen médico. False si SMTP/email sin configurar."""
    from notifications.email import build_medical_exam_email

    built = build_medical_exam_email(settings, vacancy, candidate, exam)
    if built is None:
        return False
    recipients, subject, text, html = built
    return deliver(
        settings,
        "medical_exam_email",
        {"recipients": recipients, "subject": subject, "text": text, "html": html},
        tenant_id=vacancy.get("tenant_id"),
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )


def deliver_onboarding(
    settings: Settings, vacancy: dict, candidate: dict, kit: dict, *, conversation_id: Optional[str] = None
) -> bool:
    """Entrega/encola el correo del kit de onboarding. False si SMTP/email sin configurar."""
    from notifications.email import build_onboarding_email

    built = build_onboarding_email(settings, vacancy, candidate, kit)
    if built is None:
        return False
    recipients, subject, text, html = built
    return deliver(
        settings,
        "onboarding_email",
        {"recipients": recipients, "subject": subject, "text": text, "html": html},
        tenant_id=vacancy.get("tenant_id"),
        candidate_id=candidate.get("id"),
        conversation_id=conversation_id,
    )
