# `observabilidad/` — Observabilidad

Componente **Observabilidad** de la rúbrica: logs, métricas y trazas del sistema, con las métricas clave
(latencia, tasa de alucinaciones, uso de recursos) instrumentadas y expuestas en `/observabilidad`.

| Archivo | Rol |
|---|---|
| `observability.py` | Gancho de trazado (LangSmith opcional) + `setup_tracing`. |
| `httpmetrics.py` | Histograma por ruta → p50/p95/p99 (`percentile_from_buckets`), snapshot a DB. |

Complementan: `orquestacion/llm.py` (`MeteredLLM`: tokens/costo/latencia + trazas con contenido),
`api/runtime.py` (Sentry + Arize **Phoenix self-hosted**, config-gated), `api/routes/observability.py`
+ `api/scheduler.py` (barridos de presupuesto/SLA/calidad), `evaluation/quality.py` (juez de
fundamentación → tabla `quality_metrics`).

**Métricas de la rúbrica:** latencia (p95/p99 por ruta + latencia del turno, stage `turn`) ·
alucinaciones (juez de groundedness) · recursos (tokens/costo/presupuesto en `llm_usage`, `_budget_sweep`).

> **Salvedad:** varias señales **nacen apagadas** (`LLM_TRACE_ENABLED`, `PHOENIX_ENABLED`,
> `LANGSMITH_TRACING`, `quality_alerts`) por superficie mínima / privacidad (Ley 29733). El mecanismo
> está instrumentado; se encienden por configuración.
