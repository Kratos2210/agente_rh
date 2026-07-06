# Notificaciones — Outbox durable, correos y avisos al candidato

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `notifications/`

## 1. Propósito y alcance

Todo efecto "hacia afuera" que no es parte del turno conversacional: correos al reclutador y al
candidato, avisos Telegram disparados por el dashboard, alertas operativas y trabajos diferidos
(reindex de la KB). El principio: **cero fire-and-forget** — todo envío que puede fallar queda
en una cola durable con reintentos.

## 2. Decisiones de diseño

- **Outbox en Postgres** (`notifications/outbox.py` + tabla `outbox`): `deliver(kind, payload)`
  intenta el envío en línea y **encola en fallo**; el drain del scheduler (bajo el advisory
  lock) reintenta lo vencido con **backoff exponencial 1 min → 6 h** y marca `failed`
  (**dead-letter**) al agotar `max_attempts=6`. Antes de esto, un SMTP caído perdía scorecards
  en silencio (hallazgo de auditoría).
- **Handlers que LANZAN**: las primitivas de envío (`email.send_email`,
  `candidate.post_telegram`) levantan excepción en fallo — el outbox necesita saber que falló;
  las variantes "best-effort silencioso" se eliminaron.
- **Builders puros separados del transporte**: `build_scorecard_email`, `build_meeting_email`,
  `build_psych_exam_email`, `build_hired_email`, `build_onboarding_email` (HTML+texto) se
  testean sin SMTP.
- **Kinds genéricos para operación**: `ops_email` (presupuesto, SLAs, calidad) con destinatario
  resuelto por `_alert_recipient` (correo del tenant → fallback `OPS_ALERT_EMAIL` de equipo).
- **Trabajos no-notificación también van por outbox**: `kb_reindex` se encola SIN intento en
  línea (torch nunca corre en el request path). Gotcha: `next_attempt_at` debe ser timestamp
  (NULL no matchea el `lte` del drain).

## 3. Diseño e implementación

### Ciclo de vida de un envío
`deliver(kind, payload)` → intento en línea → ok = `sent` / fallo = fila `pending` con
`attempts`, `next_attempt_at`, `last_error` → `drain(settings, now)` en cada tick reintenta lo
vencido (`backoff_seconds`/`next_state_after_failure`, helpers puros) → `sent` o `failed`.
Dead-letters: visibles en `/observabilidad` con motivo, reintentables a mano
(`POST /api/outbox/{id}/retry` → status `pending` + `next_attempt_at=now`; 409 si ya `sent`).

### Kinds registrados

| Kind | Efecto |
|---|---|
| `scorecard_email` | Scorecard HTML al reclutador al terminar la entrevista |
| `meeting_email` | Confirmación de reunión a candidato + reclutador (con teléfonos) |
| `psych_exam_email` / `medical_exam_email` | Credenciales/cita al correo del candidato (del CV) |
| `hired_email` | Correo de contratación |
| `onboarding_email` | Kit de onboarding el día de ingreso |
| `candidate_telegram` (vía `post_telegram`) | Avisos avanza/rechaza/contratado/exámenes por Telegram |
| `ops_email` | Alertas de presupuesto/SLA/calidad al correo de operación |
| `kb_reindex` | Reindexación de la KB de la vacante (ver [RAG.md](RAG.md)) |

### Reconciliación (vigilancia de lo que el outbox no ve)
El barrido `_reconciliation_sweep` alerta: dead-letters acumulados, reuniones virtuales sin
`meet_link`, coordinaciones de horario estancadas sin reunión, divergencia motor↔negocio,
entregas Telegram fallidas sin interacción posterior. Ver [Observabilidad.md](Observabilidad.md).

## 4. Contratos e invariantes

- Ningún call site envía correo/Telegram directo: **siempre** `deliver(...)` (o una primitiva
  raising dentro del outbox).
- El drain corre bajo el advisory lock: no hay doble entrega entre réplicas.
- Un `failed` es terminal hasta acción humana (retry manual) — el sistema no reintenta infinito.
- El erasure/retención purga el outbox del candidato (FK cascade + `delete_outbox_by_candidate`):
  no queda PII en payloads muertos.

## 5. Patrones reutilizables

- **Outbox pattern con intento en línea**: latencia de camino feliz + durabilidad de cola, sin
  broker externo (Postgres alcanza).
- **Backoff exponencial con dead-letter y retry humano**: reintentos automáticos acotados,
  visibilidad del resto.
- **Builders puros + transporte raising**: el contenido se testea unitario; el fallo es señal,
  no silencio.
- **Cola genérica para trabajos pesados fuera del request** (reindex): el mismo outbox sirve de
  job queue mínima.

## 6. Pendientes conocidos

- Plantillas de correo con branding configurable por tenant (hoy estilo único).
- Webhooks/Slack como destino de `ops_email` (hoy solo SMTP).

## 7. Trazabilidad

- Tests: `test_outbox.py` (9), `test_email_escaping.py`, `test_kb_reindex.py`,
  `test_medical.py`/`test_onboarding.py` (envíos por outbox), `test_observability.py` (retry).
- Migraciones: `0014` (outbox), `0022` (FK cascade candidate). UI: sección outbox en
  `/observabilidad`.
