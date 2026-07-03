# Decisiones de arquitectura (ADR-lite)

Registro condensado de las decisiones estructurales del sistema: qué se eligió, qué
alternativas se consideraron y por qué. La narrativa completa por fecha vive en la
bitácora de `CLAUDE.md`; la explicación divulgativa, en la ruta `/guia` del dashboard.

## Núcleo

| Decisión | Alternativas | Por qué |
|---|---|---|
| **Monolito modular** (un proceso FastAPI: API + bot + scheduler + MCP) con costuras de adaptador | Microservicios desde el día 1; funciones serverless | El volumen (entrevistas conversacionales, decenas/día) no justifica la complejidad distribuida. Las costuras ya son contratos (Channel, SourcingConnector, Scheduler, LLM inyectable): partir el sistema después es mover módulos, no reescribir. Detalle: `docs/despliegue.md`. |
| **LangGraph con checkpointer Postgres** para la conversación | Estado en memoria; tabla de sesiones a mano; framework de diálogo | La entrevista es una máquina de estados de días de vida (recordatorios, reagendas, 3 etapas). El checkpointer da durabilidad transaccional por hilo (`canal:chat`) y reanudación exacta tras reinicios, gratis. |
| **Cerebro puro** (nodos sin I/O; LLM, red y DB inyectados) | Nodos que llaman servicios directamente | Los 283 tests corren en ms con IA falsa determinista; el mismo grafo se conduce por Telegram, scripts o simulación service-level sin cambios. |
| **Doble persistencia**: negocio (cliente Supabase) + estado motor (checkpointer por `DATABASE_URL`) | Todo por un solo camino | El estado del motor es interno y opaco (blobs del grafo); el negocio es consultable y con RLS. `service._sync_business` proyecta motor→negocio en cada turno. |
| **LLM compatible-OpenAI inyectable** (Groq/Qwen3 hoy) | Atarse a un proveedor/SDK | `build_llm` + `MeteredLLM` envuelven cualquier endpoint OpenAI-compatible; cambiar de proveedor es cambiar 3 variables del `.env`. |

## Datos y seguridad

| Decisión | Alternativas | Por qué |
|---|---|---|
| **Supabase (Postgres)** para el negocio | SQLite; MySQL; Mongo | Postgres gestionado con REST inmediato (PostgREST), RLS nativa, migraciones CLI y camino local→cloud sin cambios de código. |
| **Multi-tenancy en capa de app + RLS latente** | Solo app; solo RLS; una DB por tenant | Guards por endpoint (con test de CI `test_tenant_guards.py` que falla si un endpoint nuevo los olvida) + políticas RLS en las 20 tablas como defensa en profundidad. RLS efectiva sobre el backend queda diferida (el backend usa service_role); activarla requiere claims por request. |
| **JWT propio (PyJWT + bcrypt) con rotación grácil** | Supabase Auth; Auth0 | Cero infra extra para el MVP; `JWT_SECRET_PREVIOUS` permite rotar la firma sin cerrar sesiones. El gate `assert_secure_config` impide arrancar en prod con secretos débiles. Runbook: `docs/gestion_secretos.md`. |
| **Documentos del candidato en Postgres** (`candidate_documents`, base64, cap 5 MB) | S3/Supabase Storage; solo disco | Storage está off en el entorno local (Mac Intel) y el disco muere en cada redeploy. En Postgres sobreviven redeploys, el borrado es transaccional (FK cascade con el erasure) y el volumen es bajo. Migrar a object store es optimización de escala, no corrección. |
| **Retención/erasure Ley 29733** (anonimización programada + derecho al olvido en cascada, checkpoint incluido) | Guardar todo indefinidamente | Cumplimiento peruano de datos personales; también purga trazas LLM y PII residual del outbox/auditoría. |

## Confiabilidad

| Decisión | Alternativas | Por qué |
|---|---|---|
| **Outbox durable con backoff** (1 min→6 h, dead-letter a los 6 intentos, reintento manual en UI) | Fire-and-forget; cola externa (SQS/Rabbit) | Ningún correo/aviso se pierde en silencio y no se agrega infra: la cola es una tabla, el drain corre en el scheduler bajo el mismo lock. |
| **Scheduler in-process con advisory lock de Postgres** | Cron externo; Celery/worker aparte | Un tick de 30 s cubre auto-contacto, inactividad, retención, presupuesto, SLAs y snapshots. El lock (`pg_try_advisory_lock`) garantiza un solo ejecutor con takeover — N réplicas son seguras sin coordinador nuevo. |
| **Bot Telegram en polling** | Webhook | Cero infra para desarrollar (sin dominio público/TLS). Costo conocido: limita el backend a 1 réplica; el camino webhook está documentado en `docs/despliegue.md` y los manifests lo codifican honesto (`replicas: 1`, strategy Recreate). |
| **Registro-primero en agendamiento** (fila de reunión antes del evento Calendar) | Evento primero | Un crash a mitad no duplica eventos: la reconciliación detecta la fila sin link y alerta. |
| **Idempotencia sistemática** (contacto, re-sync por `source_ref`, psych-exam, reuniones por (conversación, etapa)) | Confiar en que no se repite | Los reintentos (outbox, humanos con doble click, re-sync) son la norma, no la excepción. |

## IA aplicada

| Decisión | Alternativas | Por qué |
|---|---|---|
| **RAG con Chroma local + hybrid search + cross-encoder** | Solo vectorial; API de embeddings de pago; pgvector | Heredado probado de `agente_pro`; corre offline (embeddings HF locales), BM25 cubre términos exactos (siglas, nombres) donde lo vectorial falla, y el re-ranker liviano prioriza sin GPU. pgvector es el camino si se quiere sacar el estado del pod (ver `docs/despliegue.md`). |
| **Anti-inyección en TODOS los prompts con texto del candidato** (sanitizado + delimitadores `<<<respuesta>>>` + instrucción explícita) | Confiar en el modelo | El candidato es input adversarial por definición; la suite golden incluye contraejemplos de inyección que deben puntuar 0. |
| **Fallbacks deterministas + escalamiento a humano** (`low_confidence` → `review_required` en el scorecard) | Reintentar hasta que salga; inventar nota | Si la IA falla, el sistema prefiere avisar "requiere revisión humana" a fabricar una evaluación. |
| **Evaluación con contratos** (la IA devuelve JSON parseado por clave; heurística de respaldo en cada parser) | Ejecutar lo que diga el LLM | La IA nunca ejecuta acciones: produce datos que código determinista valida (scoring, elección de horario, clasificación de turno). |
| **`PROMPT_VERSION` sellado** en scorecards y uso LLM | Prompts sin versionar | Permite atribuir cambios de calidad/costo a la versión exacta de los prompts. |

## Observabilidad (plan O-1..O-6, completo)

| Decisión | Alternativas | Por qué |
|---|---|---|
| **Tablas propias como fuente de verdad** (`llm_usage`, `llm_traces`, `http_metrics_snapshots`) + gancho LangSmith opcional | SaaS externo (LangSmith/Arize) como primario | Los prompts contienen respuestas del candidato (PII, Ley 29733): no salen a un SaaS por defecto. LangSmith queda config-gated para desarrollo. |
| **Percentiles con histograma de buckets fijos** (HTTP) y nearest-rank sobre `llm_usage` (IA) | Prometheus + Grafana | Memoria O(1), cero infra, visible en el propio dashboard (`/observabilidad`). Prometheus es el paso natural si aparece una plataforma de métricas. |
| **Golden suite (28 casos) + juez de groundedness** como gates ejecutables | Evaluación manual | Regresiones de prompts/modelo se detectan con `exit 1` (usable como nightly); el juez mide la tasa de alucinaciones sobre trazas reales. |
| **SLAs push por tenant** (correo, dedupe por condición/día) | Solo dashboard pull | Lo crítico (dead-letters, p95 del turno, presupuesto) llega solo, sin esperar que alguien mire el panel. |

## MCP

| Decisión | Alternativas | Por qué |
|---|---|---|
| **Servidor MCP read-only montado en la misma app**, mismo JWT, tools = funciones de los endpoints | Servidor MCP aparte con su propia auth; tools con mutación desde el día 1 | Capa de adaptación pura: hereda tenancy, RBAC, enmascarado por rol y auditoría sin duplicar lógica. Capability ≠ autoridad: v1 sin mutaciones; extender con confirmación explícita cuando haga falta. |
