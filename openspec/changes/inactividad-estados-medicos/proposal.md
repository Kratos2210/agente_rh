# Propuesta: Inactividad en estados médicos

## Why (por qué)

Hoy la vigilancia de inactividad cubre saludo, entrevista, documentos y coordinación de agenda,
pero un candidato en `medical_pending`/`medical_scheduled` que deja de responder (no confirma la
cita, no asiste) queda invisible: solo recibe un acuse pasivo si escribe, y nadie recuerda ni
alerta. Pendiente declarado en `audit/auditoria_v3.md` (Parte D) y en `spec/Producto.md` §6.

## What Changes (qué cambia)

- El barrido de inactividad incluye los estados `medical_pending` y `medical_scheduled`:
  recordatorio al candidato (confirmar/asistir a la cita) con la MISMA config por-tenant
  (`reminder_minutes`, `max_reminders`).
- Al agotar recordatorios NO se auto-cierra (decisión humana): se emite alerta operativa
  `medical_unresponsive` para que RR.HH. decida (patrón de la coordinación de agenda).
  Complementa a la existente `medical_stuck` (reconciliación por antigüedad, > N días): esta
  nueva mide silencio del candidato tras recordatorios activos, no solo el paso del tiempo.
- Prompt nuevo `REMINDER_MEDICAL` en `agente/prompts.py` (con bump de `PROMPT_VERSION`).

## Non-goals (fuera de alcance)

- Cerrar/rechazar automáticamente por silencio en fase médica (siempre decide RR.HH.).
- Cambiar el flujo médico en sí (cita, resultado) ni sus endpoints.
- WhatsApp u otros canales.

## Impacto

- Capacidad afectada: `examenes-contratacion` (delta en `specs/`), toca también el barrido de
  `entrevista` (reusa su maquinaria de recordatorios sin cambiar sus requisitos).
- Código previsto: `api/scheduler.py` (`_inactivity_sweep` + `_collect_ops_alerts`, alerta
  nueva junto a `scheduling_stuck`) y `agente/prompts.py`. Manual de dominio: `spec/Producto.md`.
