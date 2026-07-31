# examenes-contratacion Specification

## Purpose
El cierre del proceso: exámenes psicológico y médico, contratación y onboarding del ingresante.
Manual de dominio: `spec/Producto.md` y `spec/Notificaciones.md`.

## Requirements

### Requirement: Examen médico configurable
Con el setting por-tenant `medical_exam` activo, aprobar gerencia DEBE (SHALL) llevar a
`medical_pending` (cita → resultado); con el setting apagado DEBE (SHALL) contratar directo
(comportamiento retrocompatible).

#### Scenario: Flag médico apagado
- **GIVEN** `medical_exam.enabled=false` para el tenant
- **WHEN** RR.HH. aprueba la etapa de gerencia
- **THEN** el candidato pasa a `hired` con sus notificaciones, sin fase médica

### Requirement: Credenciales idempotentes
Reenviar credenciales/citas idénticas de un examen DEBE (MUST) responder 409; credenciales
distintas DEBEN (SHALL) enviarse y registrarse.

#### Scenario: Doble click al enviar el examen psicológico
- **GIVEN** un examen psicológico ya enviado con link+código+clave
- **WHEN** se reenvía el mismo contenido
- **THEN** la API responde 409 y no se envía correo duplicado

### Requirement: Resultado médico decide el cierre
El resultado `apto` DEBE (SHALL) llevar a `hired` con notificación por Telegram y correo de
contratación; `no_apto` DEBE (SHALL) llevar a `rejected` con notificación al candidato. Ambos
envíos DEBEN (MUST) ir por el outbox.

#### Scenario: Resultado apto
- **GIVEN** un candidato `medical_scheduled`
- **WHEN** RR.HH. registra `apto`
- **THEN** queda `hired`, recibe la felicitación por Telegram y el correo de contratación se encola

### Requirement: Onboarding el día de ingreso
Con `start_date` fijada, el barrido de onboarding DEBE (SHALL) enviar el kit de la vacante
(correo + Telegram) el día de ingreso, UNA sola vez (sello `onboarding.sent_at` ANTES de
despachar), respetando horario laboral; DEBE (SHALL) existir respaldo manual idempotente.

#### Scenario: Barrido y botón manual el mismo día
- **GIVEN** un contratado con fecha de ingreso hoy y kit configurado
- **WHEN** RR.HH. dispara el envío manual y minutos después corre el barrido
- **THEN** el kit se envía exactamente una vez

### Requirement: Estado contratado terminal
`hired` DEBE (MUST) ser terminal: el onboarding no cambia el status y los mensajes del candidato
reciben acuse sin reprocesar el flujo.

#### Scenario: Contratado escribe al bot
- **GIVEN** un candidato `hired`
- **WHEN** envía un mensaje por Telegram
- **THEN** recibe el acuse y su estado sigue `hired`
