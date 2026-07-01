# Auditoría de seguridad de integraciones externas — `agente_rh`

**Fecha:** 2026-07-01 · **Alcance:** todos los puntos de contacto del agente con
sistemas externos (Supabase/Postgres, LLM Groq, Telegram, SMTP, Google Calendar/Sheets,
conectores de sourcing) evaluados contra el marco de *integración segura de agentes*.

> Marco: mínimo privilegio · adaptadores como capa de control · gestión de credenciales ·
> validación/sanitización · manejo de errores · observabilidad/auditoría · escalabilidad.

## Resumen ejecutivo

El diseño ya aplica **muy bien** el patrón de *capa de adaptación contractual*: el LLM nunca
construye llamadas directas a APIs; solo produce texto/JSON que código determinista parsea y
ejecuta a través de adaptadores (`Protocol` + factory). El manejo de errores (outbox durable con
backoff + dead-letter + reconciliación) y la observabilidad/auditoría (audit_log, llm_usage,
panel) son maduros. Las brechas concretas están en **mínimo privilegio a nivel de base de datos**
y en una **fuga de credencial en logs** (confirmada).

| # | Dimensión | Estado | Severidad brecha |
|---|-----------|--------|------------------|
| F1 | Credencial (token Telegram) en logs | ✅ **Corregido** (código) · rotar token pendiente | **ALTA** |
| F2 | Mínimo privilegio en DB (service_role + sin RLS) | ✅ **Mitigado** (guard-test + RLS latente) | **ALTA** |
| F3 | Sanitización de salida (HTML de correos sin escapar) | ✅ **Corregido** | MEDIA |
| F4 | Scopes de Google demasiado amplios | ✅ **Corregido** | BAJA |
| F5 | Secretos en `.env` plano, sin rotación | ✅ **Endurecido** (rotación JWT + runbook) | BAJA |

Fortalezas (sin acción): adaptadores contractuales, manejo de errores, observabilidad, validación
de entrada (documentos + anti-inyección de prompt), RBAC + aislamiento por tenant a nivel de app.

---

## Hallazgos

### F1 · Token del bot de Telegram expuesto en logs — **ALTA (confirmado)**
`notifications/candidate.post_telegram` llama a
`https://api.telegram.org/bot<TOKEN>/sendMessage`. Como `httpx` registra cada request en nivel
INFO y `src/logging_config.py` usa `logging.basicConfig(level=INFO)` sobre el root, el **token
completo** (`bot<id>:<secret>`) queda escrito en los logs. Verificado en `backend.log`:
`api.telegram.org/bot8203186985:AAGGa…`. Cualquiera con acceso a logs obtiene control total del bot.
- **Arreglo:** subir el logger `httpx`/`httpcore` a WARNING
  (`logging.getLogger("httpx").setLevel(WARNING)` en `logging_config.py`), y/o usar la API de PTB
  (que no imprime el token) en vez de httpx directo para el envío. Rotar el token tras el fix.
- **Estado (2026-07-01 — corregido en código):**
  1. `src/logging_config.setup_logging` sube `httpx`/`httpcore` a **WARNING** → dejan de loguear la
     URL (con token) en INFO. Cubre también el httpx interno de python-telegram-bot.
  2. **Fuga residual cerrada:** el mensaje de `httpx.HTTPStatusError` lleva la URL —y el token— y
     terminaba en `logger.exception` (`candidate.send_text`) y en `outbox.last_error` **persistido en
     DB** (`outbox.deliver`/`drain`). `notifications.candidate.post_telegram` ahora captura los errores
     de httpx y **re-lanza saneado** (`RuntimeError` con `redact_token()` + `from None`), único punto de
     estrangulamiento de todas las rutas de envío a Telegram (directo + outbox).
  3. Tests: `tests/test_token_redaction.py` (4) — loggers en WARNING, redacción, y no-fuga en el
     error re-lanzado ni en `send_text`. **116/116 verde.**
  - **PENDIENTE (manual, operativo):** **rotar el token** vía BotFather (`/revoke`) y actualizar
    `TELEGRAM_BOT_TOKEN` en el `.env`, ya que el token actual quedó escrito en `backend.log` histórico.

### F2 · Mínimo privilegio ausente en la capa de datos — **ALTA**
`db/client.py` usa la **service_role key**, que **salta RLS** y tiene acceso total a todas las
tablas de todos los tenants. El aislamiento multi-empresa se aplica **solo en código de aplicación**
(`_require_vacancy_in_tenant` / `_require_candidate_in_tenant`). Un solo endpoint que olvide el
guard → fuga cross-tenant. Contradice "permisos estrictamente limitados a las operaciones necesarias".
- **Arreglo (defensa en profundidad):** activar **RLS** en las tablas de negocio con políticas por
  `tenant_id`, y operar con una clave/rol cuyo `tenant_id` venga del JWT (Postgres `SET LOCAL
  request.jwt.claims`), de modo que la propia DB niegue el acceso cross-tenant aunque falle el guard
  de aplicación. Mínimo viable: auditar que **los 32 endpoints** pasan por un guard de tenant.
- **Estado (2026-07-01 — mitigado):** dos capas.
  1. **Blindaje de la capa de app (invariante en CI):** `tests/test_tenant_guards.py` recorre TODAS las
     rutas de FastAPI e impone, sin excepciones silenciosas, que (a) toda ruta `/api/*` fuera de la
     allowlist pública (`/api/health`, `/api/auth/login`) resuelve el usuario del token
     (`get_current_user`/`require_role`), y (b) toda ruta que carga un recurso por id de la URL
     (`{vacancy_id}`/`{candidate_id}`/`{recruiter_id}`/`{outbox_id}`) pasa por un guard de tenant
     (`_require_*_in_tenant`) o compara `tenant_id`. Si un endpoint futuro olvida el guard, CI falla.
     Verificado: la cobertura actual pasa (todos los endpoints con recurso por id ya aíslan por tenant).
  2. **RLS latente (defensa en profundidad en la DB):** migración `0018_rls_tenant_isolation.sql` activa
     **RLS** + política `tenant_isolation` (FOR ALL, roles `anon`/`authenticated`) en las **16 tablas de
     negocio**. El tenant "actual" se lee del claim `tenant_id` del JWT (`app_current_tenant()`, con guard
     de cadena vacía para requests anónimos de PostgREST); tablas hijas suben al tenant por funciones
     `SECURITY DEFINER` (`app_vacancy_tenant`/`app_conversation_tenant`/`app_candidate_tenant`) para evitar
     recursión de RLS. **Sigue latente** porque el backend usa la **service_role key** (rol `BYPASSRLS`):
     el aislamiento operativo lo hace la capa de app; RLS protege ante fugas de la anon key o clientes
     directos futuros. Para que RLS aplique al backend habría que dejar service_role y setear
     `request.jwt.claims.tenant_id` por request (cambio mayor, no hecho). **Verificado en vivo** (Supabase
     local): `service_role` ve todas las filas (backend intacto); un rol no-bypass con claim tenant=A ve
     **solo** las filas de A (no las de B); claim vacío → 0 filas sin error. **122/122 tests verde.**

### F3 · Salida sin sanitizar en los correos HTML — **MEDIA**
`notifications/email.py` interpola con f-strings el nombre del candidato, la vacante y las
**justificaciones generadas por el LLM** directamente en el HTML del correo, sin escapar. Un valor
con `<`, `>` o markup rompe/inyecta el HTML del correo (los clientes de correo renderizan HTML).
El principio pide "filtrar y normalizar la respuesta" antes de reusarla.
- **Arreglo:** escapar con `html.escape()` todo dato dinámico interpolado en las plantillas HTML
  (`_render_html`, `build_meeting_email`).
- **Estado (2026-07-01 — corregido):** `notifications/email.py` escapa con `_esc` (=`html.escape`,
  `quote=True`) todos los valores dinámicos del HTML: en `_render_html` (nombre, vacante, total,
  resumen, recomendación, y por criterio el nombre + la **justificación del LLM**) y en el HTML de
  `build_meeting_email` (título, fecha, nombre, correos/teléfonos de ambos y el `meet_link`, que va en
  `href` — al escapar las comillas no puede romper el atributo). Las versiones de **texto plano** no se
  escapan (no se interpretan como markup). Tests: `tests/test_email_escaping.py` (2). **118/118 verde.**

### F4 · Scopes de Google más amplios de lo necesario — **BAJA**
`integrations/scheduling.GoogleScheduler._SCOPES` pide `calendar` + `spreadsheets` (lectura/escritura
totales). Para el caso de uso bastan `calendar.events` y (si se limita el Sheet) `drive.file`.
- **Arreglo:** reducir a los scopes mínimos; los permisos deben ser "por rol, no por sistema completo".
- **Estado (2026-07-01 — corregido):** `GoogleScheduler._SCOPES` (y `scripts/google_oauth.py`) pasan del
  scope total de Calendar a los granulares `calendar.events` (crear evento + Meet) + `calendar.freebusy`
  (leer disponibilidad). Sheets se mantiene en `spreadsheets`: no existe scope "por hoja" y `drive.file`
  solo cubre archivos creados/abiertos por la app, no una hoja pre-compartida externamente. Reducir scopes
  **invalida tokens OAuth previos** → re-correr `scripts/google_oauth.py`. Test: `test_google_scopes_are_minimal`.
  (Google está en modo **simulado** por defecto; el cambio aplica cuando `scheduling_provider=google`.)

### F5 · Secretos en `.env` plano, sin rotación — **BAJA (aceptable en MVP)**
Los secretos viven en `.env` (correcto: fuera del código y ya en `.gitignore`), pero sin gestor de
secretos ni rotación. `assert_secure_config` ya bloquea `JWT_SECRET`/`admin_password` por defecto en
producción — bien. Para prod real: mover a un secret manager y establecer rotación periódica.
- **Estado (2026-07-01 — endurecido):** tres frentes.
  1. **Rotación grácil de JWT:** nuevo `jwt_secret_previous` (CSV, `src/config.py` + `.env.example`).
     Se **firma** siempre con `JWT_SECRET`; al **validar** se aceptan el actual + los retirados
     (`api.auth.accepted_jwt_secrets`/`decode_access_token`), así se rota el secreto de firma sin cerrar
     las sesiones vivas durante la ventana ≈ `JWT_EXPIRE_MINUTES`. La expiración es definitiva (no se
     sigue probando otros secretos si la firma valida pero el token expiró).
  2. **`assert_secure_config` ampliado:** además de `JWT_SECRET`/`ADMIN_PASSWORD` default, en producción
     ahora bloquea también secretos de rotación (`JWT_SECRET_PREVIOUS`) default/cortos y `ADMIN_PASSWORD`
     < 12 caracteres. Cubierto por `tests/test_secrets.py` (12) — el gate no tenía tests hasta ahora.
  3. **Runbook:** `docs/gestion_secretos.md` — inventario de secretos (radio de impacto), procedimiento
     de rotación por secreto (JWT grácil + emergencia, service key, DB, Telegram, SMTP, Google, admin),
     cadencia recomendada y camino a un secret manager (Doppler/Vault/AWS-GCP/Supabase Vault) para prod.
  **134/134 tests verde.** Queda como endurecimiento pre-producción real (no MVP) migrar a un gestor.

---

## Fortalezas verificadas (cumplen el marco)

- **Adaptadores como capa de control:** `SourcingConnector`, `SchedulingBackend`, `Channel`, `LLM`
  y los handlers del outbox son contratos `Protocol` + factory. El LLM devuelve texto/JSON que
  `parse_json_object` / `parse_slot_choice` interpretan por clave — el modelo **no** emite comandos
  arbitrarios. Comunicación contractual, no libre.
- **Manejo de errores:** outbox durable (backoff exponencial 1m→6h, dead-letter a los 6 intentos),
  `_reconciliation_sweep`, timeouts + `max_retries` del LLM, `low_confidence`/`review_required`
  (escalamiento a humano), degradación con gracia (SMTP/Sheets ausentes no tumban el flujo).
- **Observabilidad/auditoría:** `audit_log` (quién/qué/cuándo), `llm_usage` (tokens por etapa),
  estado del outbox, panel `/observabilidad`, alertas de reconciliación.
- **Validación de entrada:** documentos (`channels/documents.py`: solo PDF, ≤20 MB, anti path-traversal
  con confinamiento en `uploads/`), respuestas del candidato (`sanitize_answer_for_prompt` +
  delimitadores anti-inyección + `is_meaningful_answer`), cuerpos de API (Pydantic).
- **Autz de aplicación:** RBAC jerárquico (viewer<recruiter<admin) + aislamiento por tenant vía JWT;
  sin logging directo de secretos en nuestro código (solo la fuga transitiva de httpx, F1).

## Plan de remediación sugerido (orden)
1. ~~**F1** (rápido, alto impacto): silenciar httpx en logs + rotar token.~~ ✅ Código corregido
   (silenciado httpx + fuga residual de la excepción cerrada en `post_telegram`); **rotar token: pendiente manual**.
2. ~~**F3** (rápido): `html.escape` en plantillas de correo.~~ ✅ Corregido (escape en ambas plantillas HTML).
3. ~~**F2** (mayor): RLS por tenant + auditoría de guards en los 32 endpoints.~~ ✅ Mitigado: guard-test
   automatizado en CI (`test_tenant_guards.py`) + RLS latente por tenant (`0018`, 16 tablas). Falta
   (opcional, mayor): RLS efectivo sobre el backend (dejar service_role + `request.jwt.claims` por request).
4. ~~**F4**~~ ✅ Corregido (scopes granulares Calendar) · ~~**F5**~~ ✅ Endurecido (rotación grácil de JWT +
   `assert_secure_config` ampliado con tests + runbook `docs/gestion_secretos.md`). Pendiente pre-prod real:
   migrar los secretos a un gestor.

**Todos los hallazgos (F1–F5) están corregidos/mitigados/endurecidos.** Pendientes operativos/opcionales:
RLS *efectivo* sobre el backend (F2, cambio mayor) y migración a un secret manager en producción (F5).
