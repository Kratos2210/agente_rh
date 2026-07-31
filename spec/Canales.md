# Canales — Telegram (polling/webhook), documentos y gobierno de turnos

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `channels/`, `api/telegram_bot.py`
> Specs normativos: `openspec/specs/canal-telegram/spec.md` · `openspec/specs/documentos/spec.md`

## 1. Propósito y alcance

El transporte entre el candidato y el motor: la interfaz de canal, el bot de Telegram en sus
dos modos, el enrutado multi-tenant por deep-links, la recepción de documentos y los límites
que protegen el presupuesto LLM por chat. El motor en sí está en [Agente.md](Agente.md).

## 2. Decisiones de diseño

- **Interfaz `Channel`** (`channels/base.py`) con implementaciones `telegram.py` y
  `whatsapp.py` (stub): el servicio y el motor no conocen el transporte; agregar WhatsApp es
  implementar la interfaz, no tocar el núcleo.
- **Dual-mode config-gated**: sin `TELEGRAM_WEBHOOK_URL` → **polling** (dev local, cero infra;
  admite UNA réplica por token); con URL pública → **webhook** (`POST /telegram/webhook`,
  habilita `replicas>1` + rolling deploys). El mismo Application de PTB procesa ambos: en
  webhook los updates se **encolan** en `app.update_queue` (`process_webhook_update`).
- **Secret del webhook nunca vacío**: si no se configura, se **deriva del token** del bot
  (`resolve_webhook_secret`, sha256) — el endpoint jamás queda sin validar; comparación en
  tiempo constante.
- **Gobierno de turnos ANTES de gastar LLM** (`TurnGovernor`): cooldown 2 s por chat (ráfagas
  se ignoran en silencio), tope diario 120 turnos (aviso único, luego silencio), dedupe de
  texto idéntico en 6 s (doble-tap/reintento de red). Cada mensaje cuesta llamadas LLM: el
  gate va primero.
- **El trabajo síncrono corre en hilo** (el handler PTB no bloquea el event loop); las acciones
  del dashboard que envían por el bot vivo usan `run_coroutine_threadsafe`.

## 3. Diseño e implementación

### Bot (`api/telegram_bot.py`)
- Handlers PTB: `/start` (con `context.args` → deep-link), texto, botones inline
  **Acepto / No interesado**, documentos.
- **Deep-links multi-tenant**: `t.me/<bot>?start=<vacancy_id>` — payload validado como UUID
  antes de tocar DB; vacante inexistente/cerrada → aviso sin crear candidato; conversación
  existente del thread siempre gana (sticky). `TELEGRAM_BOT_USERNAME` habilita el enlace
  copiable en el dashboard.
- **Lifecycle en el lifespan de FastAPI**: polling = updater clásico; webhook =
  `initialize()+start()` sin updater + `bot.set_webhook(url, secret_token=)`; shutdown limpia
  (`delete_webhook()` / `updater.stop()`). `GET /api/health` expone `telegram_mode`.
- **Resultado de entrega**: `_mark_delivery_result` marca fallos de envío
  (`conversations.last_delivery_failed_at`) → alerta `delivery_failed` si el candidato no
  interactuó después.

### Documentos (`channels/documents.py` + `_on_document`)
- `sanitize_filename` (anti path-traversal) + `validate_document` (solo PDF, ≤ 20 MB) ANTES de
  descargar; destino blindado dentro de `uploads/`.
- Validación de contenido (`DOCUMENT_CONTENT_CHECK_ENABLED`): extrae el texto y verifica que el
  PDF corresponda al tipo pedido (CV vs CUL, heurística + LLM); mismatch → se rechaza y se
  vuelve a pedir; fail-open ante fallo de extracción.
- El servicio persiste el binario en Postgres (≤ 5 MB) con metadata en el candidato
  (ver [Base_datos.md](Base_datos.md)).

### Notificación saliente por Telegram
La API HTTP directa (`notifications/candidate.py::post_telegram`) se usa para avisos
disparados por el dashboard (avanza/rechaza/contratado/exámenes), siempre vía outbox.

## 4. Contratos e invariantes

- **Un token de bot = un consumidor de `getUpdates`**: jamás correr dos instancias en polling
  ni hacer `setWebhook` contra un bot que otro proceso polea (le roba el registro).
- Todo update entrante pasa por el gobernador de turnos antes del motor.
- El webhook responde 404 fuera de modo webhook, 403 con secret inválido, 400 con payload
  malformado — nunca 500 por input externo.
- `channel_user_id` puede reasignarse (demo): la identidad estable de plataforma es
  `source_ref`, no el chat.

## 5. Patrones reutilizables

- **Dual-mode polling/webhook tras un flag**: desarrollo sin infra pública y producción
  multi-réplica con el mismo código; la cola interna del framework como punto de unión.
- **Secret derivado del credential existente** cuando el operador no configura uno: seguro por
  defecto sin fricción.
- **Presupuesto por usuario ANTES del modelo** (cooldown + cap diario + dedupe): tres números
  que cortan el 95 % del abuso/accidente de costo.
- **Deep-link con payload = id del recurso** validado como UUID: enrutado multi-tenant sin
  estado previo ni tabla de tokens.

## 6. Pendientes conocidos

- `channels/whatsapp.py` es stub (WhatsApp Cloud API post-MVP).
- E2E webhook contra un pod real con HTTPS público (validado con tests + smoke local; no
  contra el bot demo compartido, a propósito).

## 7. Trazabilidad

- Tests: `test_webhook.py` (9), `test_routing.py` (7), `test_documents.py` (6),
  `test_contact.py`, `test_token_redaction.py`.
- Config: bloque Telegram + gobierno de turnos del `.env.example`.
