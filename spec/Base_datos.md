# Base de datos — Supabase/Postgres: esquema, migraciones y acceso

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `db/`, `supabase/migrations/` (27)

## 1. Propósito y alcance

El modelo de datos completo (21 tablas), la estrategia de doble persistencia, el flujo de
migraciones y los patrones de acceso. La política de seguridad de datos (RLS, retención,
erasure) se detalla en [Seguridad.md](Seguridad.md).

## 2. Decisiones de diseño

- **Un Postgres, dos planos**: ① **negocio** vía cliente Supabase/PostgREST
  (`db/client.py` + `db/repositories.py`, service_role + GRANTs) y ② **estado conversacional**
  vía `DATABASE_URL` + `PostgresSaver` de LangGraph (tablas de checkpoint propias,
  `thread_id = canal:chat`). Mismo motor, caminos independientes: el checkpoint es la verdad
  del turno; el negocio es la proyección consultable.
- **Todo el CRUD en `repositories.py`**: una sola capa de acceso; los handlers no arman queries.
  Facilita los guards (p. ej. `ensure_valid_status` en `update_candidate`) y los fakes de test.
- **Supabase local (Docker) en dev, cloud en prod**: la CLI aplica `supabase/migrations/`;
  storage/analytics/vector desactivados en local (health checks fallidos en Mac Intel).
- **Contenido de documentos EN Postgres** (`candidate_documents.content_b64`, ≤5 MB por
  `DOCUMENT_DB_MAX_BYTES`): sobrevive redeploys sin object store (que está off en local);
  sobre el umbral queda solo en disco con flag `stored`.
- **RPCs para lo atómico**: `app_replace_vacancy_questions` y `app_claim_candidate_chat`
  (funciones SQL) con fallback retro-compatible en el repo — PostgREST no da transacciones
  multi-tabla.

## 3. Diseño e implementación

### Tablas (21) — propósito y escritor principal

| Tabla | Propósito (escritor) |
|---|---|
| `tenants`, `users` | Multi-tenancy + cuentas del dashboard (admin/API users) |
| `vacancies`, `vacancy_questions` | Vacante + preguntas con criterio/`cv_field`/`label` (dashboard) |
| `recruiters` | Roster RR.HH./líder/gerencia con calendario y contacto (dashboard) |
| `candidates` | Candidato: status, `source_ref`, `cv_profile`, prescreen, documentos-metadata, `consent_at`, `psych_exam`, `medical_exam`, `start_date`, `onboarding` (servicio+endpoints) |
| `conversations`, `messages`, `answers` | Conversación por thread + transcripción + respuestas evaluadas (servicio) |
| `scorecards` | Resultado final 1×conversación: semáforo, `review_required`, `prompt_version` (servicio) |
| `meetings` | Reuniones por (conversación, stage) con modalidad/ubicación/asistencia (servicio) |
| `stage_feedback` | Feedback + decisión por etapa (endpoints) |
| `candidate_documents` | CV/CUL binario en base64, unique(candidate,type) (servicio) |
| `app_settings` | Settings por-tenant, PK (tenant_id, key) (endpoints) |
| `outbox` | Cola durable de notificaciones con reintentos (outbox) |
| `audit_log` | Quién hizo qué (endpoints + MCP) |
| `state_transitions` | Historial de cambios de fase (servicio) |
| `llm_usage` | Tokens/llamadas/errores/latencia por etapa y conversación (servicio) |
| `llm_traces` | Prompt/respuesta por llamada LLM, capadas (servicio, flag) |
| `quality_metrics` | Tasas diarias del juez de calidad por tenant (barrido) |
| `http_metrics_snapshots` | Snapshot periódico de métricas HTTP (barrido; RLS sin políticas = solo service_role) |

### Migraciones (27, `supabase/migrations/`)

`0001` núcleo (7 tablas) · `0002` seed demo · `0003` GRANTs service_role · `0004` detalle del
puesto · `0005–0007` prescreening + cv_fields + labels · `0008` app_settings · `0009` inactividad
· `0010–0012` scheduling + contactos + campos vacante · `0013` auth+tenancy · `0014` outbox ·
`0015` integridad/audit/consentimiento · `0016` documentos en DB · `0017` settings por-tenant ·
`0018` RLS por tenant (16 tablas) · `0019` multi-etapa · `0020` latencia LLM · `0021`
prompt_version · `0022` cierre backlog (FKs cascade, transiciones, trigger updated_at, RPCs) ·
`0023` source_ref · `0024` llm_traces · `0025` snapshots HTTP · `0026` quality_metrics ·
`0027` médico+onboarding.

### Índices y uniques que sostienen la lógica

`candidates(status)` · `unique(candidate_id, type)` en documentos · `unique(conversation_id,
stage)` en meetings · `unique(tenant, metric, day)` en quality_metrics · índice parcial de
`source_ref` (dedupe de re-sync) · trigger `updated_at` en candidates (referencia de retención).

## 4. Contratos e invariantes

- **Nadie escribe `candidates.status` sin pasar por `ensure_valid_status`** (guard en el repo).
- El negocio **jamás** toca las tablas de checkpoint; el motor jamás toca las de negocio.
- Los deletes de candidato cascadean (documentos, outbox, trazas) — el erasure no deja huérfanos.
- **Gotcha operativo**: tras aplicar DDL por psql directo, PostgREST no ve el cambio hasta
  `NOTIFY pgrst, 'reload schema'` (la CLI `supabase migration up` recarga sola).
- **Gotcha PostgREST**: un embed to-one (unique) llega como **objeto**, no lista — los builders
  aceptan ambas formas.

## 5. Patrones reutilizables

- **Doble persistencia con proyección unidireccional**: el estado rico del agente en su
  checkpointer; una proyección plana y consultable para el producto. Nunca dos fuentes de verdad.
- **Repositorio único + fakes**: toda la suite corre sin DB real; los smokes en vivo cubren lo
  que los fakes no ven (¡el embed objeto-vs-lista salió en vivo, no en tests!).
- **Embedded selects contra N+1**: una consulta con `count=exact` para listado+total; guards de
  test que fallan si alguien reintroduce el loop.
- **Migraciones chicas y numeradas con seed demo**: cada feature con su DDL + backfill; el
  seed mantiene una demo end-to-end reproducible.

## 6. Pendientes conocidos

- Algunas migraciones (0019–0027) se aplicaron por psql directo y quedaron fuera del registro
  del CLI (`supabase migration up`) — reconciliar el historial si se recrea el entorno.
- Object storage (S3/Supabase Storage) para documentos a escala.

## 7. Trazabilidad

- Tests: `test_storage.py`, `test_listing.py`, `test_backlog_close.py` (RPCs+fallback),
  `test_estados.py`, `test_tenant_settings.py`.
- Guía de datos: sección "Base de datos" de `/guia`. Cliente: `db/client.py` (service_role).
