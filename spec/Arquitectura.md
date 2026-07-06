# Arquitectura — Componentes, límites y patrones transversales

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Fuente ADR: `docs/arquitectura.md`

## 1. Propósito y alcance

Describe la forma global del sistema: qué procesos existen, cómo se comunican, dónde vive cada
responsabilidad y qué patrones se repiten en todos los dominios. Los detalles de cada dominio
están en su spec propio.

## 2. Decisiones de diseño

- **Monolito modular, no microservicios**: un solo proceso FastAPI aloja API + bot Telegram +
  scheduler (ambos en el lifespan). Para el tamaño del equipo (unipersonal) y la carga, separar
  procesos habría multiplicado infraestructura sin beneficio; los límites se mantienen por
  **carpetas con interfaces claras**, lo que deja la puerta abierta a extraer servicios después.
- **Doble persistencia deliberada**: el estado conversacional (checkpointer LangGraph vía
  `DATABASE_URL` + `PostgresSaver`) y el estado de negocio (cliente Supabase, `db/repositories.py`)
  viven en el mismo Postgres pero con caminos de escritura distintos. El motor es la fuente de
  verdad del turno; `_sync_business` proyecta al negocio.
- **LangGraph con UN nodo** (`handle_turn`) + runner durable, en vez de un grafo de muchos nodos:
  la máquina de estados real vive en el `state` (fases) y el código Python; el grafo aporta el
  checkpointing durable y la inyección de dependencias, no el control de flujo.
- **Scheduler embebido con advisory lock de Postgres** (`pg_try_advisory_lock`, key 704127) en
  vez de un worker externo (Celery/cron): solo una réplica ejecuta los barridos; standby con
  takeover automático. Cero infraestructura adicional.
- **Todo LLM por API compatible-OpenAI**: un solo camino de integración
  (`ChatOpenAI(base_url=, api_key=)`) sirve para Groq, Gemini, NVIDIA, OpenAI, OpenRouter,
  Together, Ollama, HuggingFace y custom. Ver [Orquestacion.md](Orquestacion.md).

## 3. Diseño e implementación

### Diagrama (texto)

```
 Candidato ──Telegram──▶ ┌────────────────────────── FastAPI (proceso único) ─┐
 RR.HH. ───Dashboard───▶ │  api/main.py (lifespan, auth, ensamblaje)          │
 (Next.js :3000)         │  api/routes/* (8 routers, 67 endpoints)            │      Supabase/Postgres
 Asistente ──MCP───────▶ │  adaptadores_mcp/mcp.py (7 tools, /mcp)            │◀──▶  · negocio (21 tablas)
 Portales ──sourcing───▶ │  api/telegram_bot.py (polling ó webhook)           │      · checkpoints LangGraph
                         │  api/scheduler.py (advisory lock + ~10 barridos)   │
                         │  agente/ (LangGraph) + evaluation/ (scoring)       │◀──▶  LLM (Groq/BYOK)
                         │  retrieval/ + ranking/ (RAG Chroma)                │◀──▶  Chroma local
                         │  integrations/ (sourcing, Google Calendar/Meet)    │◀──▶  Google APIs
                         │  notifications/ (outbox → SMTP / Telegram API)     │◀──▶  SMTP
                         └────────────────────────────────────────────────────┘
```

### Mapa del código (estructura por la rúbrica, reorganizada 2026-07-04)

| Carpeta | Responsabilidad |
|---|---|
| `api/` | FastAPI: `main.py` (lifespan/login/ensamblaje), `routes/`, `telegram_bot.py`, `scheduler.py`, `auth.py`, `deps.py`, `ratelimit.py`, `runtime.py` |
| `agente/` | Cerebro LangGraph: `state.py`, `graph.py`, `nodes.py`, `prompts.py`, `service.py`, `sourcing_service.py` |
| `evaluation/` | `prescreen.py`, `scorer.py`, `scorecard.py`, `quality.py` |
| `channels/` | Interfaz `Channel` + Telegram/WhatsApp + validación de documentos |
| `db/` | `client.py` (Supabase) + `repositories.py` (todo el CRUD) |
| `notifications/` | `email.py`, `candidate.py`, `outbox.py` |
| `core/` | `config.py`, `estados.py`, `logging_config.py`, `registry.py` |
| `retrieval/` + `ranking/` | RAG: Chroma, embeddings, hybrid search, re-ranker, cachés |
| `orquestacion/` | `llm.py` (MeteredLLM), `providers.py` (BYOK), `qa_chain.py`, `classifier.py` |
| `observabilidad/` | `observability.py`, `httpmetrics.py` |
| `adaptadores_mcp/` | Servidor MCP |
| `integrations/` | `sourcing.py`, `scheduling.py` |
| `supabase/migrations/` | 27 migraciones SQL |
| `frontend/` | Dashboard Next.js 16 (12 páginas) |
| `despliegue/` | `deploy.sh`, `k8s/` (base+overlays), `launchd/` |

### El scheduler y sus barridos

`api/scheduler.py::_scheduler_loop` (tick 30 s, bajo advisory lock, config por-tenant leída en
cada tick): auto-contacto programado y de aptos diferidos · inactividad (recordatorios/cierres) ·
drenaje del outbox · reconciliación (dead-letters, reuniones sin link, scheduling estancado,
divergencia motor↔negocio, entregas fallidas) · retención/anonimización · purga de checkpoints ·
presupuesto LLM · SLAs push · calidad de respuestas (juez) · snapshot de métricas HTTP · onboarding.
Los gates "a lo sumo cada N min" usan sentinel `None` (no `0.0`: en un host recién booteado
`time.monotonic()` < intervalo y el primer barrido se saltaba — bug real cazado por CI).

## 4. Contratos e invariantes

- Toda ruta `/api/*` (salvo `/api/health` y `/api/auth/login`) exige JWT y aísla por tenant;
  hay un test estructural que lo impone (ver [Seguridad.md](Seguridad.md)).
- El motor conversacional **no escribe negocio directamente**: solo `service._sync_business`
  proyecta fase→status; el negocio no escribe checkpoints.
- Los barridos son **best-effort e idempotentes**: un fallo en uno no tumba el loop ni repite
  efectos (dedupe por slot/día/condición).
- El bot en **polling admite exactamente una réplica** (un `getUpdates` por token); webhook
  habilita N réplicas. La decisión vive en los overlays de K8s.

## 5. Patrones reutilizables

- **Config-gated, apagado por defecto**: toda capacidad nueva con costo/riesgo (MCP, trazas,
  RAG en caliente, caché, médico, webhook, Sentry, Phoenix) nace detrás de un flag default-off;
  el "perfil de producción" es el overlay que los enciende.
- **Helpers puros + shell imperativo**: la lógica decidible (`_inactivity_decision`,
  `compute_free_slots`, `backoff_seconds`, `_sla_breaches`, `percentile_from_buckets`) se extrae
  a funciones puras testeables sin infraestructura; el I/O queda en una cáscara delgada.
- **Protocol + factory para integraciones**: `SimulatedX` (sin credenciales, default) +
  `RealX` (lazy) + `get_x(settings)`. Permite demo completa sin cuentas externas y e2e tests.
- **Registro-primero**: ante efectos externos no transaccionales (crear evento de Calendar),
  registrar la intención en DB ANTES del efecto; un crash a mitad no duplica.
- **Fail-open vs fail-safe explícito por caso**: caché/revocación/tracing degradan sin romper
  (fail-open); gates de seguridad y validación de estado fallan ruidoso (fail-safe).
- **Outbox para todo efecto hacia afuera** (correo, Telegram del dashboard, reindex): fin del
  fire-and-forget. Ver [Notificaciones.md](Notificaciones.md).
- **Invariantes estructurales en CI**: tests que recorren la app (rutas→guards, estados→espejo
  frontend) y fallan si un endpoint futuro olvida el patrón.

## 6. Pendientes conocidos

- Extraer el scheduler a proceso propio si el volumen lo pide (hoy: lock + réplica única lógica).
- RLS "efectivo" sobre el backend (hoy latente: el backend usa service_role; ver [Seguridad.md](Seguridad.md)).
- Object storage para CVs (hoy Postgres ≤5 MB + disco).

## 7. Trazabilidad

- ADRs: `docs/arquitectura.md` (~25 decisiones). Madurez: `audit/auditoria_v4.md` (≈85/100, Nivel 4).
- Tests estructurales: `tests/test_tenant_guards.py`, `tests/test_estados.py`.
- Guía navegable con diagrama SVG: `frontend/src/app/guia/page.tsx` (ruta `/guia`).
