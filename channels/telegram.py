"""Glue específico de Telegram: botones inline y envío de mensajes.

Traduce el resultado agnóstico del InterviewService (texto + bandera de botones)
a la API de python-telegram-bot.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from channels.base import (
    ACCEPT_LABEL,
    BUTTON_ACCEPT,
    BUTTON_DECLINE,
    DECLINE_LABEL,
)

_MAX_MSG = 4096  # límite de Telegram por mensaje


def consent_markup() -> InlineKeyboardMarkup:
    """Botones Acepto / No interesado (callback_data = payload del canal)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(ACCEPT_LABEL, callback_data=BUTTON_ACCEPT),
                InlineKeyboardButton(DECLINE_LABEL, callback_data=BUTTON_DECLINE),
            ]
        ]
    )


def _chunks(text: str):
    for i in range(0, len(text), _MAX_MSG):
        yield text[i : i + _MAX_MSG]


async def send_messages(bot, chat_id: int, messages: list[str], *, show_consent_buttons: bool) -> None:
    """Envía los mensajes del turno; adjunta los botones al último si corresponde."""
    for idx, msg in enumerate(messages):
        is_last = idx == len(messages) - 1
        markup = consent_markup() if (show_consent_buttons and is_last) else None
        parts = list(_chunks(msg)) or [""]
        for j, part in enumerate(parts):
            await bot.send_message(
                chat_id=chat_id,
                text=part,
                reply_markup=markup if j == len(parts) - 1 else None,
            )
