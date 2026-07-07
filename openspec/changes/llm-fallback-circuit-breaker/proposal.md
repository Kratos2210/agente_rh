# Proposal: llm-fallback-circuit-breaker

## Why

Hoy el sistema depende de UN solo proveedor LLM (Groq en el `.env`, o el BYOK del tenant): un
outage/429 sostenido degrada simultáneamente entrevistas (todo `low_confidence` →
`review_required` masivo), prescreen (cae a heurística) y dudas del candidato. Es el riesgo R3
de `audit/auditoria_v4.md` (dimensión D: "falta fallback de proveedor LLM") y el primer ítem del
roadmap de mediano plazo: un segundo endpoint compatible-OpenAI + circuit breaker convierte el
outage del proveedor en una degradación invisible para el candidato.

## What Changes

- Nuevo **proveedor de respaldo** config-gated por `.env`: `LLM_FALLBACK_BASE_URL` /
  `LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_MODEL` (vacíos = apagado, comportamiento actual
  intacto). Reusa `build_default_llm(model=, base_url=, api_key=)` — cero caminos nuevos de
  cliente.
- Nuevo `orquestacion/fallback.py`: **`CircuitBreaker`** (cerrado → abierto tras N fallos
  consecutivos → half-open tras cooldown; reloj inyectable) y **`FallbackLLM`** (protocolo
  `LLM`): intenta el principal; ante excepción registra el fallo y sirve con el respaldo; con el
  circuito abierto va directo al respaldo (no paga el timeout del proveedor caído); en half-open
  deja pasar una sonda al principal.
- **Atribución del modelo real**: `MeteredLLM` registra el modelo que REALMENTE atendió cada
  llamada (hoy lo captura antes del `complete`; con failover puede diferir) → `llm_usage`,
  `llm_traces` y costos quedan correctos sin cambios de esquema.
- Integración en `providers._build_pair` (cubre el LLM del `.env` Y el BYOK por-tenant: el
  respaldo es de la instalación, no del tenant) — el bot, sync-applicants y el juez lo heredan.
- **Banco de aceptación**: `scripts/golden_eval.py` gana `--base-url` / `--api-key-env` para
  validar el candidato a respaldo contra las suites existentes ANTES de habilitarlo (regla ya
  normada para el routing barato, extendida al respaldo).
- Todo fallo al principal se loggea (`LLM fallback de proveedor…`) — visible en los contadores
  `errors` por etapa ya existentes.

## Capabilities

### New Capabilities

(ninguna — es evolución de la operación LLM existente)

### Modified Capabilities

- `llm-operacion`: nuevo requisito "Fallback de proveedor con circuit breaker" (failover
  transparente, breaker con cooldown, atribución del modelo real, gating por banco de
  aceptación y default apagado).

## Impact

- **Código**: `orquestacion/fallback.py` (nuevo), `orquestacion/llm.py` (atribución post-call +
  wiring), `orquestacion/providers.py` (`_build_pair`), `core/config.py` (+5 parámetros),
  `scripts/golden_eval.py` (flags), `.env.example`.
- **Tests**: `tests/test_llm_fallback.py` (breaker, failover, atribución, gating, no-op sin
  config).
- **Docs**: `docs/adr-seleccion-modelo.md` (procedimiento de validación del respaldo),
  `spec/Orquestacion.md` y `spec/Costos.md` (punteros), guía `/guia` (parámetros 98→103 + nota).
- **Sin migraciones, sin cambios de API ni de frontend.** Con los 3 `.env` nuevos vacíos el
  comportamiento es byte-a-byte el actual.
