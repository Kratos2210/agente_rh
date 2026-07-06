# Configuración — Settings, flags y configuración por-tenant

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Fuente: `core/config.py` (~98 settings), `.env.example`

## 1. Propósito y alcance

Cómo se configura el sistema: settings de proceso (env vars vía pydantic-settings), settings de
negocio por-tenant (tabla `app_settings`) y el perfil de producción. Los valores concretos de
cada dominio se detallan en su spec.

## 2. Decisiones de diseño

- **pydantic-settings con `extra="ignore"`**: settings tipados con defaults seguros; una env var
  mal escrita **se ignora en silencio** — esto causó un bug real (el ConfigMap de K8s usaba
  `APP_ENV`/`OPENAI_BASE_URL`, nombres que pydantic no lee, y producción corría como
  development). Mitigación: los nombres canónicos viven en `core/config.py` y los manifests se
  revisan contra ellos.
- **Dos capas de configuración**: lo que define el *proceso* (credenciales, endpoints, flags de
  capacidad) va en `.env`; lo que define el *negocio por empresa* (horarios, umbrales, correos
  de alerta, precios) va en `app_settings` por-tenant con fallback a `_DEFAULT_*` en código.
- **Convención "apagado por defecto"**: toda capacidad con costo, riesgo o dependencia externa
  nace default-off (MCP, trazas LLM, caché de dudas, examen médico, webhook, Sentry, Phoenix,
  retención, SLAs, calidad continua). Encenderlas es una decisión explícita (el overlay prod de
  K8s enciende el perfil recomendado).

## 3. Diseño e implementación

### Bloques del `.env` (14)

| Bloque | Variables clave |
|---|---|
| LLM (API compatible OpenAI) | `OPENAI_API_BASE/KEY/MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_CHEAP_MODEL`, `LLM_CHEAP_STAGES`, `ALLOW_PRIVATE_LLM_ENDPOINTS`, `INTERVIEW_ANSWER_CACHE_*` |
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DATABASE_URL` (checkpointer) |
| Auth dashboard | `JWT_SECRET`, `JWT_SECRET_PREVIOUS` (rotación), `JWT_EXPIRE_MINUTES`, `ADMIN_*` (bootstrap), `CORS_ORIGINS` |
| Servidor MCP | `MCP_ENABLED` |
| Pipeline LLM | `INTERVIEW_RAG_ENABLED`, `COMPANY_KB_COLLECTION` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (deep-links), `TELEGRAM_WEBHOOK_URL/SECRET`, `TELEGRAM_ALLOWED_USERS` |
| SMTP / reporte | `SMTP_*`, `RECRUITER_EMAIL`, `OPS_ALERT_EMAIL` (fallback de alertas) |
| Gobierno de turnos | `BOT_TURN_COOLDOWN_SECONDS`, `BOT_MAX_TURNS_PER_DAY`, `BOT_TURN_DEDUP_SECONDS` |
| Documentos / limpieza | `DOCUMENT_DB_MAX_BYTES`, `DOCUMENT_CONTENT_CHECK_ENABLED`, `CHECKPOINT_RETENTION_DAYS` |
| Entrevista / evaluación | `INTERVIEW_MAX_FOLLOW_UPS`, `SEMAPHORE_GREEN_MIN/YELLOW_MIN` |
| Sourcing / contacto | `SOURCING_PROVIDER`, `PRESCREEN_PASS_MIN`, `AUTO_CONTACT_ON_PASS`, `DEMO_TELEGRAM_CHAT_ID` |
| Agendamiento | `SCHEDULING_PROVIDER`, `GOOGLE_OAUTH_CLIENT_PATH/TOKEN_PATH`, `GOOGLE_CREDENTIALS_PATH`, `MEETING_SHEET_ID/TAB` |
| Observabilidad | `LANGSMITH_*` (+`HIDE_INPUTS/OUTPUTS`), `LLM_TRACE_ENABLED/MAX_CHARS`, `PHOENIX_*`, `LOG_JSON`, `SENTRY_DSN/TRACES_SAMPLE_RATE`, `HTTP_SNAPSHOT_MINUTES/RETENTION_DAYS` |
| RAG | `PERSIST_DIRECTORY`, `EMBEDDING_MODEL`, `RERANKER`, `CROSS_ENCODER_MODEL`, `CHUNK_SIZE/OVERLAP`, `RETRIEVE_K`, `FINAL_K` |

### Settings por-tenant (`app_settings`, PK compuesta `(tenant_id, key)`)

`repositories.get_app_setting(key, default, tenant_id)` / `set_app_setting(...)` (upsert).
Un tenant sin fila cae al `_DEFAULT_*` de `api/runtime.py`. Keys: `auto_contact`, `inactivity`,
`scheduling` (ventana laboral + provider + slots), `retention`, `llm_pricing`, `llm_budget`,
`sla_alerts`, `quality_alerts`, `medical_exam`, `llm_provider` (BYOK, key cifrada). Cada una con
endpoints `GET/PUT /api/settings/<key>` (PUT = admin, auditado).

### Perfil de producción

- `ENVIRONMENT=production` activa `assert_secure_config` (bloquea `JWT_SECRET`/
  `JWT_SECRET_PREVIOUS`/`ADMIN_PASSWORD` débiles o default → `RuntimeError` al arrancar).
- El overlay `despliegue/k8s/overlays/prod` fija el perfil "todo encendido" (trazas, caché,
  routing barato, webhook) — ver [Despliegue.md](Despliegue.md) y `tests/test_prod_profile.py`.

## 4. Contratos e invariantes

- Ningún setting de negocio se lee "una vez al arrancar": los barridos releen la config por
  tenant **en cada tick** (cambios en caliente sin redeploy).
- `get_app_setting` siempre devuelve algo utilizable (fila del tenant o default de código):
  el llamador no maneja "no configurado".
- El `.env.example` es el contrato público: toda variable nueva se documenta ahí con su porqué
  y su default.

## 5. Patrones reutilizables

- **Default en código + override en DB por tenant**: evita migraciones para settings nuevos y
  da multi-tenancy real de configuración (patrón de la migración `0017`).
- **Flags de capacidad separados de credenciales**: `X_ENABLED` + `X_DSN/KEY`; sin credencial la
  feature es no-op aunque esté enabled (best-effort, nunca tumba el arranque).
- **Gate de arranque para producción**: un `assert_secure_config` que convierte configuración
  insegura en fallo de deploy, no en incidente.
- **Documentar el nombre canónico de cada env var** donde se consume: con `extra="ignore"`,
  el typo es invisible en runtime.

## 6. Pendientes conocidos

- Migrar secretos del `.env` plano a un gestor (External Secrets/Doppler/Vault) — scaffolding
  listo en `despliegue/k8s/secret-manager/`, requiere cuenta externa.
- Validación automática ConfigMap↔`core/config.py` (hoy revisión manual).

## 7. Trazabilidad

- Tests: `test_config.py`, `test_secrets.py` (12: gate dev/prod + rotación),
  `test_tenant_settings.py`, `test_prod_profile.py`.
- Migraciones: `0008` (app_settings), `0017` (por-tenant).
- Runbook: `docs/gestion_secretos.md`.
