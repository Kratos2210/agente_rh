# Test — Estrategia de calidad: unit, invariantes, golden y red team

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `tests/` (52 suites, ~467 tests)

## 1. Propósito y alcance

Cómo se verifica el sistema: la suite pytest (rápida, sin red), los tests estructurales que
imponen invariantes, los bancos golden contra el LLM real, el red teaming y los smokes en vivo.
La calidad EN producción (juez continuo) está en [Observabilidad.md](Observabilidad.md).

## 2. Decisiones de diseño

- **Fakes en las costuras, no mocks por todos lados**: el motor recibe LLM/retriever/caché
  falsos; los endpoints reciben repos falsos. La suite completa corre en segundos sin DB, red
  ni torch — y por eso corre SIEMPRE (local y CI).
- **Invariantes estructurales**: en vez de confiar en el review, tests que recorren la app e
  imponen el patrón — `test_tenant_guards.py` (toda ruta protegida y con guard de tenant),
  `test_estados.py` (paridad backend↔frontend del catálogo de estados). Un endpoint futuro que
  olvide el patrón rompe CI.
- **Separar determinista de estocástico**: pytest fija la lógica con FakeLLM; la calidad del
  modelo real se mide aparte con **bancos golden** (rangos esperados, no igualdad exacta) que
  corren en nightly — no bloquean cada push con flakiness de LLM.
- **Red teaming versionado**: los ataques son un set JSON con **guardias puras** por caso
  (`evaluate_breach`, `answer_breach`, …) — reproducible, ampliable y corriendo en nightly.
- **Los smokes en vivo complementan, no reemplazan**: bugs como el embed objeto-vs-lista de
  PostgREST o el re-sync duplicado salieron en vivo; cada verificación mayor incluye un paseo
  contra DB real + Groq.

## 3. Diseño e implementación

### Suite pytest (`uv run pytest`)
52 archivos `test_*.py`. Dominios: motor/entrevista (`test_interview`, `test_routing`,
`test_iteration_limits`, `test_inactivity`, `test_multistage`, `test_scheduling`), evaluación
(`test_prescreen`, `test_integrity`, `test_quality`), plataforma (`test_auth`,
`test_tenant_guards`, `test_users`, `test_ratelimit`, `test_listing`, `test_webhook`,
`test_outbox`, `test_documents`, `test_storage`), LLM ops (`test_llm_provider`,
`test_cost_routing`, `test_costs`, `test_traces`, `test_latency`, `test_sla`, `test_mcp`,
`test_redteam_harness`, `test_golden_harness`), y regresiones puntuales (`test_backlog_close`,
`test_contact`, `test_conversation_lock`, `test_token_redaction`, `test_prod_profile`, …).

### Bancos contra el LLM real (offline/nightly)
- **Golden** `tests/golden/golden_set.json` — 28 casos, 4 suites: `evaluate` 11 (respuestas
  reales de Alberto + vagas + inyección + fuera-de-tema), `classify` 7, `slot` 6, `prescreen` 4.
  Runner `scripts/golden_eval.py` (`--suite`, `--case`, `--model` para bancos de aceptación de
  modelos candidatos); exit 1 fuera de rango.
- **Retrieval** `tests/golden/retrieval_set.json` + `scripts/retrieval_eval.py` (hit@k, sin LLM).
- **Red team** `tests/redteam/redteam_set.json` — 12 ataques a los 4 puntos de inyección +
  `scripts/redteam_eval.py`; estado 12/12 contenidos.
- **Juez de groundedness** `scripts/groundedness_judge.py` sobre trazas reales (`--sample`,
  `--min-rate`).

### Gotchas de la suite
- `test_mcp_disabled_by_default` lee el `.env` real: falla (sin ser regresión) si el entorno
  local dejó `MCP_ENABLED=true`.
- Los FakeLLM extraen la respuesta real con regex sobre los delimitadores
  `<<<respuesta>>>…<<<fin>>>`: los ejemplos few-shot del prompt usan comillas «» a propósito
  para no romper el extractor.
- Guards anti-N+1: fakes que truenan si un listado recae en la ruta por-item.

## 4. Contratos e invariantes

- La suite es verde SIEMPRE antes de commit; un test rojo conocido se documenta con su causa.
- Ningún test de la suite necesita red, credenciales ni servicios levantados.
- Todo bug encontrado en vivo deja un test de regresión con su nombre apuntando a la causa.
- Golden y red team se amplían al tocar prompts (el gate de `PROMPT_VERSION` obliga a mirar).

## 5. Patrones reutilizables

- **Diseñar las costuras para fakes** (inyección del LLM/retriever/repos) es lo que hace
  posible una suite de segundos en un sistema de IA.
- **Tests estructurales para políticas transversales** (auth, tenancy, catálogos espejo):
  imponen el patrón a código que aún no existe.
- **Golden con rangos + contraejemplos + exit code**: evaluación de LLM utilizable como gate
  de CI/nightly.
- **Red team como dataset con guardias puras**: separar "el ataque" (JSON) de "cómo se detecta
  la brecha" (función pura testeada).

## 6. Pendientes conocidos

- Cobertura E2E de navegador automatizada (hoy smokes Playwright manuales en verificaciones).
- Golden de evaluate multi-rubro (hoy centrado en la vacante demo).

## 7. Trazabilidad

- CI: los 467 tests corren en `ci.yml`; golden/red-team en `nightly-quality.yml`
  (ver [CI_CD.md](CI_CD.md)).
- Verificación e2e viva: `scripts/verify_multistage.py`, `scripts/demo.py --alberto`.
