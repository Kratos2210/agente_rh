# Design: Inactividad en estados médicos

## Decisión clave

Reusar la maquinaria existente de inactividad (config por-tenant + `_inactivity_sweep`) en vez
de un barrido nuevo: los estados médicos entran a la lista de estados barridos con un mensaje
propio, igual que se hizo con `greeting` y `scheduling`.

## Cómo

1. **Estados barridos**: `_inactivity_sweep` (`api/scheduler.py`) suma `medical_pending` y
   `medical_scheduled` a los estados vigilados. La referencia de actividad sigue siendo
   `conversations.last_activity_at` + `reminders_sent` (sin columnas nuevas, sin migración).
2. **Mensaje**: `_reminder_messages` devuelve `REMINDER_MEDICAL` (prompt nuevo, texto de
   confirmar/asistir a la cita) para esos estados.
3. **Sin auto-cierre**: como en `scheduling`, al agotar `max_reminders` NO se envía timeout al
   motor; se marca la condición y `_collect_ops_alerts` emite `medical_unresponsive`
   (patrón `scheduling_stuck`). La decisión queda en RR.HH.
4. **PROMPT_VERSION**: bump + línea de changelog (el gate de CI lo exige).

## Alternativas descartadas

- Barrido dedicado para lo médico: duplica dedupe/config/locking sin beneficio.
- Auto-rechazo por silencio: contradice el principio "la IA propone, el humano decide"
  (spec `examenes-contratacion`, requirement de estado terminal y decisión humana).

## Riesgos

- Los estados médicos viven en `candidates.status` (no en la fase del checkpoint): el barrido
  debe resolverlos por status de negocio, no por fase del motor (mismo mapa
  `_vacancy_tenant_map` que ya usan los demás barridos).
