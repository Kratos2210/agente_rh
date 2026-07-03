# Agente de Selección de Talento (`agente_rh`)

Sistema de IA en producción que **automatiza el proceso de selección de punta a punta**: importa
postulantes de portales de empleo, pre-filtra sus CVs con IA, conduce una **entrevista conversacional
por Telegram**, evalúa cada respuesta contra los criterios de la vacante, entrega a RR.HH. un
**scorecard con semáforo (🟢/🟡/🔴)** y coordina las entrevistas de las tres etapas del proceso
(RR.HH. → líder del proyecto → gerencia) hasta la **contratación** — con multi-empresa, roles,
auditoría y observabilidad completas.

> 📖 **Guía end-to-end para todo público**: ruta `/guia` del dashboard (19 secciones, cada una con
> resumen "En simple"). Bitácora de decisiones por fecha: `CLAUDE.md`.

## Arquitectura

```
   Telegram ⇄ poll   ┌──────────────────────────────────────────────┐
                     │  BACKEND · FastAPI (api/)                    │
   Dashboard ───────▶│  · API REST 45 endpoints (JWT + RBAC/tenant) │       Supabase / PostgreSQL
   Next.js 16        │  · Bot Telegram (polling, lifespan)          │◀──▶   · negocio: 20 tablas, RLS
   (frontend/)       │  · Scheduler 30 s (advisory lock)            │       · checkpoints LangGraph
                     │  · MCP server /mcp (read-only, opcional)     │       · outbox durable
   Asistentes IA ───▶│                                              │
   (MCP)             │  agent/ (LangGraph) · evaluation/ (scoring)  │
                     │  integrations/ (sourcing, Google Calendar)   │
                     │  notifications/ (email, outbox) · src/ (RAG) │
                     └──────────────────────────────────────────────┘
```

**Monolito modular**: el cerebro (LangGraph) es lógica pura y testeable; canales, DB, IA y Google
son adaptadores intercambiables (cada uno con implementación real y simulada). Todo el estado vive
en Postgres → el contenedor es reemplazable sin perder conversaciones.

## Patrones de IA implementados (con punteros al código)

| Patrón | Dónde | Qué hace |
|---|---|---|
| **RAG** (hybrid retrieval + re-ranker + generación) | `src/vectorstore.py` (Chroma + BM25 + vectorial), `src/reranker.py` (cross-encoder), `src/qa_chain.py`, `agent/rag.py` | Responde dudas del candidato sobre el puesto fundamentándose en la base de conocimiento de la empresa. Embeddings `multilingual-e5` + semantic cache. |
| **Orquestación LangChain** | `src/qa_chain.py`, `agent/llm.py` (`LangChainLLM`), `src/embeddings.py`, `src/classifier.py` | Cadenas de prompts, LLM intercambiable (Groq/Qwen3 u otro compatible-OpenAI), metadata propagada a trazas. |
| **Flujos cíclicos LangGraph** | `agent/graph.py`, `agent/nodes.py`, `agent/state.py` | Máquina de estados durable (checkpointer Postgres, hilo `canal:chat`): follow-ups por respuesta vaga, dudas con tope, reintentos de horario con escalamiento, timeouts, agendamiento ×3 etapas. |
| **MCP seguro** | `api/mcp.py` | 5 tools read-only bajo el mismo JWT del dashboard: tenancy, RBAC, revocación y auditoría heredados; capability ≠ autoridad (v1 sin mutaciones). `MCP_ENABLED` off por defecto. |
| **Observabilidad** | `src/observability.py`, `agent/llm.py` (`MeteredLLM`), `api/httpmetrics.py`, `scripts/golden_eval.py`, `scripts/groundedness_judge.py` | Trazas LLM con contenido, costos por modelo/empresa + presupuesto, p50/p95/p99 por etapa y ruta, latencia end-to-end del turno, SLAs push, suite golden (28 casos) + juez de alucinaciones, logs JSON + request-id, Sentry. Gancho LangSmith opcional. |

## Requisitos

- Python 3.12 + [uv](https://docs.astral.sh/uv/) · Node 20+ (dashboard)
- Docker + [Supabase CLI](https://supabase.com/docs/guides/cli) (DB local) o un proyecto Supabase cloud
- Bot de Telegram ([@BotFather](https://t.me/BotFather)) y API key de un LLM compatible-OpenAI (p. ej. Groq)

## Puesta en marcha (desarrollo)

```bash
# 1) Dependencias
uv sync --extra dev

# 2) Base de datos local (aplica las 25 migraciones de supabase/migrations/)
supabase start
supabase migration up

# 3) Configuración
cp .env.example .env
#    Pega: SUPABASE_URL / SUPABASE_SERVICE_KEY / DATABASE_URL (de `supabase status`),
#    OPENAI_API_KEY (+ base/model), TELEGRAM_BOT_TOKEN y (opcional) SMTP_*.

# 4) Sembrar la base de conocimiento RAG de la vacante demo
uv run python scripts/seed_company_kb.py

# 5) Backend + bot (el bot arranca solo si hay TELEGRAM_BOT_TOKEN)
uv run uvicorn api.main:app --port 8000 --reload

# 6) Dashboard → http://localhost:3000 (login: ADMIN_EMAIL/ADMIN_PASSWORD del .env)
cd frontend && npm install && npm run dev

# Demo del cerebro sin infraestructura (consola, LLM real):
uv run python scripts/demo.py --alberto
```

## Despliegue

| Camino | Artefactos | Para qué |
|---|---|---|
| **Docker Compose** | `docker-compose.yml` + `Dockerfile.backend` + `frontend/Dockerfile` + `Caddyfile` | Demo / on-premise de una máquina: `docker compose up --build` → `http://localhost:3000`. |
| **Kubernetes** | `deploy/k8s/` (kustomize; validado con kubeconform) | Producción. Backend `replicas: 1` (bot en polling — la decisión y el camino a escalar están documentados), frontend escala libre. |
| **Serverless** | `docs/despliegue.md` | Decisión argumentada: NO para el núcleo (bot long-lived, scheduler, RAG con estado), sí viable para API/notificaciones tras migrar el bot a webhook. |

Detalle completo: **`docs/despliegue.md`**.

## Calidad y verificación

```bash
uv run pytest -q                                   # 283 tests (motor, API, seguridad, outbox…)
cd frontend && npx tsc --noEmit && npm run lint    # dashboard
uv run python scripts/golden_eval.py               # golden suite multi-etapa con LLM real
uv run python scripts/groundedness_judge.py        # juez de alucinaciones sobre trazas reales
```

CI (GitHub Actions, `.github/workflows/ci.yml`): tests backend + lint/typecheck frontend +
build de la imagen Docker + validación de manifests K8s.

## Mapa de documentación

| Documento | Contenido |
|---|---|
| `/guia` (ruta del dashboard) | Guía end-to-end de 19 secciones para todo público. |
| `docs/arquitectura.md` | Decisiones de arquitectura (ADR-lite): qué se eligió, alternativas y por qué. |
| `docs/despliegue.md` | Los tres caminos de despliegue y la decisión microservicios/serverless. |
| `docs/gestion_secretos.md` | Inventario de secretos, rotación por secreto y camino a un secret manager. |
| `docs/auditoria_e2e.md` | Auditoría de 10 dimensiones (seguridad, UX, LLM, estado…) — backlog cerrado. |
| `docs/auditoria_integraciones_externas.md` | Auditoría de integraciones (F1–F5, cerradas). |
| `CLAUDE.md` | Bitácora cronológica de cada fase con su verificación en vivo. |

## Convenciones

Código en inglés · documentación/chat en español · `uv` (nunca pip directo) · commits
convencionales (`feat:`, `fix:`, `docs:`…).

> ⚠️ **Gotcha (Mac Intel)**: `torch==2.2.2` y `onnxruntime<1.21` están pineados porque las versiones
> nuevas no publican wheels para macOS x86_64. La imagen Docker (Linux) usa el mismo pin resuelto
> contra el índice CPU de PyTorch.
