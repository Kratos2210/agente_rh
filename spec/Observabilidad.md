# Observabilidad — Trazas, métricas, SLAs y calidad continua

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `observabilidad/`, `api/scheduler.py`

## 1. Propósito y alcance

Cómo se ve el sistema por dentro en producción: trazas LLM con contenido, métricas de
tokens/latencia/HTTP, alertas push, medición continua de la calidad de las respuestas de IA y
logging estructurado. Implementado como plan completo O-1..O-6 (estilo LangSmith/Phoenix pero
con **la PII en infraestructura propia** — Ley 29733).

## 2. Decisiones de diseño

- **Tabla propia como fuente de verdad de trazas** (`llm_traces`), no un SaaS: los prompts
  llevan respuestas del candidato; retención y erasure las purgan. LangSmith y Phoenix quedan
  como opcionales (LangSmith con `HIDE_INPUTS/OUTPUTS=true` por defecto; Phoenix self-hosted).
- **Percentiles sin series de tiempo**: HTTP usa histograma de buckets fijos por ruta
  (memoria O(1), `percentile_from_buckets` puro); LLM agrega p50/p95/p99 desde las filas de
  `llm_usage`. Suficiente señal sin Prometheus.
- **La latencia que importa es la del turno**: fila sintética `stage="turn"` con el wall-clock
  del turno del candidato **incluida la espera del lock** (latencia percibida), excluida de los
  agregados de tokens/costo.
- **Alertas push deduplicadas**: presupuesto (1×/tenant/mes/umbral), SLA (1×/condición/día),
  calidad (1×/día) — correo vía outbox `ops_email`. Sin dedupe, las alertas se vuelven ruido y
  se ignoran.
- **Calidad como signo vital, no foto**: el juez de groundedness corre 1×/día por tenant sobre
  las trazas reales de `answer`, persiste tasas en `quality_metrics` y alerta bajo umbral.

## 3. Diseño e implementación (O-1..O-6)

| Fase | Qué | Dónde |
|---|---|---|
| O-1 Trazas | Prompt/respuesta/error/latencia POR LLAMADA (capadas a `LLM_TRACE_MAX_CHARS`), flag `LLM_TRACE_ENABLED` | `MeteredLLM(trace=)` → `llm_traces` (0024); UI admin en detalle del candidato; `GET /api/candidates/{id}/traces` |
| O-2 Costos | Ver [Costos.md](Costos.md) | `compute_cost`, `_budget_sweep` |
| O-3 Percentiles | p95/p99 HTTP por ruta + latencia LLM por etapa + turno e2e | `observabilidad/httpmetrics.py`, `_latency_summary`, fila `turn` |
| O-4 SLAs push | Setting `sla_alerts` por tenant: ops alerts agrupadas + umbral p95 del turno 24 h | `_sla_sweep` + `_sla_breaches` (puro) + `ops_email` |
| O-5 Calidad | Golden 28 multi-suite + juez de fundamentación/relevancia | `scripts/golden_eval.py`, `scripts/groundedness_judge.py`, `_quality_sweep`, `quality_metrics` (0026) |
| O-6 Logs/errores | JSON logs con `request_id` (contextvar + header `X-Request-ID`), Sentry config-gated sin PII, snapshots HTTP a DB | `core/logging_config.py` (`LOG_JSON`), `runtime.init_sentry`, `_http_snapshot_sweep` (0025) |

### Alertas operativas (`_collect_ops_alerts`, por tenant)
Dead-letters del outbox · reuniones virtuales sin Meet link · coordinación de horario estancada
· divergencia motor↔negocio (fase del checkpoint vs `conversations.state`) · entrega Telegram
fallida sin interacción posterior · examen médico estancado · presupuesto excedido.
Visibles en `GET /api/ops/alerts` + página `/observabilidad`; empujadas por `_sla_sweep`.

### Superficie de consulta
`/observabilidad` (admin): alertas, outbox (chips + retry), calidad del día con semáforo,
tabla HTTP con p95/p99, bitácora de auditoría. Métricas de negocio+costo+latencia en la home y
el detalle de vacante.

## 4. Contratos e invariantes

- Toda llamada LLM queda medida (tokens, modelo, etapa, duración) — el metering no es opcional;
  las trazas con contenido sí (flag).
- Los sweeps de observabilidad son best-effort: nunca tumban el scheduler ni el turno.
- Las trazas/métricas con PII entran al ciclo de retención/erasure como cualquier dato del
  candidato.
- `X-Request-ID` se propaga si viene y se genera si no; siempre vuelve en la respuesta.

## 5. Patrones reutilizables

- **Trazas propias en tu DB** cuando hay PII: mismo valor de replay/debug que un SaaS, sin
  ceder datos; el juez de calidad las reutiliza como corpus.
- **Histograma de buckets + percentil por techo del bucket**: percentiles honestos sin
  infraestructura de métricas.
- **Fila sintética por unidad de negocio** (el "turno") además de por llamada: la latencia del
  usuario no es la suma de las del LLM.
- **Dedupe de alertas por (ámbito, condición, período)** con purga del set: alerta una vez,
  re-alerta solo si es condición nueva.
- **Signo vital LLM**: muestrear trazas reales + juez + persistir tasa diaria + umbral. Foto
  offline (golden) para regresión, vital online para deriva.

## 6. Pendientes conocidos

- Dashboards de series de tiempo (hoy: snapshots acumulados; el consumidor deriva deltas).
- Exportador OTel/Prometheus si se adopta stack de métricas externo.

## 7. Trazabilidad

- Tests: `test_traces.py` (10), `test_latency.py` (9), `test_sla.py` (10), `test_quality.py`
  (12), `test_o6_observability.py` (7), `test_metrics.py`, `test_observability.py` (5),
  `test_langsmith_privacy.py`.
- Migraciones: `0024`, `0025`, `0026`. Scripts: `golden_eval.py`, `groundedness_judge.py`,
  `retrieval_eval.py`.
