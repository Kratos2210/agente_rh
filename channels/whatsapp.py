"""Stub del canal WhatsApp (Cloud API) — Fase 6.

WhatsApp Cloud API soporta botones interactivos nativos (reply buttons) que mapean
1:1 con el consentimiento Acepto / No interesado, igual que SofIA. El motor y el
InterviewService no cambian: este adapter solo traducirá:
  - webhook entrante de Meta  → channels.base.InboundMessage (text o button)
  - TurnResult                → mensajes de texto + interactive buttons de WhatsApp

Implementación pendiente: verificación del webhook (hub.challenge), envío vía
Graph API (/{phone_number_id}/messages) y plantillas para el primer contacto
(las ventanas de 24h exigen plantilla aprobada para iniciar conversación).
"""

from __future__ import annotations

# TODO(Fase 6): parse_inbound(payload) -> InboundMessage
# TODO(Fase 6): async send_messages(text + interactive reply buttons)
