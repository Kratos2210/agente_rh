"""Notificación al candidato según la decisión del reclutador (avanza / rechaza).

Envía por el canal del candidato. En Telegram usa la API HTTP directa
(no requiere el bot corriendo), así un endpoint del dashboard puede dispararla.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from agent.prompts import NOTIFY_ADVANCE, NOTIFY_HIRED, NOTIFY_REJECT
from channels.base import CHANNEL_TELEGRAM
from src.config import Settings
from src.logging_config import get_logger

logger = get_logger(__name__)

DECISION_ADVANCE = "advance"
DECISION_REJECT = "reject"
DECISION_HIRED = "hired"

# Seguridad (audit F1): las excepciones de httpx incluyen la URL del request en su
# mensaje, y las llamadas a Telegram llevan el token EN la URL
# (api.telegram.org/bot<id>:<secret>/...). Ese texto termina en logs (logger.exception,
# outbox) y en outbox.last_error (persistido en DB). Redactamos el token antes de que
# el mensaje escape. Subir httpx a WARNING (logging_config) tapa el log de request de
# httpx, pero NO nuestras propias excepciones re-lanzadas.
_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def redact_token(text: str) -> str:
    """Oculta el token del bot de Telegram si aparece embebido en `text`."""
    return _TOKEN_RE.sub("bot<REDACTED>", text)


def render_message(decision: str, name: str) -> str:
    template = {
        DECISION_ADVANCE: NOTIFY_ADVANCE,
        DECISION_HIRED: NOTIFY_HIRED,
    }.get(decision, NOTIFY_REJECT)
    return template.format(name=name or "")


def can_send_telegram(settings: Settings, chat_id: Any) -> bool:
    """True si hay token y el chat_id es un chat real de Telegram (numérico)."""
    return bool(settings.telegram_bot_token) and str(chat_id).lstrip("-").isdigit()


def post_telegram(settings: Settings, chat_id: str, text: str) -> None:
    """POST sendMessage a Telegram. LANZA si falla (habilita el reintento del outbox).

    Re-lanza un error **saneado** (`from None`): el mensaje de la excepción de httpx
    lleva la URL con el token, y esa cadena acabaría en logs y en outbox.last_error
    (audit F1). Redactamos el token y cortamos la cadena de la causa para que tampoco
    aparezca en el traceback.
    """
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Envío a Telegram falló: {redact_token(str(e))}") from None


def send_text(settings: Settings, channel: str, chat_id: str, text: str) -> bool:
    """Envía un texto al candidato por su canal. Devuelve True si se envió. No lanza.

    Telegram solo entrega a chats que ya iniciaron el bot (/start); con un chat_id no
    numérico/desconocido (postulante simulado) devuelve False y el contacto queda 'simulado'.
    """
    if channel == CHANNEL_TELEGRAM:
        if not can_send_telegram(settings, chat_id):
            return False
        try:
            post_telegram(settings, chat_id, text)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Fallo al enviar texto al candidato por Telegram")
            return False
    return False


def notify_candidate(settings: Settings, candidate: dict, decision: str) -> bool:
    """Envía la notificación final. Devuelve True si se envió. No lanza.

    (El camino robusto con reintentos es notifications.outbox.deliver_candidate_notify.)"""
    channel = candidate.get("channel")
    if channel != CHANNEL_TELEGRAM:
        logger.info("Canal '%s' aún no soportado para notificación de candidato", channel)
        return False
    if not settings.telegram_bot_token:
        logger.warning("Sin TELEGRAM_BOT_TOKEN: no se puede notificar al candidato")
        return False
    text = render_message(decision, candidate.get("name") or "")
    return send_text(settings, channel, candidate.get("channel_user_id"), text)
