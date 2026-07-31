# Integraciones — Sourcing de postulantes y agendamiento (Google)

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `integrations/`
> Specs normativos: `openspec/specs/sourcing-prescreen/spec.md` · `openspec/specs/agendamiento-multietapa/spec.md`

## 1. Propósito y alcance

Las dos integraciones de negocio con el mundo exterior: importación de postulantes desde
portales de empleo (`sourcing.py`) y coordinación de reuniones con calendario real
(`scheduling.py`: Google Calendar + Meet + Sheets). El SMTP se cubre en
[Notificaciones.md](Notificaciones.md).

## 2. Decisiones de diseño

- **Protocol + factory en ambas**: `SimulatedConnector`/`SimulatedScheduler` como default sin
  credenciales (demo end-to-end completa) y la implementación real detrás de config
  (`SOURCING_PROVIDER`, `SCHEDULING_PROVIDER`), con carga lazy de los SDKs. El motor y el
  servicio no saben cuál corre.
- **Identidad estable de plataforma (`source_ref`)**: el dedupe del re-sync es por el id del
  portal, NO por `(vacancy, channel, chat)` — el chat puede reasignarse (demo) y eso duplicaba
  candidatos (bug real cazado en smoke). Migración `0023` + backfill.
- **El sync NO auto-contacta por sí mismo**: importa y pre-filtra; el contacto es una acción
  separada (automática con `AUTO_CONTACT_ON_PASS` + horario laboral, o manual). Separar
  importar/contactar hizo el sync idempotente y reversible.
- **Registro-primero en el agendamiento**: la reunión se persiste ANTES de crear el evento de
  Calendar; un crash a mitad no duplica el evento (luego `update_meeting` completa
  link/event_id).
- **Dos modos de credencial Google**: OAuth de usuario (Gmail personal, genera Meet real;
  `scripts/google_oauth.py`) o cuenta de servicio con Domain-Wide Delegation (Workspace).

## 3. Diseño e implementación

### Sourcing (`integrations/sourcing.py` + `agente/sourcing_service.py`)
- `SimulatedConnector` lee el fixture Bumeran (`integrations/fixtures/`) → `cv_profile`
  normalizado (incluye email/phone). Flujo del sync: importa → gate de CV
  ([Evaluacion.md](Evaluacion.md)) → aptos quedan `prescreen_passed` → contacto según config.
- Idempotencia: `find_candidate_by_source_ref` primero; re-sync no retrocede estados ni
  re-contacta; refresh sana filas legadas. Rate limit 2/min/tenant en el endpoint.

### Agendamiento (`integrations/scheduling.py`)
- Helper **puro** `compute_free_slots(busy, ventana laboral, slot_minutes, horizon)` — testeable
  sin Google.
- `SimulatedScheduler`: reclutador siempre libre, Meet falso, "Sheet" CSV local en `uploads/`.
- `GoogleScheduler` (lazy): Calendar `freebusy` → slots → evento con `conferenceData` (Meet) o
  con `location` y SIN Meet para `modality=onsite` → append a Google Sheets
  (`MEETING_SHEET_ID/TAB`). Descripción del evento con nombre/correo/teléfono de ambas partes.
- Config por-tenant (`app_settings.scheduling`): ventana laboral (días/horas/zona),
  `slot_minutes`, horizonte, nº de opciones — la misma ventana gobierna el auto-contacto.
- El flujo conversacional (propuesta de 2–3 slots, parsing de la elección, reintentos,
  confirmación por stage/modalidad) vive en el motor; ver [Agente.md](Agente.md) y
  [Producto.md](Producto.md).

## 4. Contratos e invariantes

- Una reunión por `(conversation_id, stage)` (unique en DB): re-agendar actualiza, no duplica.
- `compute_free_slots` nunca propone fuera de la ventana laboral del tenant.
- Los conectores no fabrican chats ni contactan: solo traen datos (el contacto es del servicio).
- Presencial (`onsite`) ⇒ evento sin Meet link — la reconciliación lo sabe (no alerta
  "reunión sin link" para onsite; falso positivo corregido).

## 5. Patrones reutilizables

- **Simulado-por-default + real-detrás-de-config**: permite demo, tests e2e y desarrollo sin
  cuentas; la implementación real se enchufa sin tocar llamadores.
- **Helpers puros para la lógica de calendario** (slots, ventanas): lo difícil de agendar es
  la aritmética de tiempo, y eso se testea sin red.
- **Dedupe por id de origen** (source_ref) y no por atributos mutables.
- **Efectos externos con registro-primero + completado posterior**: patrón general para
  cualquier API sin transacciones.

## 6. Pendientes conocidos

- Conectores reales de portales (Bumeran/LinkedIn API) — hoy fixture.
- Sheets/Calendar: manejo de cuotas/errores parciales más fino si sube el volumen.

## 7. Trazabilidad

- Tests: `test_sourcing.py`, `test_scheduling.py` (compute_free_slots, parse, simulated, motor),
  `test_contact.py` (ventana laboral + regresión del claim demo), `test_multistage.py`.
- Migraciones: `0010` (scheduling), `0011` (contactos), `0019` (stage/modality), `0023`
  (source_ref). Config: bloques sourcing y agendamiento del `.env.example`.
