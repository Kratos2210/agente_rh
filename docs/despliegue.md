# Arquitectura de despliegue

Cómo se despliega el Agente de Selección, qué decide cada camino y por qué. Complementa
`deploy/k8s/README.md` (manifests) y `docs/gestion_secretos.md` (secretos).

## El sistema, visto desde el despliegue

```
                    ┌─────────────────────────────────────────────┐
   Telegram ⇄ poll  │  BACKEND (FastAPI, 1 proceso)               │
                    │  · API REST (45 endpoints, JWT, stateless)  │      Supabase / Postgres
   Dashboard ──────▶│  · Bot Telegram (polling en el lifespan)    │◀──▶  · negocio (20 tablas)
   (Next.js)        │  · Scheduler (tick 30 s, advisory lock)     │      · checkpoints LangGraph
                    │  · Servidor MCP (/mcp, opcional)            │
   Asistentes IA ──▶│  · RAG (Chroma local + embeddings)          │
   (MCP)            └─────────────────────────────────────────────┘
```

Un solo contenedor de backend concentra cuatro cargas: API, bot, scheduler y MCP.
**El estado vive en Postgres** (negocio + checkpoints de conversación + outbox): el
contenedor es reemplazable sin perder nada.

## Los tres caminos

### 1. Docker Compose (demo / on-premise chico) — `docker-compose.yml`
`docker compose up --build` levanta backend + frontend + proxy Caddy en `:3000`.
La DB es Supabase local (`supabase start` en el host; el compose redirige
`host.docker.internal`) o un proyecto cloud. Es el camino de evaluación y de
instalaciones de una sola máquina.

### 2. Kubernetes (producción) — `deploy/k8s/`
Manifests declarativos con namespace, ConfigMap/Secret, probes y recursos.
Particularidades honestas (no son plantilla genérica):

- **Backend `replicas: 1`** con `strategy: Recreate`. La restricción NO es el diseño
  de la app (API stateless, scheduler con advisory lock multi-réplica, outbox durable):
  es el **polling de Telegram**, que admite un solo `getUpdates` por token. Es una
  decisión deliberada del MVP (cero infra de webhooks: no requiere dominio público
  ni TLS para desarrollar).
- **Frontend `replicas: 2+`**: stateless puro, escala libre.
- **Datos**: todo lo durable está en Postgres; los volúmenes del pod son `emptyDir`
  re-generables (modelos HF, índice Chroma, staging de uploads).

**El camino para escalar el backend a N réplicas** (cuando el volumen lo pida):
1. Migrar el bot de polling a **webhook** (`setWebhook` → endpoint FastAPI ya detrás
   del Ingress). El turno del candidato ya es re-entrante (lock por `thread_id` +
   checkpointer transaccional).
2. El scheduler ya está listo (advisory lock con takeover).
3. Separar el scheduler a su propio Deployment (1 réplica lógica garantizada por el
   lock) si se quiere aislar su ciclo de vida — opcional, el lock ya lo hace seguro.

### 3. Serverless — decisión: **NO para el núcleo, sí para partes**

| Componente | ¿Serverless? | Motivo |
|---|---|---|
| API REST | ✅ Viable | Stateless (JWT), sin afinidad de instancia. Costo: cold-start con torch/embeddings (~decenas de s) — exigiría separar el RAG o precalentar. |
| Bot Telegram (polling) | ❌ No | Proceso long-lived por diseño. En webhook sí sería invocable, pero hoy el polling es la decisión de MVP (cero infra). |
| Scheduler (tick 30 s) | ❌ No como está | Es un loop residente. El equivalente serverless sería un cron externo (Cloud Scheduler/EventBridge) invocando endpoints de barrido — refactor menor, valor bajo mientras el backend ya corre 24/7 para el bot. |
| RAG (Chroma local + modelos) | ❌ No | Estado en disco + modelos de cientos de MB en memoria: anti-patrón FaaS. El camino sería un servicio vectorial gestionado (pgvector en el MISMO Supabase, o Pinecone). |
| Notificaciones (outbox) | ✅ Ya lo es conceptualmente | El patrón outbox+drain es una cola: mapea directo a una función consumiendo mensajes. |

**Conclusión documentada**: el sistema es un **monolito modular** desplegado como
contenedor único, con las costuras (canal, scheduler, RAG, notificaciones) ya
definidas como adaptadores/protocolos. Microservicios reales o serverless serían
prematuros para el volumen actual (entrevistas conversacionales: decenas/día, no
miles/s); el diseño deja el camino pavimentado en vez de pagar hoy la complejidad.

## Requisitos transversales (cualquier camino)

- **Migraciones**: `supabase/migrations/0001..0025` aplicadas ANTES del primer arranque
  (CLI `supabase db push` o psql). Gotcha conocido: tras DDL por psql directo,
  `NOTIFY pgrst, 'reload schema'`.
- **Secretos**: `assert_secure_config` bloquea el arranque en producción con secretos
  default/débiles (JWT, admin). Inventario y rotación: `docs/gestion_secretos.md`.
- **Salud**: `GET /api/health` (estado de Telegram, Supabase, scheduler) — es la probe
  de compose y K8s.
- **Observabilidad**: logs JSON (`LOG_JSON=true`) + `X-Request-ID`, Sentry
  (`SENTRY_DSN`), trazas LLM (`LLM_TRACE_ENABLED`), snapshots HTTP — todo config-gated,
  sin redeploy de código.
