# 📊 Informe de Auditoría LLMOps v2: Agente de Selección de Talento (`agente_rh`)

**Fecha:** 2026-07-03 (tarde — re-auditoría post-roadmap; la v1 es de la mañana del mismo día)
**Auditor:** Auditoría automatizada con Claude Code (exploración directa del código fuente, historial de CI real y PRs — no de una descripción)
**Frameworks aplicados:** `audit/auditoria_one.md` (3 fases, madurez 1–4, score 0–100, matriz de riesgos, roadmap) + `audit/auditoria_two.md` (madurez 1–5, 10 sub-áreas × 4 dimensiones: Procesos / Herramientas / Equipo / Métricas)
**Línea base:** `audit/auditoria_final.md` (v1: Nivel 3, 72/100, 3.2/5) — este informe re-puntúa contra ella
**Fuentes de evidencia:** código en `agent/`, `evaluation/`, `api/`, `src/`, `integrations/`, `notifications/`, `deploy/`; migraciones `supabase/migrations/0001–0026`; suite de tests (330 verdes, 40 archivos); `scripts/{golden_eval,groundedness_judge,retrieval_eval}.py`; `docs/{arquitectura,despliegue,gestion_secretos,adr-seleccion-modelo}.md`; **runs reales de GitHub Actions** (`gh run list`: CI 5 jobs en verde en cada push/PR desde 2026-07-03; nightly-quality ejecutado success); **PRs #1 y #2 mergeados con CI verde**.

> **Alcance y método.** Igual que la v1: cada afirmación traza a un archivo, símbolo, migración o run de
> CI verificable. Donde el framework pregunta por algo que no existe, se dice explícitamente. Contexto de
> calibración: producto pre-lanzamiento de un solo desarrollador con vocación SaaS multi-empresa, que
> procesa datos personales de candidatos en Perú (Ley 29733). **La diferencia central con la v1:** entre
> ambas auditorías se ejecutó el roadmap completo de 5 pasos que la v1 recomendó, más el flujo de
> trabajo por PR. Esta v2 mide cuánto movió la aguja y qué queda.

---

## 1. Resumen Ejecutivo y Nivel de Madurez

- **Nivel de Madurez Estimado (escala framework 1, 1–4):** **Nivel 3 — Producción Robusta, CONSOLIDADO y en el umbral del Nivel 4**. Las dos anclas que en la v1 impedían mirar al Nivel 4 (CI inerte; evaluación de calidad no continua) están cerradas. Lo que hoy separa del Nivel 4 (LLMOps Empresarial) ya no es ingeniería: es operación (equipo unipersonal, secretos planos, features de calidad apagadas por defecto sin un perfil de producción que las encienda).
- **Nivel de Madurez Estimado (escala framework 2, 1–5):** **3.6 — Definido alto, con mayoría de picos "Gestionado" (4)**. La dimensión Procesos promedia **4.1** (ya en Gestionado); el ancla sigue siendo Equipo (**2.6**, realidad unipersonal sin cambios posibles por código).
- **Puntuación General:** **81/100** (v1: 72/100, **+9**)
- **Veredicto Breve:** El roadmap de la v1 se ejecutó completo y con evidencia: el CI corre en cada push/PR (5 jobs, incluido un gate de versión de prompts), un nightly valida el golden contra el LLM real, hay entornos dev/prod separados, el bot escala a réplicas con webhook, la calidad de la IA es un signo vital diario persistido y alertado, y el costo tiene dos palancas activas (routing a modelo barato **validado con banco de aceptación** + caché semántica de dudas) con ADR de selección de modelo. Los 3 riesgos de la v1 quedaron: 1 cerrado, 2 mitigados. Los riesgos residuales son de operación humana y de configuración por defecto — no de diseño ni de tooling.

---

## 2. Delta v1 → v2 (qué movió la aguja)

### 2.a Puntajes por sub-área

| Fase | Sub-área | v1 | v2 | Δ | Causa del movimiento (evidencia) |
|---|---|:--:|:--:|:--:|---|
| Ideación | Data Sourcing | 3.0 | 3.0 | = | Sin cambios en esta sub-área |
| Ideación | Selección de modelo | 2.5 | **3.75** | ▲ +1.25 | ADR formal (`docs/adr-seleccion-modelo.md`), banco de aceptación (`golden_eval.py --model`), **2 modelos alternativos benchmarkeados y archivados** (llama-3.1-8b-instant 13/13; gpt-oss-20b 13/13) — exactamente las 2 recomendaciones v1 |
| Desarrollo | Prompt Engineering | 3.5 | **3.75** | ▲ +0.25 | Versionado **exigido por CI** (`prompt-version-gate`: cambiar `agent/prompts.py` sin subir `PROMPT_VERSION` rompe el build) + regresión nightly contra LLM real |
| Desarrollo | Cadenas y Agentes | 4.0 | 4.0 | = | Diseño ya era techo; la advertencia v1 del lock por-proceso **pasó de hipotética a activa** (prod overlay usa replicas:2) → ver Riesgo 3 |
| Desarrollo | RAG vs Fine-Tuning | 3.0 | **3.5** | ▲ +0.5 | Golden de retrieval (hit@k offline, 6 casos, umbral 0.8, `scripts/retrieval_eval.py`) + relevancia de respuesta juzgada continuamente; relevancia de contexto sigue sin medirse |
| Desarrollo | Testing | 3.0 | **3.75** | ▲ +0.75 | **CI vivo** en cada push/PR (los 330 tests + lint + tsc + docker + kubeconform corren solos), nightly golden en verde contra Groq desde Actions, flujo por PR con CI antes del merge |
| Operación | Despliegue y UX | 3.0 | **3.5** | ▲ +0.5 | Entornos dev/prod (overlays kustomize + fix del gate `ENVIRONMENT=production`), webhook dual-mode → prod `replicas:2` + RollingUpdate (deploy sin corte), caché semántica ya cableada (era código muerto) |
| Operación | Monitoreo y Observabilidad | 4.0 | 4.0 | = | Ya era la sub-área más fuerte; suma el signo vital de calidad y el destino de alertas de equipo (`ops_alert_email`), pero Equipo sigue en 2 y falta post-mortem formal |
| Operación | Gestión de Costos | 3.0 | **3.5** | ▲ +0.5 | Routing por etapa a modelo barato **validado** (13/13), caché de dudas (hit = 0 tokens), ADR con tabla de palancas y rutina de revisión mensual |
| Operación | Gobernanza y Seguridad | 3.5 | 3.5 | = | Suma flujo PR + hook pre-push + gate de prompts; pero secretos siguen en `.env` plano y la protección de rama no es server-side (plan Free) — se compensan |

**Promedio global: 3.2 → 3.625 ≈ 3.6/5.** Por dimensión: Procesos 3.5→**4.1**, Herramientas 3.5→**3.9**, Equipo 2.6→**2.6**, Métricas 3.3→**3.7**.

### 2.b Estado de los 3 riesgos críticos de la v1

| # v1 | Riesgo v1 | Estado | Evidencia del cierre/mitigación |
|:--:|---|:--:|---|
| 1 | Cadena de calidad no ejecutada (CI inerte + evaluación LLM manual) | ✅ **CERRADO** | Remote `github.com/Kratos2210/agente_rh`; CI 5 jobs success en cada push/PR (runs verificados vía `gh run list`); `prompt-version-gate`; `nightly-quality.yml` ejecutado en verde (golden 28/28 contra Groq desde Actions); juez gated a DB accesible (auto-skip limpio mientras la DB sea local). El primer run de CI además **destapó y corrigió un bug real** (sentinel monotonic de los sweeps en host recién booteado) — la cadena ya trabaja. |
| 2 | Punto único de fallo operativo y humano | 🟡 **MITIGADO** | Webhook dual-mode desbloquea `replicas:2` + RollingUpdate en prod (`deploy/k8s/overlays/prod/backend-scale-patch.yaml`); el scheduler ya era multi-réplica (advisory lock); alertas ops/SLA con fallback a correo de equipo (`ops_alert_email`). **Residuo:** sigue habiendo un solo operador humano y los secretos siguen en `.env`/Secret plano (runbook listo, migración pendiente de decisión externa). |
| 3 | Calidad del LLM sin medición continua | 🟡 **MITIGADO (mecanismo completo, apagado por defecto)** | Juez compartido `evaluation/quality.py` (fundamentación + relevancia de respuesta en una llamada), `_quality_sweep` diario por tenant → tabla `quality_metrics` (migración `0026`) + tarjeta semáforo en `/observabilidad` + alerta por correo bajo umbral. **Residuo:** `quality_alerts.enabled=False` y `LLM_TRACE_ENABLED=false` por defecto — el signo vital existe pero nace apagado (ver Riesgo 1 de esta v2); relevancia de contexto aún sin juez (el golden de retrieval cubre hit@k offline). |

---

## 3. Evaluación por Fases (Hallazgos y Brechas)

Cada sub-área sigue la plantilla del framework 2. Para no repetir la v1, la justificación se centra en **lo que cambió** y re-valida lo que sigue igual; la evidencia estable de la v1 (secciones 2.x) sigue vigente salvo indicación.

### 🔵 FASE 1: IDEACIÓN

#### 3.1.1 Data Sourcing — 📊 Puntaje: 3/5 (v1: 3 — sin cambios)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 3 | Pipeline sourcing→gate→contacto formalizado; sin contrato de calidad de fuentes reales aún |
| Herramientas | 3 | Protocol+factory extensible; dedupe idempotente por `source_ref` |
| Equipo | 2 | Sin roles de gobernanza de datos (unipersonal) |
| Métricas | 3 | Embudo completo medido + gate de CV con puntaje y costo |

**🔍 Justificación.** Sin cambios desde la v1: el ciclo de vida de PII (consentimiento → retención → erasure, Ley 29733) sigue siendo la fortaleza diferencial; las dos recomendaciones v1 (contrato de calidad del conector real; sanear correos reales del fixture) siguen abiertas y siguen siendo las correctas — ninguna bloqueaba el roadmap.

**🎯 Recomendaciones** (heredadas, vigentes)
1. Contrato de calidad mínima del CV en el conector antes de enchufar una fuente real.
2. Fixture 100 % sintético al migrar a staging.

#### 3.1.2 Selección del Modelo Base — 📊 Puntaje: 3.75/5 (v1: 2.5 — ▲ la sub-área que más subió)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Proceso formal documentado Y ejecutado: ADR + banco de aceptación + procedimiento de cambio de modelo |
| Herramientas | 4 | `golden_eval.py --model` = arnés de benchmark reproducible; comparación por modelo en `llm_usage`/`by_model` |
| Equipo | 3 | Trade-offs comprendidos y ahora escritos (matriz del ADR) |
| Métricas | 4 | Latencia p50/p95/p99, costo y errores por modelo + **resultados de benchmark archivados** (tabla de candidatos en el ADR) |

**🔍 Justificación.** Las dos recomendaciones de la v1 se ejecutaron literalmente. (1) **ADR formal** `docs/adr-seleccion-modelo.md`: matriz latencia/costo/calidad/español/privacidad de Qwen3-32B@Groq, con la **residencia de datos PII (proveedor EE.UU. vs Ley 29733) declarada como pendiente de producción real** — la pregunta incómoda que la v1 señaló ("si mañana hay que defender ante un cliente por qué sus datos pasan por Groq, no hay documento") hoy tiene documento. Incluye alternativas consideradas (frontier, local/on-prem, statu quo) y el procedimiento de cambio de modelo en 5 pasos. (2) **Benchmark sistemático**: `scripts/golden_eval.py --model <candidato>` corre las suites contra cualquier modelo sin tocar `.env`; se midieron y archivaron 2 candidatos (`llama-3.1-8b-instant` classify 7/7 + slot 6/6, elegido; `openai/gpt-oss-20b` 7/7 + 6/6, alternativa apta) — tabla en el ADR con fecha y veredicto.

**✅ Fortalezas**
- La decisión de modelo dejó de ser tácita: es reproducible (comando documentado), comparada (2 candidatos) y auditable (ADR con fecha).
- El banco de aceptación convierte cualquier cambio futuro de modelo en un trámite de minutos con criterio objetivo.

**⚠️ Debilidades**
- El benchmark cubre las suites de las etapas ruteadas (classify/slot); un cambio del modelo PRINCIPAL exigiría correr también evaluate/prescreen (el procedimiento del ADR lo dice, pero nadie lo ha necesitado aún).
- La residencia de datos sigue siendo un pendiente declarado, no resuelto (correcto para pre-lanzamiento; bloqueante para clientes con exigencia de cumplimiento).

**🎯 Recomendaciones**
1. Al primer cliente real con exigencia de datos: ejecutar la evaluación de proveedor con residencia/acuerdo de tratamiento que el ADR deja planteada.
2. Archivar en el ADR la corrida completa (4 suites) del modelo principal como línea base datada.

### 🟢 FASE 2: DESARROLLO

#### 3.2.1 Prompt Engineering — 📊 Puntaje: 3.75/5 (v1: 3.5 — ▲)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 5 | Versionado ya no es disciplina: es **gate de CI** (build roto si cambia el prompt sin subir versión) + regresión nightly |
| Herramientas | 3 | Sistema propio (prompts como código + trazas + LangSmith opcional) — adecuado a la escala, sin cambios |
| Equipo | 3 | Sin cambios |
| Métricas | 4 | Golden con rangos por caso incl. adversariales; efectividad por versión sellada en datos |

**🔍 Justificación.** El cambio es cualitativo en Procesos: la regla "cambiar prompt ⇒ subir `PROMPT_VERSION` + correr golden" era documentación en la v1; hoy es **mecánica**: el job `prompt-version-gate` de `ci.yml` compara el diff de `agent/prompts.py` contra la base del PR y rompe el build si la versión no subió, y `nightly-quality.yml` corre la suite golden completa contra el LLM real cada madrugada (02:00 Lima) — la primera corrida real desde Actions salió 28/28. Todo lo demás de la v1 sigue vigente (anti-inyección sistemática, sellado en datos, zero-shot deliberado).

**⚠️ Debilidades** (persistentes)
- Zero-shot puro: el experimento few-shot sugerido en la v1 (2 ejemplos en `EVALUATE_ANSWER_PROMPT`, comparar contra el golden) sigue sin correrse.
- Sin changelog de por qué cambia cada versión de prompt (solo el diff de git).

**🎯 Recomendaciones**
1. El experimento few-shot de la v1 sigue siendo el siguiente movimiento de mayor valor/esfuerzo en esta sub-área.
2. Nota de 1 línea por bump de `PROMPT_VERSION` (en el propio archivo o en el PR).

#### 3.2.2 Cadenas y Agentes (Orquestación) — 📊 Puntaje: 4/5 (v1: 4 — sin cambio de puntaje, con una advertencia que se activó)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Sin cambios: diseño documentado y deliberado |
| Herramientas | 4 | LangGraph + checkpointer Postgres durable; sin cambios |
| Equipo | 4 | Sin cambios |
| Métricas | 4 | Latencia por etapa y e2e del turno; topes de iteración; sin cambios |

**🔍 Justificación.** El diseño (grafo de un nodo + dispatch en Python puro + estado durable) sigue siendo el techo de la escala y no necesitaba cambios. **Pero la debilidad que la v1 registró como hipotética se volvió condición real:** la serialización por conversación es un `threading.Lock` por `thread_id` **en memoria del proceso** (`agent/service.py`), y el overlay de producción ahora despliega `replicas: 2` en modo webhook — dos updates del mismo chat podrían procesarse concurrentemente en pods distintos y pisar el mismo checkpoint. La probabilidad es baja (Telegram entrega por chat de forma mayormente secuencial y el turno típico dura segundos), pero el escenario existe justo en el modo que el propio roadmap habilitó. Está recogido como **Riesgo 3** de esta auditoría; la mitigación natural ya tiene patrón en el repo (advisory lock de Postgres, como el del scheduler, keyed por thread).

**🎯 Recomendaciones**
1. Antes de operar `replicas>1` con tráfico real: advisory lock de Postgres por `thread_id` en `InterviewService.process()` (patrón ya existente en `api/scheduler.py`), o afinidad de sesión en el ingress.

#### 3.2.3 RAG vs. Fine-Tuning — 📊 Puntaje: 3.5/5 (v1: 3.0 — ▲)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Sin cambios: decisión RAG justificada y correcta |
| Herramientas | 4 | Pipeline híbrido BM25+vectorial+re-ranker con degradación en capas; sin cambios |
| Equipo | 3 | Sin cambios |
| Métricas | 3 | **Nuevo:** golden de retrieval hit@k offline + relevancia de respuesta juzgada; falta relevancia de contexto |

**🔍 Justificación.** La brecha señalada en la v1 ("la sub-área con mayor distancia entre calidad de implementación y calidad de medición") se cerró a la mitad, con las dos piezas que la v1 pidió: (1) **golden de retrieval** — `tests/golden/retrieval_set.json` (6 dudas de la vacante demo → substring esperado en el contexto recuperado) + `scripts/retrieval_eval.py` que corre el retriever REAL de `agent/rag.py` sobre `company_kb` y mide **hit@k** con umbral 0.8 (exit 1 si cae; usable en nightly; no gasta LLM); (2) **relevancia de respuesta** — el juez compartido de `evaluation/quality.py` evalúa en la misma llamada si la respuesta atiende la pregunta del candidato, y el barrido diario la persiste como métrica. Lo que falta es la tercera pata RAGAS: **relevancia de contexto** con juez (¿el chunk recuperado era el pertinente?) — el propio módulo lo declara ("NO se mide aquí") en su docstring, honestidad que se agradece al auditar.

**⚠️ Debilidades**
- Relevancia de contexto sin juez (el hit@k del golden es un proxy binario offline, no una medición sobre tráfico real).
- El set de retrieval tiene 6 casos sobre 1 vacante — suficiente como humo, corto como cobertura cuando haya varias vacantes reales.

**🎯 Recomendaciones**
1. Juez de relevancia de contexto sobre las trazas `answer` (el prompt del juez ya recibe la pregunta; falta pasarle el contexto recuperado por separado).
2. Crecer el retrieval set con cada vacante real que se cargue (2-3 dudas por vacante).

#### 3.2.4 Testing — 📊 Puntaje: 3.75/5 (v1: 3.0 — ▲ el cierre del talón de Aquiles)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 5 | Multi-capa Y automatizado: CI en cada push/PR + nightly con LLM real + gate de prompts + flujo por PR |
| Herramientas | 4 | Arnés propio completo (golden 4 suites + juez dual + retrieval eval + CI); sin RAGAS/red-teaming externos |
| Equipo | 2 | Sin QA dedicado ni evaluadores humanos formales (sin cambios) |
| Métricas | 4 | Rangos por caso, tasas con umbral, hit@k — y ahora con historial de ejecución real (runs de Actions) |

**🔍 Justificación.** El talón de Aquiles de la v1 ("nada de esto corre automáticamente; el único gate real es la disciplina del desarrollador") está cerrado con evidencia de ejecución, no solo de configuración: **el CI corrió en cada push y PR de hoy** (6 runs success verificados, ~1 min cada uno, 330 tests + lint + tsc + build Docker + kubeconform dev+prod + prompt-version-gate), el **nightly** corrió el golden 28/28 contra Groq **desde Actions** (secrets configurados), y el juez de fundamentación queda gated a DB accesible con skip explícito (correcto mientras la DB sea local). Bonus que valida la cadena: el primer run de CI destapó un bug real de los sweeps (sentinel `0.0` con `time.monotonic()` en host recién booteado) que los tests locales no podían ver — el pipeline ya paga su costo. La suite creció 297→330 (webhook 9, SLA +2, calidad 12, cost routing 10).

**✅ Fortalezas**
- Red de regresión de 3 velocidades: determinista en cada cambio (CI), semántica cada noche (golden vs LLM real), continua en producción (quality sweep, cuando se encienda).
- El flujo por PR (estrenado hoy con los PRs #1 y #2) pone el CI ANTES del merge, no después.

**⚠️ Debilidades**
- El "required check" no es server-side (plan GitHub Free en repo privado): el merge con CI verde es disciplina + hook `pre-push` local, no una regla del servidor.
- Red teaming sigue sin ser proceso (solo los adversariales del golden); sin evaluadores humanos estructurados.

**🎯 Recomendaciones**
1. Al pasar a plan Pro (o repo público): activar branch protection con los 5 checks requeridos — la configuración ya se intentó y está documentada.
2. Sesión de red teaming asistida por LLM sobre el flujo Telegram→prompt→scorecard (heredada de la v1, sigue pendiente y sigue siendo barata).

### 🔴 FASE 3: OPERACIÓN

#### 3.3.1 Despliegue y UX — 📊 Puntaje: 3.5/5 (v1: 3.0 — ▲)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | CI/CD ejecutándose + entornos separados + rolling en prod; sin canary/A-B; deploy sigue siendo manual |
| Herramientas | 4 | Overlays kustomize dev/prod validados en CI + deploy.sh + webhook dual-mode |
| Equipo | 3 | Sin cambios |
| Métricas | 3 | Los runs de CI dan duración y tasa de éxito del build; sin métricas de release en runtime |

**🔍 Justificación.** Tres de las cuatro carencias que la v1 enumeró se cerraron: (1) **CI/CD ya no es inerte** — valida build, imagen y manifiestos en cada cambio (el despliegue en sí sigue siendo manual vía `deploy.sh`, aceptable sin cluster productivo); (2) **entornos separados** — `deploy/k8s/` pasó a base + overlays (`agente-rh-dev`: polling, 1 réplica, Recreate; `agente-rh-prod`: webhook, 2 réplicas, RollingUpdate, dominio y recursos propios), validados ambos con kubeconform en el CI; el mapeo destapó y corrigió un bug real (el ConfigMap usaba `APP_ENV`/`OPENAI_BASE_URL`, nombres que pydantic ignora → el gate de producción nunca se activaba); (3) **el bot dual-mode** (polling dev / webhook prod con secret validado en tiempo constante) desbloquea réplicas y rolling — deploy sin corte. (4) La **caché semántica ya no es código muerto** (cableada en la etapa `answer`, ver 3.3.3). Siguen fuera: canary/A-B (sin tráfico real que lo justifique aún) y streaming SSE (correctamente descartado para Telegram; la métrica pertinente — latencia e2e del turno — ya existe).

**⚠️ Debilidades**
- El e2e Telegram→webhook→pod **no se ha probado en vivo** (requiere ingress HTTPS público; probarlo desde localhost contra el bot demo rompería su polling). El modo webhook está cubierto por 9 tests y smoke local de la ruta, pero el circuito completo con Telegram real es fe + tests, no evidencia.
- Deploy manual sin registro de releases (qué versión corre dónde).

**🎯 Recomendaciones**
1. Primer despliegue a un cluster real (aunque sea efímero) con ingress público para cerrar el e2e webhook con Telegram de verdad.
2. Etiquetar imágenes por SHA/versión en el CI y registrar qué tag corre en cada entorno.

#### 3.3.2 Monitoreo y Observabilidad — 📊 Puntaje: 4/5 (v1: 4 — se mantiene, con más superficie)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Barridos + reconciliación + ahora calidad diaria; sin post-mortem formal |
| Herramientas | 5 | Stack propio completo + LangSmith/Phoenix/Sentry opt-in; sin cambios |
| Equipo | 2 | Sin SRE; un solo par de ojos (sin cambios) |
| Métricas | 5 | Se suma la **calidad de la IA como métrica diaria persistida** (fundamentación + relevancia por tenant) |

**🔍 Justificación.** Ya era la sub-área más fuerte; el roadmap le añadió la pieza que faltaba en la capa de métricas: **la calidad de las respuestas como serie temporal** (`quality_metrics`: tenant/métrica/día/tasa/muestra/umbral, upsert idempotente) con tarjeta semáforo en `/observabilidad` y alerta por correo bajo umbral, más el **destino de alertas de equipo** (`ops_alert_email` como fallback de budget/SLA/calidad — cierra la debilidad v1 "todas las alertas convergen en un buzón personal", en la mitad técnica; la mitad humana — que alguien más las lea — no se resuelve con código). Persisten: sin plantilla de post-mortem (la disciplina de bitácora existe; el proceso formal no) y el hecho estructural de un solo observador.

**🎯 Recomendaciones**
1. Plantilla mínima de post-mortem (heredada de la v1).
2. Encender el signo vital en el perfil de producción (ver Riesgo 1 — hoy nace apagado).

#### 3.3.3 Gestión de Costos — 📊 Puntaje: 3.5/5 (v1: 3.0 — ▲ de "cero optimización" a dos palancas activas)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Selección de modelo barato con banco de aceptación + rutina de revisión mensual documentada en el ADR |
| Herramientas | 4 | Routing multi-modelo por etapa + caché semántica de dudas + medición por modelo — palancas implementadas |
| Equipo | 2 | Sin FinOps (sin cambios, esperable) |
| Métricas | 4 | Costo por modelo/tenant/mes + presupuesto + atribución de modelo POR ETAPA en trazas |

**🔍 Justificación.** La debilidad central de la v1 ("ninguna palanca activa; a volumen real el costo escala linealmente sin amortiguador") se corrigió con las dos palancas exactas que recomendó: (1) **routing por etapa** — `MeteredLLM` es multi-modelo (`overrides={etapa: LLM}` con atribución de modelo por etapa en `llm_usage` y trazas); las etapas simples y frecuentes (`classify` corre en CADA turno; `schedule`) van a `llama-3.1-8b-instant` (~$0.05/$0.08 por 1M vs $0.29/$0.59 del principal, ~6× más barato), **validado** con el banco de aceptación (13/13); (2) **caché semántica de dudas** — `agent/answer_cache.py` cachea por vacante la respuesta a preguntas del candidato; un hit devuelve la respuesta **sin RAG ni LLM (0 tokens)**, seguro entre candidatos (mismo `company_info`). El ADR cierra el círculo con la tabla de palancas activas y la rutina de revisión mensual de `cost_by_model` (10 min con el dashboard existente).

**⚠️ Debilidades**
- La caché de dudas está **apagada por defecto** (`interview_answer_cache_enabled=False`) y sin hit-rate medido con tráfico real — palanca lista, efecto no demostrado.
- El routing barato en el `.env` local está activo, pero los overlays de despliegue no lo fijan — otra pieza del "perfil de producción" ausente (Riesgo 1).

**🎯 Recomendaciones**
1. Incluir `LLM_CHEAP_MODEL` y la caché en el perfil de producción y medir hit-rate + ahorro real con el primer tráfico.
2. La revisión mensual del ADR: agendarla de verdad (calendario), no solo documentarla.

#### 3.3.4 Gobernanza y Seguridad — 📊 Puntaje: 3.5/5 (v1: 3.5 — se mantiene; mejoras y pendientes se compensan)

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Se suma gestión de cambios real (PR + CI + gate de prompts + hook pre-push); auditorías internas con backlog en cero |
| Herramientas | 4 | JWT+rotación, RBAC, RLS, rate limiting, guardrails — sin cambios; **sin secret manager aún** |
| Equipo | 2 | Sin cambios |
| Métricas | 3 | Bitácora de auditoría completa; sin KPIs formales de cumplimiento/incidentes |

**🔍 Justificación.** La base de la v1 (defensa en capas app+DB+telemetría, Ley 29733 implementada, MCP con confirmación en dos pasos) sigue íntegra y no se degradó. Lo nuevo es **gestión de cambios**: todo cambio pasa por rama+PR con CI (2 PRs mergeados hoy estrenando el flujo), hook `pre-push` local bloquea el push directo a `main`, y el gate de prompts impide cambios de comportamiento del LLM sin versión. Los dos pendientes honestos de la v1 siguen: **secretos en `.env`/Secret plano** (el runbook `docs/gestion_secretos.md` y el gate `assert_secure_config` endurecido existen; la migración a gestor requiere cuenta externa — decisión del usuario, declarada pendiente pre-prod) y **protección de rama no server-side** (plan Free en repo privado: branch protection y rulesets devuelven 403; verificado hoy). Se compensan con las mejoras → puntaje estable.

**🎯 Recomendaciones**
1. Secret manager antes del primer despliegue con datos reales (sin cambios desde la v1; ahora es EL pendiente de seguridad).
2. Branch protection server-side al pasar a Pro o publicar el repo.

---

## 4. Matriz de Riesgos Críticos 🚩 (v2)

Los 3 de la v1 quedaron cerrado/mitigados (§2.b). Estos son los top-3 ACTUALES:

| # | Riesgo | Tipo | Detalle y evidencia | Impacto | Probabilidad |
|:-:|---|---|---|:--:|:--:|
| 1 | **Los signos vitales nacen apagados: sin "perfil de producción" que encienda lo construido** | Operacional / Calidad | Las capacidades que cierran el riesgo de calidad y costo son config-gated con default off: `LLM_TRACE_ENABLED=false` (sin trazas no hay juez), `quality_alerts.enabled=false` (el sweep diario no corre), `interview_answer_cache_enabled=false`, y los overlays de k8s no fijan ninguno de estos valores ni `LLM_CHEAP_MODEL`. Un despliegue a producción "tal cual" ejecutaría el sistema SIN medición continua de calidad ni palancas de costo — re-materializando en silencio el Riesgo 3 de la v1 pese a estar resuelto en código. Los defaults conservadores son correctos para dev; lo que falta es el perfil prod explícito y un guard que avise ("producción con calidad apagada"). | Alto | Media |
| 2 | **Dependencia unipersonal + secretos planos (residuo del Riesgo 2 v1)** | Operacional / Seguridad | Un solo operador humano (dimensión Equipo 2.6: sin guardia, sin segundo acceso, post-mortem informal); secretos en `.env`/Secret de k8s plano (runbook de rotación listo, gestor pendiente de decisión externa). El correo de equipo para alertas existe pero nadie más lo lee todavía. Una ausencia del operador o una fuga del `.env` siguen siendo los peores escenarios realistas. | Alto | Media |
| 3 | **Carrera de checkpoint entre réplicas en modo webhook** | Arquitectura | La serialización por conversación es un `threading.Lock` en memoria (`agent/service.py`); el overlay prod despliega `replicas:2` con webhook → dos updates del mismo chat pueden procesarse en pods distintos y pisar el mismo checkpoint Postgres. La v1 lo advirtió como hipotético ("hoy imposible por el polling"); el paso 3 del roadmap activó la condición sin añadir el lock distribuido. Mitigación barata y con patrón en casa: advisory lock de Postgres por `thread_id` (como el del scheduler) o session affinity en el ingress. Además el e2e webhook con Telegram real sigue sin probarse (falta ingress público). | Medio-Alto | Baja-Media |

*Nota financiera: el riesgo de costo bajó respecto a la v1 — hay presupuesto+alerta por tenant Y dos palancas de optimización implementadas; queda condicionado a encenderlas (Riesgo 1).*

---

## 5. Plan de Acción Recomendado (Roadmap v2)

Ordenado por impacto/esfuerzo; los pasos 1 y 3 atacan el Riesgo 1, el 2 el Riesgo 3, el 4 el Riesgo 2.

1. **Perfil de producción "todo encendido" (esfuerzo: horas).** En el overlay `prod`: fijar `LLM_TRACE_ENABLED=true`, `LLM_CHEAP_MODEL`, caché de dudas on, y sembrar por migración/seed los settings por-tenant de prod (`quality_alerts.enabled=true`, `llm_budget`, `sla_alerts`, retención). Añadir un guard tipo `assert_secure_config` que en `ENVIRONMENT=production` **avise** (log warning + alerta ops) si calidad/trazas están apagadas — que producción sin signos vitales sea una decisión visible, nunca un default heredado.
2. **Lock distribuido por conversación (esfuerzo: horas-día).** Advisory lock de Postgres keyed por `thread_id` en `InterviewService.process()` (patrón ya probado en el scheduler), degradando al lock local sin `DATABASE_URL`. Cierra el Riesgo 3 y habilita de verdad `replicas:2`. Completar con el e2e webhook contra un ingress público real (primer despliegue a cluster, aunque sea efímero).
3. **Relevancia de contexto + retrieval creciente (esfuerzo: 1-2 días).** Tercer criterio en el juez compartido (¿el contexto recuperado era pertinente a la duda?) persistido como tercera métrica en `quality_metrics`; regla de crecer `retrieval_set.json` con 2-3 dudas por cada vacante real. Completa el trío RAGAS (fidelidad ✅, relevancia de respuesta ✅, relevancia de contexto ◻).
4. **Operación a prueba de ausencias (esfuerzo: días, decisión del usuario).** Ejecutar la migración a secret manager del runbook; segundo humano con acceso de lectura a `/observabilidad` y al correo de equipo; plantilla de post-mortem de 5 líneas; branch protection server-side al pasar a Pro.
5. **Experimento few-shot + red teaming (esfuerzo: días, oportunista).** Los dos pendientes de calidad heredados de la v1 que no bloquean nada pero afinan: 2 ejemplos few-shot en el prompt de evaluación medidos contra el golden, y una sesión de red teaming asistida por LLM sobre Telegram→prompt→scorecard.

**Trayectoria esperada:** los pasos 1-2 convierten "capacidad construida" en "capacidad operando" — con eso el sistema queda genuinamente en **Nivel 4 técnico** (métricas cuantitativas continuas en producción y despliegue sin corte); el paso 4 ataca el único límite estructural restante (la dimensión Equipo), que es organizacional, no de código.

---

## 6. Anexo — Tabla resumen de puntajes (v1 → v2)

| Fase | Sub-área | Procesos | Herramientas | Equipo | Métricas | **v1** | **v2** |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Ideación | Data Sourcing | 3 | 3 | 2 | 3 | 3.0 | **3.0** |
| Ideación | Selección de modelo | 4 | 4 | 3 | 4 | 2.5 | **3.75** |
| Desarrollo | Prompt Engineering | 5 | 3 | 3 | 4 | 3.5 | **3.75** |
| Desarrollo | Cadenas y Agentes | 4 | 4 | 4 | 4 | 4.0 | **4.0** |
| Desarrollo | RAG vs Fine-Tuning | 4 | 4 | 3 | 3 | 3.0 | **3.5** |
| Desarrollo | Testing | 5 | 4 | 2 | 4 | 3.0 | **3.75** |
| Operación | Despliegue y UX | 4 | 4 | 3 | 3 | 3.0 | **3.5** |
| Operación | Monitoreo y Observabilidad | 4 | 5 | 2 | 5 | 4.0 | **4.0** |
| Operación | Gestión de Costos | 4 | 4 | 2 | 4 | 3.0 | **3.5** |
| Operación | Gobernanza y Seguridad | 4 | 4 | 2 | 3 | 3.5 | **3.5** |
| | **Promedio por dimensión (v2)** | **4.1** | **3.9** | **2.6** | **3.7** | **3.2** | **3.6 / 5** |

**Lectura:** Procesos cruzó a territorio "Gestionado" (4.1) — el efecto directo de que la cadena de
calidad se ejecute sola (CI + nightly + gates) y de que las decisiones estén escritas (ADRs). Métricas
subió a 3.7 con la calidad de la IA como serie diaria y el benchmark de modelos archivado. Herramientas
3.9 con las palancas de costo implementadas. **Equipo no se movió (2.6) y ya es, sin ambigüedad, el
factor limitante** — todo lo que el código podía resolver del Riesgo 2 v1 está resuelto; lo que queda
(segundo operador, guardia, decisión del secret manager) es organizacional. Traducción a la escala
0–100 del framework 1: **81/100** (+9), ponderando al alza — igual que la v1 — las sub-áreas de mayor
riesgo para un sistema que decide sobre personas: gobernanza estable en 3.5 y observabilidad en 4.0,
ambas por encima de la media, y el riesgo de calidad ahora con mecanismo de detección continua.

### Mapeo rápido de evidencia por afirmación clave (nuevas de la v2)

| Afirmación | Evidencia |
|---|---|
| CI vivo en cada push/PR | `gh run list`: 6 runs success 2026-07-03 (~1 min c/u); `.github/workflows/ci.yml` (5 jobs) |
| Gate de versión de prompts | job `prompt-version-gate` en `ci.yml` (diff de `agent/prompts.py` vs base) |
| Nightly golden contra LLM real | `.github/workflows/nightly-quality.yml` (cron 02:00 Lima); run `workflow_dispatch` success (golden 28/28) |
| Entornos dev/prod | `deploy/k8s/overlays/{dev,prod}/` (namespaces, ENVIRONMENT, réplicas, dominios); job `k8s-manifests` valida ambos |
| Webhook dual-mode + réplicas | `api/telegram_bot.py` (`webhook_enabled`/`process_webhook_update`/`secret_matches`), `overlays/prod/backend-scale-patch.yaml` (replicas:2, RollingUpdate), `tests/test_webhook.py` (9) |
| Juez dual continuo | `evaluation/quality.py` (fundamentación + relevancia de respuesta), `api/scheduler.py::_quality_sweep`, migración `0026_quality_metrics.sql`, tarjeta en `/observabilidad` |
| Golden de retrieval hit@k | `tests/golden/retrieval_set.json` (6 casos, min_hit_rate 0.8), `scripts/retrieval_eval.py` |
| Modelo barato validado | `docs/adr-seleccion-modelo.md` (tabla de candidatos), `scripts/golden_eval.py --model`, `.env.example` (`LLM_CHEAP_MODEL=llama-3.1-8b-instant`) |
| Routing multi-modelo + caché | `agent/llm.py::MeteredLLM` (overrides + atribución por etapa), `agent/answer_cache.py`, `tests/test_cost_routing.py` (10) |
| Flujo PR + hook | PRs #1/#2 MERGED con CI verde (`gh pr view`); `.git/hooks/pre-push` (local, probado ambos caminos) |
| Defaults apagados (Riesgo 1) | `src/config.py` (`llm_trace_enabled=False`, `interview_answer_cache_enabled=False`), `api/runtime.py` (`_DEFAULT_QUALITY_ALERTS {enabled: False}`) |
| Lock por-proceso vs replicas:2 (Riesgo 3) | `agent/service.py` (threading.Lock por thread_id) + `overlays/prod/backend-scale-patch.yaml` |
| Branch protection no disponible (Free) | `gh api` branch-protection y rulesets → HTTP 403 "Upgrade to GitHub Pro" (verificado 2026-07-03) |

---

*Informe generado auditando el código fuente, el historial de CI y los PRs del repositorio en su estado
al 2026-07-03 por la tarde (rama `main` = `origin/main`, 330 tests en verde, PRs #1–#2 mergeados). Los
frameworks aplicados están en `audit/auditoria_one.md` y `audit/auditoria_two.md`; la línea base es
`audit/auditoria_final.md` (v1).*
