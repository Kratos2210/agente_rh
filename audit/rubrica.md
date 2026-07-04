# Reorganizar el código hacia la estructura de la rúbrica `audit/chekeo.md`

## Contexto
La rúbrica pide 7 carpetas por componente (`retrieval/ ranking/ orquestacion/ agente/ adaptadores_mcp/
observabilidad/ despliegue/`) **con el fin de organizar el código y hacerlo mantenible**. En la iteración
anterior se crearon como carpetas-puntero (signpost, solo README). El usuario aclaró que la intención es
**mover el código de verdad** a esas carpetas (mantenibilidad), no documentarlo. Este plan aborda esa
reorganización real.

## Realidad de ingeniería (medida, no supuesta)
- **La app tiene ~10 dominios, no 7.** Fuera de la rúbrica quedan: `api/` (FastAPI app, routes, auth,
  scheduler, ratelimit, runtime), `db/`, `notifications/`, `channels/`, `integrations/`, `evaluation/`,
  `supabase/`, `frontend/`. Un move a *solo* 7 carpetas dejaría >50% del código sin hogar.
- **Sin mapeo 1:1** (archivos que reclaman 2-3 carpetas): `src/qa_chain.py` (retrieval+ranking+orquestación),
  `agent/llm.py` (orquestación+observabilidad, 18 importadores), `agent/rag.py` (retrieval+ranking),
  `agent/answer_cache.py` (retrieval+orquestación).
- **Infra transversal sin carpeta**: `src/config.py` (40 importadores), `src/logging_config.py` (25),
  `src/registry.py`.
- **Blast radius**: `src` 52 archivos / `agent` 37 / `api` 38. Imports absolutos (`src.*`, `agent.*`) vía
  `sys.path` en `tests/conftest.py` + 11 scripts. Mecánicamente reescribible; la suite de **330 tests** es la red.
- **Touchpoints no-Python (pocos)**: `Dockerfile.backend` COPY 28-35 + `CMD uvicorn api.main:app` (48);
  `.github/workflows/ci.yml` refs a `agent/prompts.py` (gate). K8s/compose/Caddyfile/deploy.sh **no** se tocan.

**Conclusión clave:** un move *literal a solo 7 carpetas* pelearía con la arquitectura (rompe módulos
cohesivos, huérfana config, fuerza carpetas extra) y **no** mejoraría la mantenibilidad. El valor real está en
(a) matar el grab-bag `src/` (herencia del fork agente_pro: mezcla retrieval/ranking/orquestación/observabilidad
+ config/logging/registry) y (b) nombrar los paquetes de IA como pide la rúbrica, **preservando** los dominios
del producto que ya están bien organizados.

## Enfoque recomendado — Reorg moderada, cohesión-preservante y alineada a la rúbrica
Mover código real a las carpetas de la rúbrica **donde el mapeo es limpio**, partir el `src/` incohesivo, y
mantener los dominios del producto. Reescritura de imports mecánica + verificación con los 330 tests.

**Movimientos (git mv, preservando historia):**
| Destino (rúbrica) | Se mueve aquí | Nota |
|---|---|---|
| `retrieval/` | `src/vectorstore.py`, `src/embeddings.py`, `src/semantic_cache.py`, `agent/rag.py`, `agent/answer_cache.py` | BD vectorial + hybrid + caché semántica |
| `ranking/` | `src/reranker.py` | Re-ranker cross-encoder |
| `orquestacion/` | `src/qa_chain.py`, `src/classifier.py`, `agent/llm.py` | LLM + cadenas (llm.py va acá: es la abstracción; el metering es secundario) |
| `agente/` | `agent/graph.py`, `agent/state.py`, `agent/nodes.py`, `agent/prompts.py`, `agent/service.py`, `agent/sourcing_service.py` | LangGraph cíclico (renombra `agent/`) |
| `adaptadores_mcp/` | `api/mcp.py` | Servidor MCP (importa `api.routes.*`; una dependencia one-way) |
| `observabilidad/` | `src/observability.py`, `api/httpmetrics.py` | Trazas/métricas. `logging_config.py` → ver `core/` |
| `despliegue/` | contenido de `deploy/` (renombre `deploy/`→`despliegue/`) | deploy.sh + k8s |
| `core/` (nuevo, infra transversal — no rúbrica) | `src/config.py`, `src/logging_config.py`, `src/registry.py` | Lo que ninguna de las 7 reclama; evita huérfanos |

**Se mantienen como están (dominios del producto, fuera de la rúbrica):** `api/` (menos `mcp.py`), `db/`,
`notifications/`, `channels/`, `integrations/`, `evaluation/`, `supabase/`, `frontend/`, `scripts/`, `tests/`.

**Reescritura de imports (mecánica, guiada por tests):**
- Mapa de renombres módulo→módulo (p. ej. `src.vectorstore`→`retrieval.vectorstore`, `agent.graph`→
  `agente.graph`, `src.config`→`core.config`, `api.mcp`→`adaptadores_mcp.mcp`).
- Reescribir todos los `from X import` / `import X` con ese mapa en TODO el repo (código, `tests/`, `scripts/`),
  con `grep` + edición dirigida (no sed ciego: verificar cada patrón).
- Cada carpeta destino necesita `__init__.py` si el paquete origen lo tenía; borrar los README signpost (los
  reemplaza el código real) o convertirlos en encabezado del paquete.
- Actualizar los 2 touchpoints no-Python: `Dockerfile.backend` (COPY + entrypoint si cambia `api`→se queda
  `api`, así que el entrypoint `api.main:app` NO cambia; sí cambian las COPY de las carpetas movidas) y el
  gate de `agent/prompts.py`→`agente/prompts.py` en `ci.yml`.

## Alcance RESUELTO (aclaración del usuario)
Las 7 carpetas de la rúbrica **deben existir y contener lo que les corresponde**, pero **pueden coexistir**
otras carpetas para las demás funciones (la rúbrica no lo prohíbe). → Es la **reorg moderada** de arriba: se
llenan las 7 carpetas con su código real y los dominios del producto (`api/ db/ notifications/ channels/
integrations/ evaluation/`) conservan sus carpetas.

**Decisiones de frontera (defaults elegidos; el usuario puede vetar en la revisión del plan):**
- `agent/llm.py` → `orquestacion/` (es la abstracción del LLM; el metering/tracing es secundario y viaja con él).
- Infra transversal (`src/config.py` 40 imports, `src/logging_config.py` 25, `src/registry.py`) → **`core/`**
  (carpeta nueva, no-rúbrica; legítima según la aclaración). Evita huérfanos y un `src/` casi vacío.
- Renombres a los nombres de la rúbrica: `agent/`→`agente/`, `deploy/`→`despliegue/`, `api/mcp.py`→
  `adaptadores_mcp/mcp.py`. `src/` desaparece (su contenido se reparte en retrieval/ranking/orquestacion/
  observabilidad/core).
- El paquete `agent/` se **reparte por concern** (rag→retrieval, llm→orquestacion, graph/state/nodes/prompts/
  service→agente). Resulta un layering acíclico: `agente → orquestacion → retrieval → ranking → core`.

## Alternativas descartadas
- **Move literal a solo 7 carpetas**: forzaría inventar sub-carpetas para api/db/etc. y partir archivos
  cross-cutting. Descartada por el usuario (otras carpetas pueden coexistir).
- **Dejar signposts (estado actual)**: no cumple la intención de "organizar el código". Descartada.

## Verificación (obligatoria — NO se commitea nada)
> **Instrucción del usuario:** al finalizar la ejecución **no commitear**. Dejar todos los cambios en el
> working tree y correr TODA la verificación para garantizar que nada se rompa; el usuario revisa y decide.
> Si algún check falla, arreglar in situ hasta dejar todo verde (sin commit).

1. `uv run pytest -q` → **330/330 verde** (la red de seguridad del refactor). Es el check central: si pasa,
   los imports movidos resuelven en todo el código, tests y (los importados por) scripts.
2. Smoke de arranque: `uv run uvicorn api.main:app` levanta sano (`/api/health` ok) — valida que los imports
   del runtime resuelven.
3. `cd frontend && npx tsc --noEmit` → OK (el frontend no debería verse afectado; confirmarlo).
4. `deploy/deploy.sh validate` (o `despliegue/deploy.sh validate`) → kubeconform 7/7.
5. `docker build -f Dockerfile.backend .` build OK (valida las COPY nuevas) — opcional si Docker disponible.
6. `git grep -nE "from (src|agent)\.|import (src|agent)\b"` → **0 referencias** a los paquetes viejos.
7. `git status`: renombres detectados como `R` (historia preservada) + imports editados.

## Logística de ejecución (presupuesto de tokens ~94%)
- **Paso 0:** guardar ESTE plan en `audit/rubrica.md` (copia del plan file) para no perderlo.
- **Checkpointing a memoria:** tras cada paso con avance real (carpetas movidas, tanda de imports reescrita,
  tests verde), escribir/actualizar un memory (`project`) con el estado exacto (qué se movió, mapa de
  renombres aplicado, qué falta, resultado de pytest) → si la sesión se corta, se retoma sin perder avance.
- **Sin commit** en ningún momento (instrucción del usuario). Trabajar en la rama actual.

## Orden de ejecución sugerido (para minimizar ventanas rotas)
1. Rama nueva; descartar los README signpost previos (los reemplaza el código real).
2. `git mv` de los archivos a sus carpetas destino (+ `__init__.py` por paquete nuevo).
3. Construir el mapa de renombres módulo→módulo y reescribir imports en TODO el repo (código, `tests/`,
   `scripts/`) con grep dirigido por patrón (no sed ciego).
4. Actualizar los 2 touchpoints no-Python: `Dockerfile.backend` (líneas COPY de las carpetas movidas; el
   entrypoint `api.main:app` **no** cambia porque `api/` se mantiene) y `ci.yml` (`agent/prompts.py`→
   `agente/prompts.py` en el gate, ~5 líneas).
5. Verificación completa (sección de arriba). **No commitear** — dejar todo en el working tree para tu
   revisión, con los tests en verde y el arranque sano.

## Cierre (sin commit)
- **No se ejecuta `git commit`.** Al terminar: reporto qué se movió, el resultado de `pytest` (330/330),
  el smoke de arranque, `tsc` y `deploy.sh validate`, y dejo `git status` a la vista para que revises.
- Los README signpost previos (aún sin commitear en la rama) se descartan como parte del refactor (el código
  real ocupa ahora esas carpetas).

## Nota
Refactor mecánico grande (≈90 archivos con imports) pero de bajo riesgo con la suite de 330 tests como red.
Reversible vía git si algo sale mal. La app en runtime no cambia de comportamiento (solo se mueven módulos).
