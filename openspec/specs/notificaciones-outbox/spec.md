# notificaciones-outbox Specification

## Purpose
Entrega confiable de todo efecto hacia afuera (correos, avisos Telegram del dashboard, alertas,
trabajos diferidos) mediante una cola durable. Manual de dominio: `spec/Notificaciones.md`.

## Requirements

### Requirement: Cero fire-and-forget
Todo envío externo que pueda fallar DEBE (MUST) pasar por el outbox (`deliver(kind, payload)`);
las primitivas de transporte DEBEN (SHALL) lanzar excepción en fallo (nunca tragarlo).

#### Scenario: SMTP caído al notificar un scorecard
- **GIVEN** el servidor SMTP no responde
- **WHEN** termina una entrevista y se dispara el correo al reclutador
- **THEN** el envío queda encolado `pending` con el error registrado, y el flujo del candidato continúa

### Requirement: Reintentos con backoff y dead-letter
El drenaje DEBE (SHALL) reintentar lo vencido con backoff exponencial (1 min → 6 h) hasta
`max_attempts=6` y luego marcar `failed` (dead-letter); un dead-letter DEBE (MUST) requerir
acción humana (retry desde el dashboard) y ser visible con su motivo.

#### Scenario: Sexto intento fallido
- **GIVEN** un envío con 5 intentos fallidos
- **WHEN** el sexto también falla
- **THEN** queda `failed`, aparece en /observabilidad y el retry manual lo reencola

### Requirement: Entrega única entre réplicas
El drenaje DEBE (MUST) correr bajo el advisory lock del scheduler: dos réplicas NUNCA DEBEN
(MUST) despachar el mismo envío.

#### Scenario: Dos réplicas activas en webhook
- **GIVEN** backend con replicas:2 y outbox con pendientes
- **WHEN** ambos procesos tienen el loop del scheduler
- **THEN** solo el que posee el lock drena; el otro queda standby

### Requirement: Trabajos diferidos por la misma cola
Los trabajos pesados fuera del request (p. ej. `kb_reindex`) DEBEN (SHALL) encolarse SIN intento
en línea y con `next_attempt_at` timestamp (no NULL) para que el drenaje los tome.

#### Scenario: Editar una vacante con RAG activo
- **GIVEN** `INTERVIEW_RAG_ENABLED=true`
- **WHEN** RR.HH. actualiza la vacante
- **THEN** el request responde al instante y el reindex corre después vía outbox

### Requirement: Purga junto al candidato
La retención y el erasure DEBEN (MUST) eliminar los registros de outbox del candidato (ninguna
PII sobrevive en payloads muertos).

#### Scenario: Erasure con dead-letters pendientes
- **GIVEN** un candidato con un correo `failed` en el outbox
- **WHEN** un admin lo borra
- **THEN** el registro del outbox desaparece con él
