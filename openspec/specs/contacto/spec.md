# contacto Specification

## Purpose
Primer contacto con el candidato apto por Telegram (saludo + botones Acepto / No interesado),
manual o automático. Manual de dominio: `spec/Producto.md` (flujo) y `spec/Canales.md`.

## Requirements

### Requirement: Contacto solo desde estado apto
El contacto DEBE (SHALL) permitirse únicamente desde `prescreen_passed`; desde cualquier otro
estado la API DEBE (MUST) responder 409.

#### Scenario: Contactar a un rechazado por CV
- **GIVEN** un candidato en `prescreen_rejected`
- **WHEN** RR.HH. dispara `POST /api/candidates/{id}/contact`
- **THEN** la API responde 409 y el estado no cambia

### Requirement: Ventana laboral por tenant
Todo contacto (manual, automático al pasar el gate, o programado) DEBE (SHALL) respetar la
ventana laboral del tenant (días/horas/zona de `app_settings.scheduling`); fuera de ventana el
candidato DEBE (SHALL) quedar `prescreen_passed` diferido, y un barrido DEBE (SHALL) contactarlo
al reabrir la ventana.

#### Scenario: Sync fuera de horario laboral
- **GIVEN** un apto nuevo a las 23:00 hora del tenant con auto-contacto activo
- **WHEN** corre el sync
- **THEN** no se envía saludo y el candidato queda `prescreen_passed`
- **AND** dentro del horario siguiente el barrido lo contacta y lo marca `invited`

### Requirement: Idempotencia del contacto
El contacto DEBE (SHALL) ser idempotente: un candidato ya `invited` (o más avanzado) NO DEBE
(MUST) recibir un segundo saludo por reintentos, re-syncs ni barridos.

#### Scenario: Doble click en Contactar
- **GIVEN** una candidata recién marcada `invited`
- **WHEN** se vuelve a disparar el contacto
- **THEN** no se envía mensaje nuevo y la respuesta indica el estado actual

### Requirement: Auto-contacto configurable
El auto-contacto al pasar el gate DEBE (SHALL) estar gobernado por `AUTO_CONTACT_ON_PASS`
(reversible por configuración); apagado, los aptos esperan el botón manual del dashboard.

#### Scenario: Flag apagado
- **GIVEN** `AUTO_CONTACT_ON_PASS=false`
- **WHEN** un candidato pasa el gate durante el sync
- **THEN** queda `prescreen_passed` y solo el botón del dashboard (o el barrido programado) lo contacta
