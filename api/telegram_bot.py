"""Bot de Telegram del agente de selección (modo polling, arranca en el lifespan).

Conecta los updates de Telegram con el InterviewService (agnóstico al canal):
  - primer mensaje / botón inician y conducen la entrevista,
  - los botones Acepto / No interesado llegan como callback queries,
  - el trabajo síncrono (grafo + Supabase + LLM) corre en un hilo para no bloquear
    el event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agente.service import InterviewService
from api.ratelimit import TURN_BLOCKED, TURN_CAP_NOTICE, TURN_COOLDOWN, TurnGovernor
from channels.base import CHANNEL_TELEGRAM, InboundMessage
from channels.telegram import send_messages
from core.config import Settings
from core.logging_config import get_logger

logger = get_logger(__name__)

_allowed_users: set[int] = set()

# R2 (auditoría): gobierna los turnos por chat (cooldown + tope diario). Se reconfigura
# en build_bot_app con los valores de settings.
_governor = TurnGovernor()

_CAP_NOTICE_TEXT = (
    "Recibimos muchos mensajes tuyos hoy 🙈 Para continuar, retomemos mañana; "
    "tu proceso queda guardado justo donde lo dejamos. ¡Gracias por tu comprensión! 🙌"
)

# G3 (auditoría): threads con la última entrega fallida (para limpiar la marca en DB
# apenas un envío posterior sí llegue, sin costar una escritura por turno sano).
_delivery_failed_threads: set[str] = set()


def _mark_delivery_result(chat_id: int, ok: bool) -> None:
    """Registra en la conversación si el envío por Telegram llegó o no (audit G3).

    El servicio persiste los mensajes en la transcripción ANTES del envío; si Telegram
    falla, la transcripción afirmaría una entrega que no pasó — esta marca alimenta la
    alerta operativa `delivery_failed` y se limpia al volver a entregar con éxito."""
    from datetime import datetime, timezone

    from db import repositories as repo

    thread = f"{CHANNEL_TELEGRAM}:{chat_id}"
    if ok:
        if thread not in _delivery_failed_threads:
            return
        _delivery_failed_threads.discard(thread)
        when = None
    else:
        _delivery_failed_threads.add(thread)
        when = datetime.now(timezone.utc).isoformat()
    try:
        conv = repo.get_conversation_by_thread(thread)
        if conv:
            repo.set_delivery_failure(conv["id"], when)
    except Exception:  # noqa: BLE001 — la marca nunca rompe el turno
        logger.exception("No se pudo registrar el resultado de entrega (chat %s)", chat_id)


# ── Webhook (roadmap paso 3) ─────────────────────────────────────────────────
# Ruta relativa donde el backend recibe los updates de Telegram (montada en api/main.py).
WEBHOOK_PATH = "/telegram/webhook"


def resolve_webhook_secret(settings: Settings) -> str:
    """Secreto para el header X-Telegram-Bot-Api-Secret-Token.

    Si el operador no fija TELEGRAM_WEBHOOK_SECRET, se deriva del token del bot para que el
    endpoint NUNCA quede sin validar en modo webhook. Determinístico y estable entre réplicas.
    Telegram acepta [A-Za-z0-9_-] 1–256 chars; el hex del sha256 cumple."""
    explicit = (settings.telegram_webhook_secret or "").strip()
    if explicit:
        return explicit
    token = (settings.telegram_bot_token or "").encode()
    return hashlib.sha256(b"agente_rh-webhook:" + token).hexdigest()


def webhook_enabled(settings: Settings) -> bool:
    """El bot corre en modo webhook si hay token Y una URL pública configurada."""
    return bool((settings.telegram_bot_token or "").strip() and (settings.telegram_webhook_url or "").strip())


def webhook_url(settings: Settings) -> str:
    """URL completa del webhook: {telegram_webhook_url}/telegram/webhook (sin doble slash)."""
    base = (settings.telegram_webhook_url or "").strip().rstrip("/")
    return f"{base}{WEBHOOK_PATH}"


def secret_matches(settings: Settings, header_value: str | None) -> bool:
    """Compara en tiempo constante el secreto recibido con el esperado (anti timing)."""
    expected = resolve_webhook_secret(settings)
    return bool(header_value) and hmac.compare_digest(str(header_value), expected)


async def process_webhook_update(app: Application, data: dict[str, Any]) -> None:
    """Deserializa el update crudo de Telegram y lo encola en el Application ya arrancado.

    En modo webhook no corre el updater (que hace polling); el Application igualmente consume
    su update_queue tras app.start(), así que basta con encolar el update deserializado."""
    update = Update.de_json(data, app.bot)
    await app.update_queue.put(update)


def _init_allowed_users(settings: Settings) -> None:
    global _allowed_users
    raw = (settings.telegram_allowed_users or "").strip()
    _allowed_users = {int(x) for x in raw.split(",") if x.strip().isdigit()} if raw else set()


def _is_allowed(chat_id: int) -> bool:
    return not _allowed_users or chat_id in _allowed_users


def build_bot_app(settings: Settings, state: dict[str, Any]) -> Application:
    """Construye la Application de PTB con el servicio de entrevista inyectado."""
    from agente.graph import make_postgres_runner
    from orquestacion.llm import MeteredLLM, build_default_llm, build_stage_overrides
    from db.client import get_database_url

    _init_allowed_users(settings)

    from integrations.scheduling import get_scheduler

    global _governor
    _governor = TurnGovernor(
        cooldown_seconds=settings.bot_turn_cooldown_seconds,
        max_turns_per_day=settings.bot_max_turns_per_day,
    )

    # RAG de dudas del candidato (config-gated, lazy): None si interview_rag_enabled=False.
    from retrieval.answer_cache import build_answer_cache
    from retrieval.rag import build_company_retriever

    runner = make_postgres_runner(
        MeteredLLM(
            build_default_llm(),
            trace=settings.llm_trace_enabled,
            trace_max_chars=settings.llm_trace_max_chars,
            overrides=build_stage_overrides(settings),  # routing de costos (paso 5)
        ),
        get_database_url(),
        retriever=build_company_retriever(settings),
        answer_cache=build_answer_cache(settings),  # caché de dudas (paso 5)
    )
    notifier = _build_notifier(settings)
    service = InterviewService(
        runner,
        notify_recruiter=notifier,
        notify_meeting=_build_meeting_notifier(settings),
        scheduler=get_scheduler(settings),
        settings=settings,
        # Lock distribuido por conversación (roadmap v2, paso 2): con DATABASE_URL, el
        # turno toma además un advisory lock de Postgres → seguro con replicas>1 (webhook).
        database_url=get_database_url(),
    )
    state["service"] = service

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["service"] = service
    app.add_handler(CommandHandler("start", _on_start))
    app.add_handler(CallbackQueryHandler(_on_button))
    app.add_handler(MessageHandler(filters.Document.ALL, _on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    logger.info("Handlers de Telegram registrados")
    return app


def _build_notifier(settings: Settings):
    """Notificador del reclutador (scorecard por email) vía outbox (reintentos + dead-letter)."""
    from notifications.outbox import deliver_scorecard_email

    return lambda vacancy, candidate, conv, scorecard: deliver_scorecard_email(
        settings, vacancy, candidate, scorecard, conversation_id=conv.get("id")
    )


def _build_meeting_notifier(settings: Settings):
    """Notifica la reunión agendada (correo candidato+reclutador + Telegram al reclutador) vía outbox."""
    from notifications.outbox import deliver_meeting

    def notify(vacancy: dict, candidate: dict, meeting: dict, recruiter: dict) -> None:
        deliver_meeting(
            settings, vacancy, candidate, meeting, recruiter,
            conversation_id=meeting.get("conversation_id"),
        )

    return notify


# ── Handlers ─────────────────────────────────────────────────────────────────

async def _dispatch(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, text=None, button=None, document=None, start_payload=None
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None:
        return
    if not _is_allowed(chat.id):
        await context.bot.send_message(chat_id=chat.id, text="No tienes acceso a este bot.")
        return

    # R2: cooldown + tope diario por chat ANTES de gastar LLM. En cooldown se ignora en
    # silencio (mensajes en ráfaga); al alcanzar el tope se avisa UNA vez y luego silencio.
    verdict = _governor.check(str(chat.id))
    if verdict == TURN_COOLDOWN or verdict == TURN_BLOCKED:
        return
    if verdict == TURN_CAP_NOTICE:
        logger.warning("Chat %s alcanzó el tope diario de turnos del bot", chat.id)
        await context.bot.send_message(chat_id=chat.id, text=_CAP_NOTICE_TEXT)
        return

    service: InterviewService = context.bot_data["service"]
    inbound = InboundMessage(
        channel=CHANNEL_TELEGRAM,
        chat_id=str(chat.id),
        text=text,
        button=button,
        document=document,
        display_name=(user.full_name if user else ""),
        start_payload=start_payload,
    )
    try:
        result = await asyncio.to_thread(service.process, inbound)
    except Exception:  # noqa: BLE001
        logger.exception("Error procesando turno de Telegram")
        await context.bot.send_message(
            chat_id=chat.id,
            text="Tuvimos un problema técnico. Por favor, intenta de nuevo en un momento.",
        )
        return

    try:
        await send_messages(
            context.bot, chat.id, result.messages, show_consent_buttons=result.show_consent_buttons
        )
    except Exception:  # noqa: BLE001 — G3: la transcripción ya registró estos mensajes
        logger.exception("No se pudo entregar la respuesta por Telegram (chat %s)", chat.id)
        await asyncio.to_thread(_mark_delivery_result, chat.id, False)
        return
    if _delivery_failed_threads:
        await asyncio.to_thread(_mark_delivery_result, chat.id, True)


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # El primer contacto lo decide el servicio (sin estado aún → envía intro + botones).
    # Deep-link multi-tenant: t.me/<bot>?start=<vacancy_id> llega como argumento de /start
    # y enruta al candidato a ESA vacante (sin payload cae a la vacante default).
    payload = (context.args[0] if context.args else "").strip() or None
    await _dispatch(update, context, text=None, button=None, start_payload=payload)


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    await _dispatch(update, context, text=(msg.text if msg else None), button=None)


async def _on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()  # quita el "reloj" del botón
    await _dispatch(update, context, text=None, button=query.data)


async def _on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recibe un PDF (CV/CUL): valida tipo/tamaño, lo descarga saneado a uploads/{chat_id}/."""
    from channels.documents import sanitize_filename, validate_document

    msg = update.effective_message
    chat = update.effective_chat
    doc = msg.document if msg else None
    if doc is None or chat is None:
        return
    # Validación previa a la descarga: solo PDF, dentro del límite de tamaño.
    ok, reason = validate_document(doc.mime_type, doc.file_size, doc.file_name)
    if not ok:
        await context.bot.send_message(chat_id=chat.id, text=reason)
        return
    filename = sanitize_filename(doc.file_name or f"{doc.file_unique_id}.pdf")
    local_path = ""
    try:
        dest_dir = (Path("uploads") / str(chat.id)).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Anti path-traversal: el destino debe quedar DENTRO de dest_dir.
        target = (dest_dir / filename).resolve()
        if dest_dir != target.parent:
            raise ValueError("ruta de documento fuera de uploads/")
        tg_file = await context.bot.get_file(doc.file_id)
        local_path = str(target)
        await tg_file.download_to_drive(local_path)
    except Exception:  # noqa: BLE001 — si falla la descarga, igual registramos el file_id
        logger.exception("No se pudo descargar el documento de Telegram")
    document = {
        "file_id": doc.file_id,
        "filename": filename,
        "local_path": local_path,
        "mime": doc.mime_type or "application/pdf",
    }
    await _dispatch(update, context, text=None, button=None, document=document)
