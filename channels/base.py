"""Contrato común de canales (Telegram hoy, WhatsApp después).

El motor y el `InterviewService` son agnósticos al canal: trabajan con
`InboundMessage` (normalizado por cada adapter) y devuelven mensajes de texto +
una bandera de botones de consentimiento. Cada canal traduce eso a su API nativa
(inline keyboard en Telegram, interactive buttons en WhatsApp).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Payloads de los botones de consentimiento (callback_data en Telegram).
BUTTON_ACCEPT = "accept"
BUTTON_DECLINE = "decline"

ACCEPT_LABEL = "Acepto ✅"
DECLINE_LABEL = "No interesado"

# Nombres de canal (columna candidates.channel / prefijo del thread_id de LangGraph).
CHANNEL_TELEGRAM = "telegram"
CHANNEL_WHATSAPP = "whatsapp"


@dataclass
class InboundMessage:
    """Mensaje entrante normalizado, independiente del canal."""

    channel: str
    chat_id: str
    text: Optional[str] = None
    button: Optional[str] = None       # BUTTON_ACCEPT | BUTTON_DECLINE | None
    display_name: str = ""
    document: Optional[dict[str, Any]] = None  # PDF recibido: {file_id, filename, local_path}

    @property
    def thread_id(self) -> str:
        """thread_id que usa el checkpointer de LangGraph."""
        return f"{self.channel}:{self.chat_id}"
