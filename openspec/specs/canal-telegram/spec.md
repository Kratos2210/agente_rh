# canal-telegram Specification

## Purpose
El transporte Telegram entre candidato y motor: modos de operación, enrutado multi-tenant y
protección del presupuesto por chat. Manual de dominio: `spec/Canales.md`.

## Requirements

### Requirement: Dual-mode polling/webhook
Sin `TELEGRAM_WEBHOOK_URL` el bot DEBE (SHALL) operar en polling (una sola réplica por token);
con URL pública DEBE (SHALL) registrar webhook en `{URL}/telegram/webhook` y habilitar
múltiples réplicas. `GET /api/health` DEBE (SHALL) exponer el modo activo.

#### Scenario: Despliegue prod con webhook
- **GIVEN** `TELEGRAM_WEBHOOK_URL` configurada
- **WHEN** arranca el backend
- **THEN** registra el webhook con secret y no consume `getUpdates`

### Requirement: Webhook siempre validado
El endpoint del webhook DEBE (MUST) validar `X-Telegram-Bot-Api-Secret-Token` en tiempo
constante; con el secret sin configurar DEBE (SHALL) derivarse del token del bot (nunca queda
sin validar). Respuestas: 404 fuera de modo webhook, 403 secret inválido, 400 payload
malformado — nunca 500 por input externo.

#### Scenario: POST con secret incorrecto
- **GIVEN** el bot en modo webhook
- **WHEN** llega un POST con header inválido
- **THEN** responde 403 sin procesar el update

### Requirement: Enrutado multi-tenant por deep-link
El payload de `t.me/<bot>?start=<vacancy_id>` DEBE (MUST) validarse como UUID ANTES de tocar la
base de datos; vacante inexistente o cerrada DEBE (SHALL) avisar SIN crear candidato; una
conversación existente del thread DEBE (MUST) ganar siempre sobre el deep-link (sticky).

#### Scenario: Deep-link con basura
- **GIVEN** un `/start` con payload no-UUID
- **WHEN** el bot lo procesa
- **THEN** no hay consulta a DB con ese valor ni candidato creado

### Requirement: Gobierno de turnos antes del LLM
Cada chat DEBE (SHALL) estar gobernado ANTES de gastar LLM: cooldown entre mensajes
(`BOT_TURN_COOLDOWN_SECONDS`), tope diario (`BOT_MAX_TURNS_PER_DAY`, aviso único al alcanzarlo)
y dedupe de texto idéntico (`BOT_TURN_DEDUP_SECONDS`).

#### Scenario: Ráfaga de doble-tap
- **GIVEN** un candidato que envía el mismo texto dos veces en 3 segundos
- **WHEN** el bot recibe el segundo mensaje
- **THEN** se ignora sin consumir turno ni tokens

### Requirement: Fallos de entrega visibles
Un fallo al enviar por Telegram DEBE (SHALL) registrarse (`last_delivery_failed_at`) y generar
alerta operativa solo si el candidato no interactuó después del fallo.

#### Scenario: Candidato bloqueó al bot
- **GIVEN** un recordatorio que Telegram rechaza
- **WHEN** el candidato no vuelve a escribir
- **THEN** aparece la alerta `delivery_failed` en observabilidad
