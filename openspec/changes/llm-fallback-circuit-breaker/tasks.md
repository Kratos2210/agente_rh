# Tasks: llm-fallback-circuit-breaker

## 1. Núcleo (breaker + wrapper)

- [x] 1.1 `core/config.py`: `llm_fallback_base_url` / `llm_fallback_api_key` /
  `llm_fallback_model` (vacíos = off) + `llm_breaker_failures` (3) +
  `llm_breaker_cooldown_seconds` (60); bloque documentado en `.env.example`.
- [x] 1.2 `orquestacion/fallback.py`: `CircuitBreaker` (closed/open/half-open, reloj
  inyectable, `allow_primary()` / `record_success()` / `record_failure()`).
- [x] 1.3 `orquestacion/fallback.py`: `FallbackLLM` (protocolo LLM; propaga `metadata` a ambos,
  expone `model`/`last_usage` del que sirvió, loggea cada failover) +
  `wrap_with_fallback(llm, settings)` (no-op sin config).

## 2. Integración y atribución

- [x] 2.1 `orquestacion/providers.py::_build_pair`: envolver el LLM principal con
  `wrap_with_fallback` (camino `.env` y camino BYOK).
- [x] 2.2 `orquestacion/llm.py::MeteredLLM.complete`: atribuir `_models[stage]` y el `model` de
  la traza con el modelo leído DESPUÉS del `complete` (el que realmente sirvió).
- [x] 2.3 Verificar que el bot hereda el wrapper por `_build_pair`/`refresh_metered_llm`
  (sin tocar call sites).

## 3. Banco de aceptación

- [x] 3.1 `scripts/golden_eval.py`: flags `--base-url` y `--api-key-env` (leen la key de una
  variable de entorno, nunca de un argumento) → validar el candidato a respaldo sin tocar `.env`.

## 4. Tests

- [x] 4.1 `tests/test_llm_fallback.py`: breaker (abre a los N, half-open tras cooldown, cierra
  con éxito, reabre si la sonda falla) con reloj fake.
- [x] 4.2 FallbackLLM: principal ok → sirve principal; principal falla → sirve respaldo y
  `MeteredLLM.drain_models()` atribuye el modelo del respaldo; circuito abierto → no toca el
  principal; ambos fallan → propaga la excepción (camino `low_confidence` actual).
- [x] 4.3 Gating: `wrap_with_fallback` devuelve el mismo LLM sin config; `_build_pair` con
  config de respaldo envuelve tanto env como BYOK.

## 5. Docs y cierre

- [x] 5.1 `docs/adr-seleccion-modelo.md`: sección del respaldo (criterio de elección +
  procedimiento de validación con el banco + comando).
- [x] 5.2 `spec/Orquestacion.md` y `spec/Costos.md`: puntero al respaldo/breaker.
- [x] 5.3 Guía `/guia`: parámetros 98→103 (KPIs) + mención del respaldo en la sección 11 +
  changelog (contrato sección 19).
- [x] 5.4 `audit/auditoria_v4.md`: marcar el ítem 1 del mediano plazo como implementado
  (gated: falta elegir/validar el proveedor real y setear el `.env`).
- [x] 5.5 Suite completa verde + `openspec validate --all --strict`.
