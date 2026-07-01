"""Bot de Telegram del agente de selección (modo polling, arranca en el lifespan).

Conecta los updates de Telegram con el InterviewService (agnóstico al canal):
  - primer mensaje / botón inician y conducen la entrevista,
  - los botones Acepto / No interesado llegan como callback queries,
  - el trabajo síncrono (grafo + Supabase + LLM) corre en un hilo para no bloquear
    el event loop.
"""

from __future__ import annotations

import asyncio
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

from agent.service import InterviewService
from channels.base import CHANNEL_TELEGRAM, InboundMessage
from channels.telegram import send_messages
from src.config import Settings
from src.logging_config import get_logger

logger = get_logger(__name__)

_allowed_users: set[int] = set()


def _init_allowed_users(settings: Settings) -> None:
    global _allowed_users
    raw = (settings.telegram_allowed_users or "").strip()
    _allowed_users = {int(x) for x in raw.split(",") if x.strip().isdigit()} if raw else set()


def _is_allowed(chat_id: int) -> bool:
    return not _allowed_users or chat_id in _allowed_users


def build_bot_app(settings: Settings, state: dict[str, Any]) -> Application:
    """Construye la Application de PTB con el servicio de entrevista inyectado."""
    from agent.graph import make_postgres_runner
    from agent.llm import MeteredLLM, build_default_llm
    from db.client import get_database_url

    _init_allowed_users(settings)

    from integrations.scheduling import get_scheduler

    runner = make_postgres_runner(MeteredLLM(build_default_llm()), get_database_url())
    notifier = _build_notifier(settings)
    service = InterviewService(
        runner,
        notify_recruiter=notifier,
        notify_meeting=_build_meeting_notifier(settings),
        scheduler=get_scheduler(settings),
        settings=settings,
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

async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, *, text=None, button=None, document=None) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None:
        return
    if not _is_allowed(chat.id):
        await context.bot.send_message(chat_id=chat.id, text="No tienes acceso a este bot.")
        return

    service: InterviewService = context.bot_data["service"]
    inbound = InboundMessage(
        channel=CHANNEL_TELEGRAM,
        chat_id=str(chat.id),
        text=text,
        button=button,
        document=document,
        display_name=(user.full_name if user else ""),
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

    await send_messages(
        context.bot, chat.id, result.messages, show_consent_buttons=result.show_consent_buttons
    )


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # El primer contacto lo decide el servicio (sin estado aún → envía intro + botones).
    await _dispatch(update, context, text=None, button=None)


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
