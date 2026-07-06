# Orquestación — Capa LLM: metering, BYOK, routing y prompts

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `orquestacion/`, `agente/prompts.py`

## 1. Propósito y alcance

Todo lo que media entre el sistema y los modelos de lenguaje: construcción de clientes,
medición de tokens/latencia, trazas, proveedor por-tenant (BYOK), routing de modelo por etapa,
ingeniería y versionado de prompts, y cachés. La evaluación semántica (qué se le pregunta al
LLM) está en [Evaluacion.md](Evaluacion.md).

## 2. Decisiones de diseño

- **Un único protocolo: API compatible-OpenAI**. Groq, Gemini, NVIDIA NIM, OpenAI, OpenRouter,
  Together, Ollama, HuggingFace y custom se consumen todos con `ChatOpenAI(base_url=, api_key=)`
  — un solo camino de código, proveedores intercambiables (`orquestacion/providers.py::PROVIDERS`).
- **Metering como wrapper, no como aspecto del proveedor**: `MeteredLLM` envuelve cualquier LLM
  y acumula tokens/llamadas/errores/latencia **por etapa** (`prescreen`, `evaluate`, `classify`,
  `answer`, `schedule`, `judge`…) vía `complete_staged`. La atribución por etapa es la base de
  costos, latencias y trazas.
- **BYOK por-tenant con hot-swap**: cada empresa configura proveedor/modelo/API key desde el
  dashboard (app_setting `llm_provider`); la key se cifra con **Fernet** (clave derivada de
  `jwt_secret|llm-provider`); `resolve_llm_config(tenant_id)` cachea 60 s y
  `refresh_metered_llm` reconfigura el LLM **por turno** sin reiniciar, preservando el
  acumulado y la metadata de tracing. Apagado = todo del `.env` (retrocompat total).
- **Routing de modelo por etapa**: etapas simples y frecuentes van a un modelo barato del mismo
  proveedor (`LLM_CHEAP_MODEL` + `LLM_CHEAP_STAGES`). Elección validada con banco de aceptación
  golden, no a ojo. **Lección registrada**: `classify` se ruteó al barato y se revirtió — al
  pasar el clasificador a 3 vías (`answer|question|offtopic`) el modelo chico sobre-deflectaba
  dudas legítimas; hoy solo `schedule` va al barato (ver `docs/adr-seleccion-modelo.md`).
- **Anti-inyección en profundidad**: prompt-only no basta en modelos chicos (probado en red
  teaming); se combina sanitización + delimitadores + detectores en el input + salidas seguras
  sin LLM. Ver [Seguridad.md](Seguridad.md).

## 3. Diseño e implementación

### Construcción y despacho

- `orquestacion/qa_chain.py::build_llm` / `llm.py::build_default_llm(model=, base_url=, api_key=)`
  — cliente base (el arranque no importa torch).
- `MeteredLLM(inner, overrides={etapa: LLM}, trace=, trace_max_chars=, config_fingerprint=)`:
  despacha cada etapa a su LLM; `drain_*()` entrega tokens/modelos/trazas del turno;
  `set_context()` propaga metadata (conversación) a inner+overrides para LangSmith/Phoenix;
  `reconfigure()` = hot-swap.
- `build_stage_overrides(settings)` arma el mapa de routing; `build_tenant_metered_llm` +
  `refresh_metered_llm` aplican la config del tenant. Fingerprint = sha256 de la config
  (nunca la key en claro) para invalidar cachés.
- Endpoints BYOK: `GET/PUT /api/settings/llm-provider` (GET enmascara la key `gsk_...XXXX`;
  PUT siembra `llm_pricing` sin pisar), `POST .../test` (completion efímera con key scrubbed),
  `GET .../catalog`. **Anti-SSRF**: base_url privada rechazada salvo
  `ALLOW_PRIVATE_LLM_ENDPOINTS=true` (Ollama en red propia).

### Prompts (`agente/prompts.py`)

- `PROMPT_VERSION = "2026-07-05.2"` + changelog embebido (una línea por bump). Se sella en cada
  scorecard y fila de `llm_usage` → toda evaluación es atribuible a la versión del prompt que
  la produjo. Un job de CI bloquea cambios de prompt sin bump.
- Técnicas aplicadas: **few-shot** de calibración en `EVALUATE_ANSWER_PROMPT` (2 ejemplos con
  comillas «», sin delimitadores, para no romper extractores); **anti-inyección** — todo texto
  del candidato entra `sanitize_answer_for_prompt` (quita delimitadores, cap 4000 chars) y va
  encerrado entre `<<<respuesta>>>…<<<fin>>>` con instrucción de marco; salida JSON con cue
  explícito; mensajes del sistema por fase (recordatorios, cierres, confirmaciones de agenda
  por (stage, modality)).
- Clasificador de turno (`orquestacion/classifier.py`): 3 vías `answer|question|offtopic`,
  con heurística de respaldo.

### Cachés

- **Caché semántica de dudas** (`retrieval/answer_cache.py`, flag
  `INTERVIEW_ANSWER_CACHE_ENABLED`): por vacante, coseno ≥ umbral → respuesta sin RAG ni LLM
  (0 tokens). Almacén SQLite propio; carga lazy de embeddings; fail-open.
- Matching extraído a helper puro `best_match` (`retrieval/semantic_cache.py`), reusado por RAG.

## 4. Contratos e invariantes

- **Toda llamada LLM pasa por `MeteredLLM` con etapa**: nada llama al proveedor directo (si no,
  costos/trazas/latencias quedan ciegos).
- Fallo del proveedor → `EvalResult` de fallback con `low_confidence=True` y log
  `LLM fallback en <fn>`: el flujo del candidato **nunca** se cae por el LLM.
- La API key BYOK jamás sale en claro: cifrada en DB, enmascarada en GET, scrubbed en errores
  de `/test`. Rotar `JWT_SECRET` invalida el descifrado → warning + fallback al `.env` (fail-open).
- El hot-swap por turno es best-effort: si falla, el turno corre con el LLM anterior.

## 5. Patrones reutilizables

- **Wrapper de metering con etapas**: instrumentar en un solo punto (decorator/wrapper) con
  etiqueta de etapa da costos, latencia, trazas y routing "gratis" para todo el sistema.
- **BYOK = catálogo + cifrado derivado + caché TTL + fingerprint**: patrón completo para
  multi-tenancy de proveedor sin migraciones (JSONB en settings).
- **Bancos de aceptación antes de rutear a un modelo barato**: `golden_eval.py --model X --suite Y`
  convierte "parece que funciona" en un gate reproducible; y registrar las reversiones (caso
  classify) vale tanto como los éxitos.
- **Versionar prompts como código**: constante + changelog + gate de CI + sellado en los
  artefactos que produce.

## 6. Pendientes conocidos

- Fallback automático de proveedor LLM (hoy: fallback por llamada con `low_confidence`, no
  failover de proveedor) — mediano plazo auditoría v4.
- Few-shot solo en `evaluate`; evaluar si `classify`/`answer` lo ameritan.

## 7. Trazabilidad

- Tests: `test_llm_provider.py` (17), `test_cost_routing.py` (10), `test_metrics.py`,
  `test_traces.py`, `test_pipeline_llm.py`, `test_semantic_cache.py`.
- Golden/red team: `tests/golden/golden_set.json` (28 casos), `tests/redteam/redteam_set.json` (12).
- ADR: `docs/adr-seleccion-modelo.md`. Migraciones: `0020` (latencia), `0021` (prompt_version),
  `0024` (trazas).
