# Arquitectura de despliegue

Cómo se despliega el Agente de Selección, qué decide cada camino y por qué. Complementa
`deploy/k8s/README.md` (manifests) y `docs/gestion_secretos.md` (secretos).

## El sistema, visto desde el despliegue

```
                    ┌─────────────────────────────────────────────┐
   Telegram ⇄ poll  │  BACKEND (FastAPI, 1 proceso)               │
                    │  · API REST (45 endpoints, JWT, stateless)  │      Supabase / Postgres
   Dashboard ──────▶│  · Bot Telegram (polling o webhook)         │◀──▶  · negocio (20 tablas)
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

### 2. Kubernetes (dev/prod) — `deploy/k8s/`
Manifests declarativos con **base + overlays kustomize por entorno**
(`base/` común; `overlays/dev/` y `overlays/prod/`). Cada entorno vive en su propio
namespace (`agente-rh-dev` / `agente-rh-prod`) y difiere en lo que importa:

| | dev | prod |
|---|---|---|
| `ENVIRONMENT` | `development` (el arranque solo AVISA ante secretos débiles) | `production` (**bloquea** el arranque: `assert_secure_config`) |
| Imagen | tag `dev` | registry + tag versionado (no `latest`) |
| Frontend réplicas | 1 | 2 |
| Recursos backend | reducidos | plenos |
| Dominio Ingress / CORS | dev | prod |

Aplicar (el secret NO se commitea; se aplica al namespace del entorno):

```bash
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml   # completar
deploy/deploy.sh validate            # valida AMBOS overlays (kubeconform strict)
deploy/deploy.sh k8s-apply prod      # o 'dev' → namespace agente-rh-prod
deploy/deploy.sh k8s-status prod
```

> ⚠️ **Nombres de env var**: pydantic lee por el nombre EXACTO del campo
> (`OPENAI_API_BASE`, `ENVIRONMENT`, …) e **ignora** los que no matchean. El ConfigMap
> anterior usaba `OPENAI_BASE_URL`/`APP_ENV` (ignorados) → el LLM caía al default
> localhost y el gate de producción no se activaba. Corregido en el `base/configmap.yaml`.

Particularidades honestas (no son plantilla genérica):

- **Backend en dos modos según el canal del bot (paso 3):**
  - **Polling** (default / overlay `dev`): `replicas: 1` + `strategy: Recreate`. Telegram
    admite un solo `getUpdates` por token, así que una 2ª réplica recibiría 409 Conflict.
    Cero infra de webhook (no requiere dominio público ni TLS) — ideal para desarrollar.
  - **Webhook** (overlay `prod`): con `TELEGRAM_WEBHOOK_URL` configurado (mismo host del
    Ingress), el bot registra `setWebhook` al arrancar y recibe los updates en
    `POST /telegram/webhook` (validado con el header `X-Telegram-Bot-Api-Secret-Token`).
    Al no polear, **es seguro** correr `replicas: 2` + `RollingUpdate`
    (`overlays/prod/backend-scale-patch.yaml`): Telegram reparte los POST entre réplicas y
    el turno del candidato ya es re-entrante (lock por `thread_id` + checkpointer
    transaccional). El scheduler interno ya toleraba N réplicas por el advisory lock.
- **Frontend `replicas: 2+`**: stateless puro, escala libre.
- **Datos**: todo lo durable está en Postgres; los volúmenes del pod son `emptyDir`
  re-generables (modelos HF, índice Chroma, staging de uploads).

**Activar webhook (resumen):**
1. Fijar `TELEGRAM_WEBHOOK_URL` = URL pública base del backend (el overlay prod ya lo trae).
2. Opcional: `TELEGRAM_WEBHOOK_SECRET` en el Secret (si se omite, se deriva del token).
3. Aplicar el overlay: el lifespan hace `setWebhook` y `GET /api/health` reporta
   `"telegram_mode":"webhook"`. Volver a polling = dejar `TELEGRAM_WEBHOOK_URL` vacío
   (y quitar `backend-scale-patch.yaml` / volver a `replicas: 1`).
4. **Alertas ops/SLA a un destino de equipo** (paso 3): fijar `OPS_ALERT_EMAIL` en el
   Secret — es el fallback cuando un tenant no define su `notify_email`, para que en
   multi-réplica las alertas no caigan en un buzón personal.

### 3. Serverless — decisión: **NO para el núcleo, sí para partes**

| Componente | ¿Serverless? | Motivo |
|---|---|---|
| API REST | ✅ Viable | Stateless (JWT), sin afinidad de instancia. Costo: cold-start con torch/embeddings (~decenas de s) — exigiría separar el RAG o precalentar. |
| Bot Telegram | ⚠️ Depende del modo | En **polling** es un proceso long-lived (no serverless). En **webhook** (paso 3, prod) el endpoint `POST /telegram/webhook` SÍ es invocable como función — es el camino hacia FaaS para el canal. |
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
