# Costos — Metering, pricing, presupuesto y optimización LLM

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `orquestacion/llm.py`, `api/deps.py`, `api/scheduler.py`
> Spec normativo: `openspec/specs/llm-operacion/spec.md`

## 1. Propósito y alcance

Cómo se mide, atribuye, presupuesta y optimiza el gasto en LLM: tokens por etapa y modelo,
costo estimado por tenant, alertas de presupuesto y las tres palancas de reducción (routing,
caché, cortes sin LLM).

## 2. Decisiones de diseño

- **Atribución por etapa Y por modelo**: `MeteredLLM` registra cada llamada con su etapa
  (`prescreen`, `evaluate`, `classify`, `answer`, `schedule`, `judge`) y el modelo real que la
  sirvió (con routing, una conversación usa varios). Sin atribución no hay optimización dirigida.
- **Pricing como configuración por-tenant** (`app_settings.llm_pricing`:
  `{models: {m: {input_per_1m, output_per_1m}}, default}`): el costo se calcula en consulta
  (`compute_cost`, puro), no se congela por fila — cambiar precios recalcula el histórico.
  Al elegir proveedor BYOK, el PUT **siembra** los precios sugeridos del catálogo sin pisar
  filas existentes.
- **Presupuesto mensual con alerta única**: `_budget_sweep` (≤ cada 15 min) compara el gasto del
  mes por tenant contra `llm_budget` y alerta al `alert_pct` (default 80 %) UNA vez por
  tenant/mes/umbral, por correo `ops_email`.
- **Optimizar con banco de aceptación, no a ojo**: el modelo barato se elige corriendo el golden
  de las etapas ruteadas (`golden_eval.py --model X --suite classify|slot`); la reversión de
  `classify` (sobre-deflectaba) quedó documentada en el ADR igual que el éxito de `schedule`.

## 3. Diseño e implementación

### Medición
`MeteredLLM.complete_staged(etapa, ...)` acumula `{input, output, calls, errors, duration_ms}`
por etapa; `service._record_usage` persiste en `llm_usage` (por conversación/vacante, con
`model` y `prompt_version`). Filas `stage="turn"` (latencia e2e) se **excluyen** de tokens/costo.

### Costo y reporte
`_aggregate_tokens` suma por etapa y `by_model`; `_with_cost(metrics, tenant_id)` aplica el
pricing del tenant → `est_cost` + `cost_by_model` en `GET /api/metrics` y las métricas por
vacante. Reporte paginado en `GET /api/costs` + página `/costos`; costo visible en la home
(el pricing default viene sembrado con el modelo demo — un pricing en ceros oculta el costo).

### Palancas de optimización (todas config-gated, default sin cambios)

| Palanca | Mecanismo | Ahorro |
|---|---|---|
| Routing por etapa | `LLM_CHEAP_MODEL=llama-3.1-8b-instant` + `LLM_CHEAP_STAGES=schedule` (validado 6/6 en golden slot; ~$0.05/$0.08 por 1M vs $0.29/$0.59 del principal) | Las etapas frecuentes y simples dejan de pagar el modelo grande |
| Caché semántica de dudas | Hit por vacante → respuesta sin RAG ni LLM (**0 tokens**) | Preguntas repetidas entre candidatos de la misma vacante |
| Cortes sin LLM | Respuesta vacía, tope de dudas, eco-inyección, dedupe/cooldown/cap del bot, acuse terminal | Turnos que jamás llegan al modelo |

## 4. Contratos e invariantes

- Ninguna llamada LLM queda sin medir; `record_usage` es best-effort pero el guard acepta
  también filas solo-latencia.
- El costo mostrado es **estimado** (tokens × precio configurado); el presupuesto mide el mes
  en curso, el `est_cost` de métricas es histórico del filtro — no confundirlos.
- Cambiar de proveedor/modelo BYOK mantiene la trazabilidad: cada fila lleva el modelo real que
  la atendió.
- Un modelo barato solo entra a una etapa tras pasar su suite golden completa.

## 5. Patrones reutilizables

- **Metering por (etapa, modelo) desde el día uno**: es barato de instrumentar temprano e
  imposible de reconstruir después.
- **Precio como config viva + costo calculado en lectura**: cero migraciones al cambiar tarifas.
- **Presupuesto = gasto agregado + umbral + dedupe + correo**: cuatro piezas simples ya evitan
  la sorpresa de fin de mes.
- **Escalera de ahorro**: primero cortar llamadas (gates), luego abaratarlas (routing), luego
  evitarlas (caché) — en ese orden de esfuerzo/beneficio.

## 6. Pendientes conocidos

- Costos de embeddings/re-ranker no se miden (corren local, costo ≈ CPU).
- Presupuesto con acción dura (throttle al 100 %) — hoy solo alerta.

## 7. Trazabilidad

- Tests: `test_costs.py` (10), `test_cost_routing.py` (10), `test_costs_page.py`,
  `test_metrics.py`. Migración: `0020` (latencia en llm_usage).
- ADR: `docs/adr-seleccion-modelo.md` (matriz + banco de aceptación + reversión de classify).
