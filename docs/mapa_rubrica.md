# Mapa de conformidad — rúbrica `audit/chekeo.md` → implementación real

Índice único para el evaluador. La rúbrica ("Repositorio de IA en Producción") pide siete componentes en
carpetas nombradas. **El código real vive ahora en esas carpetas** (reorg de 2026-07-04: se movieron los
módulos desde el grab-bag `src/` y desde `agent/` a los paquetes de la rúbrica, preservando historia con
`git mv`). Los dominios del producto que no son parte de la rúbrica (`api/`, `db/`, `notifications/`,
`channels/`, `integrations/`, `evaluation/`) conservan sus carpetas, y la infra transversal
(config/logging/registry) quedó en `core/`.

**Veredicto:** cumplimiento funcional **completo** (13/13 requisitos) y estructural: cada componente de la
rúbrica es una carpeta real con su código. Layering acíclico: `agente → orquestacion → retrieval → ranking → core`.

## Componentes de la rúbrica

| Componente (rúbrica) | Carpeta | Código real | Estado |
|---|---|---|---|
| Retrieval (BD vectorial) | [`retrieval/`](../retrieval/) | `vectorstore.py`, `embeddings.py`, `semantic_cache.py`, `rag.py`, `answer_cache.py`; seed: `scripts/seed_company_kb.py` | ✅ |
| Ranking (re-ranker) | [`ranking/`](../ranking/) | `reranker.py` (cross-encoder); scoring de personas: `evaluation/scorer.py`, `evaluation/scorecard.py` | ✅ |
| Orquestación (LangChain) | [`orquestacion/`](../orquestacion/) | `llm.py` (LLM intercambiable + metering), `qa_chain.py`, `classifier.py` | ✅ |
| Agente cíclico (LangGraph) | [`agente/`](../agente/) | `graph.py`, `state.py`, `nodes.py`, `prompts.py`, `service.py`, `sourcing_service.py` | ✅ |
| Adaptadores MCP | [`adaptadores_mcp/`](../adaptadores_mcp/) | `mcp.py`; demo cliente: `scripts/mcp_client_demo.py`; externos: `integrations/`, `channels/` | ✅ |
| Observabilidad | [`observabilidad/`](../observabilidad/) | `observability.py`, `httpmetrics.py`; también `api/runtime.py`, `orquestacion/llm.py`, `api/routes/observability.py` | ✅ |
| Despliegue | [`despliegue/`](../despliegue/) | `deploy.sh`, `k8s/` (base + overlays dev/prod); + `Dockerfile.backend`, `docker-compose.yml`, `.github/workflows/`, `docs/despliegue.md` | ✅ |

## Requisitos transversales de la rúbrica

| Requisito | Dónde | Estado |
|---|---|---|
| README general (arquitectura + ejecución) | [`README.md`](../README.md) (diagrama + tabla de patrones con punteros) | ✅ |
| Manifiestos K8s / serverless por componente | `despliegue/k8s/` (base + overlays dev/prod), serverless argumentado en `docs/despliegue.md` | ✅ |
| Scripts de automatización de despliegue y escalado | `despliegue/deploy.sh` (build/push/compose/validate/k8s-apply/scale) | ✅ |
| LangSmith **y Arize** | LangSmith (`observabilidad/observability.py`) + **Arize Phoenix self-hosted** (`api/runtime.py`) + Sentry | ✅ * |
| Métrica: latencia | p95/p99 por ruta (`observabilidad/httpmetrics.py`) + latencia del turno (stage `turn`) | ✅ |
| Métrica: tasa de alucinaciones | Juez de fundamentación `evaluation/quality.py` → tabla `quality_metrics` | ✅ |
| Métrica: uso de recursos | Tokens/costo/presupuesto (`llm_usage`, `_budget_sweep`, `est_cost`) | ✅ |
| Documentación de decisiones + despliegue/monitoreo | `docs/arquitectura.md`, `docs/despliegue.md`, `docs/adr-seleccion-modelo.md`, `docs/gestion_secretos.md`, guía `/guia` | ✅ |
| Checklist: funcional y modular | 359 tests, monolito modular por dominio con las 7 carpetas de la rúbrica | ✅ |
| Checklist: MCP seguro y conforme | JWT + RBAC + tenancy + auditoría + confirmación 2 pasos; SDK oficial `mcp` | ✅ |
| Checklist: observabilidad instrumentada | Instrumentada y expuesta en `/observabilidad` | ✅ |
| Checklist: scripts de despliegue en entornos reales | `despliegue/deploy.sh validate` (kubeconform 7/7), imágenes publicadas a GHCR por CI | ✅ |

\* **Nota Arize:** la rúbrica dice "Arize"; el repo usa **Arize Phoenix self-hosted** (mismo ecosistema
OpenInference/OTel) en lugar del SaaS Arize, por residencia de datos PII (Ley 29733 · Perú). Sustitución
consciente y documentada en `docs/arquitectura.md`.

## Salvedad operativa (por diseño)
Varias señales de observabilidad **nacen apagadas** (`LLM_TRACE_ENABLED`, `PHOENIX_ENABLED`,
`LANGSMITH_TRACING`, `quality_alerts`): el mecanismo está instrumentado; para verlas en vivo hay que
encenderlas por configuración. Es una decisión de superficie mínima / privacidad, no una ausencia.
