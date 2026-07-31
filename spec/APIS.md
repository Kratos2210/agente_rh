# APIs — Backend FastAPI (67 endpoints)

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `api/main.py`, `api/routes/`

## 1. Propósito y alcance

La superficie HTTP del sistema: 67 endpoints (4 en `main.py` + 63 en 8 routers), sus
convenciones de auth, tenancy, paginación, idempotencia y errores. El servidor MCP (superficie
paralela) está en [MCP.md](MCP.md).

## 2. Decisiones de diseño

- **Routers por dominio** (`api/routes/{vacancies,candidates,recruiters,settings,observability,
  users,costs,onboarding}.py`): `api/main.py` quedó en ~240 líneas (lifespan + login +
  ensamblaje + re-exports de compatibilidad para tests que parchean `main.repo`).
- **Auth y tenancy en dependencias, no en cada handler**: `get_current_user`/`require_role`
  (JWT) + guards `_require_vacancy_in_tenant`/`_require_candidate_in_tenant`/… en `api/deps.py`.
  Un **test estructural** recorre todas las rutas y falla si un endpoint futuro olvida el patrón.
- **El webhook de Telegram vive FUERA de `/api/*`** (`POST /telegram/webhook`): no exige JWT
  (Telegram no lo tiene); se autentica con el header `X-Telegram-Bot-Api-Secret-Token`.
- **Errores humanos**: los handlers devuelven `detail` legible; el frontend lo muestra tal cual.
- **Toda mutación sensible se audita** (`_audit` → tabla `audit_log`, best-effort: no rompe la
  acción).

## 3. Diseño e implementación

### Endpoints por dominio (método · ruta · rol mínimo)

**Núcleo (`api/main.py`)**: `POST /telegram/webhook` (secret header) · `GET /api/health`
(público; expone `telegram_mode` off/polling/webhook) · `POST /api/auth/login` (público,
rate-limited 5/min/IP) · `GET /api/auth/me`.

**Vacantes (8)**: `GET/POST /api/vacancies` · `GET/PUT /api/vacancies/{id}` ·
`PUT .../onboarding-kit` · `GET .../candidates` (paginado+búsqueda) ·
`POST .../sync-applicants` (recruiter; rate-limited 2/min/tenant) · `GET .../metrics`.

**Candidatos (17)**: `GET /api/candidates` (pipeline global paginado) · `GET /api/metrics` ·
`GET /api/candidates/{id}` (detalle: scorecard, transcripción, reuniones, feedback, exámenes,
transiciones) · `GET .../documents/{tipo}` (PDF desde DB, fallback disco) · `POST .../contact`
(409 si no `prescreen_passed`) · `POST .../decision` (advance→agenda / reject→notifica) ·
`GET .../meeting` · `GET .../meetings` · `POST .../psych-exam` (409 idempotente) ·
`POST .../medical-exam` · `POST .../medical-result` (apto→hired / no_apto→rejected) ·
`POST .../start-date` · `POST .../onboarding` (kit manual, idempotente) · `POST .../attendance`
(attended/no_show) · `POST .../advance-stage` (feedback+decisión por etapa) ·
`DELETE /api/candidates/{id}` (admin, erasure) · `GET .../traces` (admin, trazas LLM).

**Reclutadores (3)**: `GET/POST /api/recruiters` · `PUT /api/recruiters/{id}`.

**Usuarios (3, admin)**: `GET/POST /api/users` · `PATCH /api/users/{id}` (anti auto-bloqueo).

**Settings (24)**: pares GET/PUT por-tenant para `scheduling`, `auto-contact`, `inactivity`,
`retention`, `medical-exam`, `llm-pricing`, `llm-budget`, `sla-alerts`, `quality-alerts`,
`llm-provider` (+ `GET .../catalog`, `POST .../test`). GET = admin o recruiter según el caso;
PUT = admin, auditado, con validación 422.

**Observabilidad (6, admin)**: `GET /api/audit` · `GET /api/ops/alerts` · `GET /api/ops/quality`
· `GET /api/ops/http-metrics` · `GET /api/outbox` · `POST /api/outbox/{id}/retry`.

**Costos (1)**: `GET /api/costs` (reporte paginado). **Onboarding (1)**: `GET /api/onboarding`.

### Convenciones

- **Auth**: Bearer JWT en todo `/api/*` salvo allowlist (`/api/health`, `/api/auth/login`).
  Roles jerárquicos `viewer < recruiter < admin`.
- **Paginación/búsqueda**: listados de candidatos aceptan `q` (ilike escapado), `limit/offset`
  (clamp 1–500, default 100) y responden `{items, total, limit, offset}`.
- **Sin N+1**: listados con embedded selects de PostgREST en UNA consulta
  (`repo.list_candidate_rows`); hay guards de test que truenan si un listado recae en la ruta
  vieja.
- **Idempotencia**: acciones repetibles → 409 con detail claro (contacto fuera de estado,
  credenciales duplicadas) o no-op (kit ya enviado).
- **Rate limits**: login 5/min/IP, sync 2/min/tenant (`api/ratelimit.py::SlidingWindowLimiter`,
  puro, por proceso) → 429.
- **Trazabilidad HTTP**: middleware `X-Request-ID` (propaga o genera; en logs JSON) + métricas
  por plantilla de ruta (`observabilidad/httpmetrics.py`).

## 4. Contratos e invariantes

- Ningún recurso se sirve cross-tenant: cargar por id sin guard de tenant es un fallo de CI
  (`test_tenant_guards.py`), no un code review.
- Los breaking changes de forma de respuesta se hacen con el frontend en el mismo commit
  (ej.: array plano → `{items,total,...}`).
- 401 (token inválido/expirado) siempre distinguible de 403 (rol insuficiente) y 404
  (cross-tenant se responde como inexistente).

## 5. Patrones reutilizables

- **Invariante estructural sobre el router**: recorrer `app.routes` en un test e imponer
  "toda ruta /api/* resuelve usuario" y "toda ruta con {id} pasa por guard de tenant".
  Gotcha FastAPI: `include_router` anida (`original_router.routes`) — el introspector debe
  recorrer recursivo.
- **Allowlist pública explícita** en vez de "acordarse de proteger": lo público es la
  excepción enumerada.
- **Rate limiter puro sin deps** (sliding window en memoria) alcanza hasta necesitar
  multi-proceso real.
- **`detail` como contrato de UX**: el backend escribe el mensaje que el usuario final leerá.

## 6. Pendientes conocidos

- Rate limiting distribuido (hoy por proceso; suficiente con réplica única de bot/scheduler).
- OpenAPI enriquecido (descripciones/ejemplos) para consumo externo.

## 7. Trazabilidad

- Tests: `test_auth.py` (16), `test_tenant_guards.py`, `test_listing.py` (8),
  `test_ratelimit.py` (7), `test_observability.py`, `test_users.py` (9), `test_webhook.py` (9),
  `test_backlog_close.py`.
- Métricas de la superficie: `GET /api/ops/http-metrics` + tarjeta en `/observabilidad`.
