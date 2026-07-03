# 📊 Informe de Auditoría LLMOps: Agente de Selección de Talento (`agente_rh`)

**Fecha:** 2026-07-03
**Auditor:** Auditoría automatizada con Claude Code (exploración directa del código fuente, no de una descripción)
**Frameworks aplicados:** `audit/auditoria_one.md` (3 fases, madurez 1–4, score 0–100, matriz de riesgos, roadmap) + `audit/auditoria_two.md` (madurez 1–5, 10 sub-áreas × 4 dimensiones: Procesos / Herramientas / Equipo / Métricas)
**Fuentes de evidencia:** código en `agent/`, `evaluation/`, `api/`, `src/`, `integrations/`, `notifications/`, `db/`; migraciones `supabase/migrations/0001–0025`; suite de tests (297 verdes, 40 archivos); `scripts/golden_eval.py` y `scripts/groundedness_judge.py`; `docs/arquitectura.md`, `docs/despliegue.md`, `docs/gestion_secretos.md`, `docs/auditoria_e2e.md`; `deploy/k8s/` y `.github/workflows/ci.yml`.

> **Alcance y método.** Cada afirmación de este informe traza a un archivo o símbolo del repositorio.
> Donde el framework pregunta por algo que no existe, se dice explícitamente "no aplica / no existe"
> en lugar de omitirlo. El contexto del proyecto es relevante para calibrar: es un producto
> pre-lanzamiento de un solo desarrollador (sin remote de git configurado), con vocación SaaS
> multi-empresa, que procesa datos personales de candidatos en Perú (Ley 29733).

---

## 1. Resumen Ejecutivo y Nivel de Madurez

- **Nivel de Madurez Estimado (escala framework 1, 1–4):** **Nivel 3 — Producción Robusta**, con varias sub-áreas ya en frontera de Nivel 4 (observabilidad, gobernanza) y dos anclas que impiden declararlo (CI inoperante por falta de remote; evaluación de calidad no continua).
- **Nivel de Madurez Estimado (escala framework 2, 1–5):** **3.2 — Definido**, con picos "Gestionado" (4) en orquestación y observabilidad, y valles (2–2.5) en selección de modelo y en toda la dimensión Equipo (proyecto unipersonal).
- **Puntuación General:** **72/100**
- **Veredicto Breve:** Ingeniería LLM notablemente por encima de la media para un proyecto de este tamaño: prompts versionados y sellados en datos, guardrails anti-inyección en todas las entradas del candidato, degradación sin-LLM en cada etapa, observabilidad completa (trazas con contenido, costos por tenant, percentiles, SLAs push) y gobernanza real (RBAC, RLS multi-tenant, auditoría, derecho al olvido). Las brechas no son de diseño sino de operación continua: el pipeline de CI existe pero nunca corre (no hay remote), la evaluación de calidad (golden + juez) es manual, y no hay optimización de costos ni redundancia operativa (réplica única, un solo operador humano).

---

## 2. Evaluación por Fases (Hallazgos y Brechas)

Cada sub-área sigue la plantilla del framework 2: puntaje 1–5, desglose por dimensión, justificación con evidencia, fortalezas, debilidades y recomendaciones.

### 🔵 FASE 1: IDEACIÓN

#### 2.1.1 Data Sourcing — 📊 Puntaje: 3/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 3 | Pipeline de sourcing formalizado (conector → pre-filtro → contacto), sin catalogación formal de fuentes |
| Herramientas | 3 | Patrón Protocol+factory extensible; sin herramientas de catálogo de datos (no aplican a esta escala) |
| Equipo | 2 | Sin roles de gobernanza de datos (proyecto unipersonal) |
| Métricas | 3 | Embudo medido (importados/aptos/rechazados/contactados) y gate de CV con puntaje |

**🔍 Justificación.** La entrada de datos es el flujo de postulantes: `integrations/sourcing.py` define un `SimulatedConnector` (patrón Protocol+factory, listo para conectores reales tipo Bumeran) que alimenta `evaluation/prescreen.py` (gate del CV con LLM + fallback heurístico) vía `agent/sourcing_service.py`, con deduplicación por id estable de plataforma (`candidates.source_ref`, migración `0023`). El fixture de demo (`integrations/fixtures/bumeran_applicants.json`) es mayormente sintético (correos `@example.com`), con un par de correos reales usados deliberadamente para la demo en vivo.

El tratamiento de PII es la fortaleza diferencial: consentimiento sellado una sola vez al aceptar (`candidates.consent_at`, migración `0015`), barrido de retención que anonimiza descartados y borra transcripción **y checkpoint LangGraph** (`_retention_sweep`), derecho al olvido con cascada + scrub de auditoría (`DELETE /api/candidates/{id}`), y RLS por tenant en las 16 tablas de negocio (migración `0018`). Todo alineado explícitamente a la Ley 29733 peruana.

**✅ Fortalezas**
- Ciclo de vida de PII completo e implementado (consentimiento → retención → erasure), no solo declarado.
- Conector de sourcing desacoplado y testeable; dedupe idempotente por `source_ref` (bug real encontrado y corregido vía smoke).
- El pre-filtro de CV registra su puntaje y su costo (`llm_usage`, etapa `prescreen`).

**⚠️ Debilidades**
- No hay proceso formal de evaluación de calidad/sesgo de las fuentes (¿qué pasa cuando se conecte el Bumeran real? no hay checklist de calidad de datos de entrada).
- Fixture demo mezcla algunos correos reales — aceptable en demo, mal hábito si migra a staging.

**🎯 Recomendaciones**
1. Antes de conectar una fuente real, definir un contrato de calidad mínima del CV (campos obligatorios, validación de formato) en el conector.
2. Sustituir los correos reales del fixture por sintéticos y mover el redirect de demo a configuración.

#### 2.1.2 Selección del Modelo Base — 📊 Puntaje: 2.5/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 2 | Sin proceso formal de benchmarking entre modelos |
| Herramientas | 2 | Sin plataformas de benchmark; la suite golden sirve como banco de pruebas al cambiar de modelo |
| Equipo | 3 | Los trade-offs se comprenden (arquitectura agnóstica deliberada) |
| Métricas | 3 | Latencia p50/p95/p99, costo y errores **por modelo** en `llm_usage` |

**🔍 Justificación.** El ADR de `docs/arquitectura.md` documenta la decisión como **agnosticismo de proveedor** ("LLM compatible-OpenAI inyectable… cambiar de proveedor es cambiar 3 variables del .env"), lo cual es una decisión arquitectónica sólida — pero no hay un análisis registrado de **por qué Groq/Qwen3-32B específicamente** (latencia vs. costo vs. privacidad vs. alternativas). Mitigantes reales: la suite golden está documentada para correrse "al cambiar de modelo" (`scripts/golden_eval.py`), lo que da un banco de aceptación objetivo para cualquier candidato a reemplazo, y `llm_usage` + `llm_pricing` permiten comparar costo/latencia por modelo con datos propios (fase O-2/O-3).

**✅ Fortalezas**
- Arquitectura que hace barato equivocarse de modelo: LLM inyectable, `MeteredLLM` mide cualquiera, golden valida el cambio.
- Supresión explícita del razonamiento de Qwen3 (`reasoning_effort: "none"`) — decisión consciente de costo/latencia para tareas de clasificación/puntuación.

**⚠️ Debilidades**
- La elección inicial del modelo no está justificada por escrito; si mañana hay que defender ante un cliente por qué sus datos de candidatos pasan por Groq (EE.UU.), no hay documento.
- No se evaluaron sistemáticamente alternativas (ni siquiera 2-3 corridas del golden contra otros modelos registradas).

**🎯 Recomendaciones**
1. Añadir un ADR corto "elección de modelo y proveedor" con la matriz latencia/costo/privacidad, incluyendo la consideración de residencia de datos (PII peruana → proveedor estadounidense).
2. Correr el golden contra 2 modelos alternativos y archivar los resultados como línea base comparativa.

### 🟢 FASE 2: DESARROLLO

#### 2.2.1 Prompt Engineering — 📊 Puntaje: 3.5/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Versionado formal (`PROMPT_VERSION`), regla documentada: cambiar prompt ⇒ subir versión + correr golden |
| Herramientas | 3 | Sistema propio (prompts como código + trazas + LangSmith opcional); sin plataforma dedicada — adecuado a la escala |
| Equipo | 3 | Responsabilidad única pero con criterio demostrado (anti-inyección en el 100 % de entradas del candidato) |
| Métricas | 4 | Golden con rangos esperados por caso, incluidos adversariales; efectividad medible por versión |

**🔍 Justificación.** `agent/prompts.py` es la fuente única versionada en git; `PROMPT_VERSION = "2026-07-02.1"` se sella en `scorecards`, `llm_usage` y `llm_traces` (migraciones `0021`/`0024`), con test que lo fija (`tests/test_pipeline_llm.py::test_scorecard_carries_prompt_version`). El patrón estructural es consistente en todos los prompts: **rol acotado → dato del candidato entre delimitadores `<<<respuesta>>>…<<<fin>>>` con instrucción anti-inyección explícita → formato de salida JSON exacto → pautas de decisión**. La sanitización previa (`evaluation/scorer.py::sanitize_answer_for_prompt`) quita los delimitadores del texto del candidato (anti-breakout) y capa a 4 000 caracteres.

Técnicas avanzadas: **no se usa Few-Shot** (todos los prompts son zero-shot: instrucción + esquema) ni **Chain-of-Thought** — este último está *activamente suprimido* (`reasoning_effort: "none"`, salida JSON directa). Es una decisión defendible (costo/latencia/parseabilidad en tareas de clasificación), pero el framework la pregunta y la respuesta honesta es "no, por diseño". Los campos `justification`/`ack` del JSON son racionalización post-hoc para el reclutador, no razonamiento intermedio.

**✅ Fortalezas**
- Gestión de prompts como código madura: versión sellada en los datos ⇒ cualquier scorecard es auditable ("¿con qué prompt se generó este puntaje?").
- Anti-inyección sistemática (delimitadores + sanitización + caso golden `inyeccion-score` que verifica que un intento de manipular el puntaje termina en score 0).
- Grounding explícito en el prompt de dudas ("usá SOLO esa información… no inventes"; prohibición de confirmar salario/condiciones fuera de `company_info`).

**⚠️ Debilidades**
- Zero-shot puro: en los casos borde del golden (respuestas ambiguas), 1-2 ejemplos few-shot en `EVALUATE_ANSWER_PROMPT` probablemente estrecharían la varianza de puntajes entre corridas/modelos.
- El ciclo de experimentación es informal: no queda registro de variantes de prompt probadas y descartadas (solo la versión ganadora).

**🎯 Recomendaciones**
1. Experimento controlado: añadir 2 ejemplos few-shot (uno 🟢, uno 🔴) al prompt de evaluación y comparar contra el golden antes/después.
2. Registrar en un changelog corto por qué cambia cada `PROMPT_VERSION` (hoy solo se ve el diff).

#### 2.2.2 Cadenas y Agentes (Orquestación) — 📊 Puntaje: 4/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Decisión de diseño documentada (ADR-lite + guía §9) y deliberada |
| Herramientas | 4 | LangGraph con checkpointer Postgres durable; LangChain minimalista a propósito |
| Equipo | 4 | Arquitectura demuestra dominio del trade-off grafo-vs-código |
| Métricas | 4 | Latencia por etapa y end-to-end del turno; errores por etapa; topes de iteración |

**🔍 Justificación.** El "cerebro" es un `StateGraph` de LangGraph con **un solo nodo** (`agent/graph.py`: nodo `turn` → `END`); el branching real (saludo/entrevista/dudas/documentos/agendamiento) es dispatch por fase en Python puro (`agent/nodes.py::handle_turn`). LangGraph se usa para lo que es insustituible — **estado durable con checkpointer Postgres** (`PostgresSaver`, thread = `canal:chat`) — y el control de flujo se mantiene en código testeable. LangChain se usa solo como cliente LLM (`LangChainLLM` envuelve `ChatOpenAI.invoke()`; sin LCEL ni parsers — JSON por regex propio con fallback). Es la elección correcta para una entrevista secuencial: un grafo multi-nodo aquí sería ceremonia sin beneficio.

**Riesgo de bucles infinitos: mitigado explícitamente.** `MAX_CANDIDATE_QUESTIONS = 3` (el ciclo de dudas era infinito y reseteaba el reloj de inactividad — hallazgo I1 de la auditoría e2e, corregido) y `MAX_SLOT_RETRIES = 3` con escalamiento único a RR.HH. (I2); además `TurnGovernor` (cooldown 2 s + tope 120 turnos/día por chat) corta ráfagas **antes** de gastar LLM, y el barrido de inactividad cierra conversaciones abandonadas. Cobertura en `tests/test_iteration_limits.py`.

**✅ Fortalezas**
- Estado durable real: un crash del proceso no pierde la entrevista (checkpoint por turno).
- Motor puro e inyectable (LLM fake en tests, retriever inyectado) — 297 tests corren sin red.
- Todos los caminos de bucle tienen tope + escalamiento humano.

**⚠️ Debilidades**
- La concurrencia sobre el mismo thread se serializa con un `threading.Lock` en el servicio — correcto en un proceso, insuficiente si el bot pasara a múltiples réplicas (hoy imposible por el polling, ver 2.3.1).

**🎯 Recomendaciones**
1. Mantener el diseño; documentar el lock por-thread como restricción conocida al migrar a webhook multi-réplica.

#### 2.2.3 RAG vs. Fine-Tuning — 📊 Puntaje: 3/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Decisión justificada y correcta: RAG para datos dinámicos de vacantes; FT innecesario |
| Herramientas | 4 | Pipeline completo: Chroma + BM25 híbrido + cross-encoder re-ranker, con degradación en capas |
| Equipo | 3 | Dominio del pipeline demostrado (re-ranker puesto en el camino vivo tras detectar orden vectorial subóptimo) |
| Métricas | 2 | **Sin métricas de recuperación**: ni precisión del retrieval ni relevancia del contexto se miden |

**🔍 Justificación.** La elección RAG-sin-fine-tuning es correcta y está razonada: el conocimiento es **por vacante y cambia** (rango salarial, beneficios, modalidad) — exactamente el caso de RAG; no hay necesidad de comportamiento/formato especial que justifique FT, y el formato se controla por prompt + JSON. Implementación: `agent/rag.py` construye un retriever custom (híbrido BM25 + vectorial sobre la colección `company_kb`, sembrada idempotentemente por `scripts/seed_company_kb.py`, re-rankeado con cross-encoder) inyectado al motor igual que el LLM; degrada en capas (sin re-ranker → vectorial; sin colección → `company_info` de la vacante) y activado por defecto.

La brecha está en la medición: el juez de groundedness (ver 2.2.4) evalúa **fidelidad** de la respuesta final, pero **nadie mide la calidad del retrieval en sí** — ni "¿el chunk recuperado era el correcto?" (context relevance) ni "¿la respuesta atiende la pregunta?" (answer relevance) existen como métrica formal. El único dato de esa capa es anecdótico (verificación en vivo: la pregunta de salario ahora trae el chunk correcto primero).

**✅ Fortalezas**
- Justificación RAG/FT limpia; pipeline de retrieval de calidad superior al típico "top-k vectorial y ya".
- Fail-safe en cada capa: el candidato nunca se queda sin respuesta por fallo del RAG.

**⚠️ Debilidades**
- Sin métricas de precisión de recuperación ni relevancia (contexto/respuesta) — es la sub-área con mayor distancia entre calidad de implementación y calidad de medición.

**🎯 Recomendaciones**
1. Añadir un mini-set golden de retrieval: N preguntas → chunk esperado, medible offline sin LLM.
2. Evaluar RAGAS (o juez propio ampliado) para answer/context relevance sobre las trazas O-1 ya existentes.

#### 2.2.4 Testing — 📊 Puntaje: 3/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Estrategia multi-capa real: unit/integración (297), regresión de prompts (golden), adversarial (inyección), e2e por script |
| Herramientas | 3 | Arnés propio (golden + juez); sin RAGAS/DeepEval; sin herramientas de red teaming |
| Equipo | 2 | Sin QA dedicado ni evaluadores humanos formales más allá del propio desarrollador |
| Métricas | 3 | Rangos por caso, tasa de fundamentación con umbral 0.9; **no continuo** |

**🔍 Justificación.** El testing determinista es excelente: 297 tests con LLM fake cubren motor, servicio, API (incluido un test estructural que obliga a todo endpoint nuevo a pasar por auth + guard de tenant — `tests/test_tenant_guards.py`), y hay verificación end-to-end multi-etapa por script contra DB y LLM reales (`scripts/verify_multistage.py`).

El testing del LLM real responde a las preguntas del framework así:
- **LLM-as-a-Judge:** APLICA PARCIALMENTE — `scripts/groundedness_judge.py` muestrea trazas reales (`llm_traces`, etapa `answer`) y un LLM juez decide si cada respuesta se fundamenta SOLO en la información del prompt, con umbral (`--min-rate 0.9`, exit 1 si baja). Verificado en vivo: cazó una alucinación de sueldo sembrada.
- **Fidelidad (faithfulness):** APLICA — es exactamente el criterio del juez.
- **Relevancia de respuesta / de contexto:** NO APLICA como métrica formal (ver 2.2.3).
- **Regresión de prompts:** APLICA — golden de 28 casos en 4 suites (evaluate/classify/slot/prescreen), con adversariales; primera corrida real 28/28 en rango.
- **Red teaming:** NO como proceso — solo los casos de inyección del golden y `tests/test_integrity.py`; "red team" aparece únicamente en los cuestionarios de auditoría, no como práctica.

El talón de Aquiles: golden y juez son **manuales/nightly-por-documentación**, y como el repo **no tiene remote**, el CI de GitHub Actions (que al menos correría el pytest determinista) **nunca se ha ejecutado**. Hoy el único gate real es la disciplina del desarrollador.

**✅ Fortalezas**
- Golden con contraejemplos adversariales y respuestas reales del dominio — raro de ver a esta escala.
- Juez de fundamentación operativo contra datos reales, con umbral y salida apta para cron.

**⚠️ Debilidades**
- Nada de esto corre automáticamente: sin remote no hay CI; golden/juez dependen de invocación manual.
- Sin red teaming programado; sin evaluación humana estructurada (más allá del HITL de producción, ver 2.3.4).

**🎯 Recomendaciones**
1. **Prioridad #1 de toda la auditoría:** configurar remote (GitHub privado basta) → el CI existente corre gratis desde el primer push.
2. Añadir job nightly (cron de Actions o launchd local) para golden + juez con presupuesto de tokens acotado.

### 🔴 FASE 3: OPERACIÓN

#### 2.3.1 Despliegue y UX — 📊 Puntaje: 3/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 3 | CI/CD definido pero inerte (sin remote); sin A/B ni canary; gate prod/dev de secretos sí existe |
| Herramientas | 4 | Docker multi-camino + K8s validado (kubeconform 8/8) + `deploy.sh`; probes de salud completos |
| Equipo | 3 | Dominio de contenedores/K8s demostrado; sin experiencia operativa multi-entorno registrada |
| Métricas | 2 | Sin métricas de despliegue (tiempo, tasa de éxito); las HTTP metrics son de runtime, no de release |

**🔍 Justificación.** Hay tres caminos de despliegue documentados y verificados (`docs/despliegue.md`): Docker Compose (con espera de health), Kubernetes (manifiestos validados con kubeconform, probes startup/readiness/liveness, decisión honesta `replicas: 1` + `Recreate` porque el bot Telegram en **polling** solo admite un consumidor por token) y una decisión serverless argumentada (no para bot/scheduler/RAG; sí para API tras migrar a webhook). `api/auth.py::assert_secure_config` bloquea el arranque en producción con secretos débiles — un gate real dev/prod.

Lo que el framework pregunta y no existe: **A/B testing y canary** (imposibles con réplica única), **entornos separados** (un solo `.env`, un solo directorio k8s, sin overlays dev/staging/prod), **streaming SSE** (todas las llamadas LLM son `.invoke()` bloqueante; el streaming del legado `src/qa_chain.py` no es alcanzable) y **caché semántica** (`src/semantic_cache.py` existe pero es código muerto: el flag de config no tiene consumidor). Matiz de UX: en Telegram el streaming token-a-token aporta poco (los mensajes llegan enteros); la latencia percibida ya se mide end-to-end (fila `turn` de O-3), que es la métrica correcta para este canal.

**✅ Fortalezas**
- Contenedorización real y validada; restricción del polling codificada en los manifiestos en vez de descubierta en producción.
- Gate de configuración insegura en arranque de producción.

**⚠️ Debilidades**
- CI/CD nunca ejecutado (sin remote); despliegue efectivo = manual.
- Sin entornos separados ni estrategia de rollout progresivo.
- Caché semántica muerta: costo de oportunidad directo en la etapa `answer` (preguntas de candidatos son repetitivas por naturaleza).

**🎯 Recomendaciones**
1. Migrar el bot a **webhook** — desbloquea réplicas>1, canary y el camino serverless ya documentado.
2. Crear overlays kustomize `dev`/`prod` (diferencias: réplicas, recursos, secrets).
3. Cablear la caché semántica en la etapa `answer` (la infraestructura ya está escrita) y medir el hit-rate.

#### 2.3.2 Monitoreo y Observabilidad — 📊 Puntaje: 4/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Barridos de reconciliación, alertas dedupeadas, divergencia motor↔negocio detectada |
| Herramientas | 5 | Stack propio completo + LangSmith/Phoenix/Sentry config-gated — sin SaaS obligatorio (decisión PII) |
| Equipo | 2 | Sin SRE; un solo par de ojos para todas las alertas |
| Métricas | 5 | Trazas con contenido, tokens/costo/errores por etapa y modelo, p50/p95/p99, latencia e2e del turno |

**🔍 Justificación.** Es la sub-área más fuerte. El plan O-1…O-6 está completo y verificado en vivo: **trazas con contenido** por llamada (prompt/respuesta capados, `llm_traces`, con purga por retención/erasure — la PII también se gobierna en la telemetría), **costos** por modelo y tenant con presupuesto mensual y alerta al 80 %, **percentiles** por etapa LLM y por ruta HTTP más la fila sintética `turn` (latencia que el candidato realmente percibe, medida desde antes del lock), **SLAs push** por correo con dedupe diario, **logs JSON con request-id propagado**, Sentry sin PII (`send_default_pii=False`) y Arize Phoenix self-hosted opcional (spans OpenInference verificados). El dashboard `/observabilidad` expone outbox, alertas operativas, métricas HTTP y bitácora de auditoría. La reconciliación detecta hasta divergencia entre el checkpoint del motor y el estado de negocio (`state_divergence`) — un chequeo de consistencia que la mayoría de sistemas de este tamaño no tiene.

**✅ Fortalezas**
- Trazabilidad completa de cada ejecución con versión de prompt, costo y latencia — responde "¿qué pasó, cuánto costó y con qué configuración?" para cualquier scorecard.
- Decisión de privacidad coherente: la fuente de verdad de telemetría es infraestructura propia; los SaaS son opt-in.

**⚠️ Debilidades**
- Todas las alertas convergen en una sola persona sin escalamiento ni guardia; un incidente durante una ausencia no lo ve nadie.
- Sin análisis post-mortem formalizado (los hallazgos se documentan bien en `docs/auditoria_e2e.md`, pero no hay plantilla/proceso de incidente).

**🎯 Recomendaciones**
1. Definir el destino de alertas para producción real (correo de equipo/canal), no el buzón personal.
2. Plantilla mínima de post-mortem (qué pasó, detección, corrección, prevención) reutilizando la disciplina ya visible en la bitácora.

#### 2.3.3 Gestión de Costos — 📊 Puntaje: 3/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 3 | Presupuesto mensual por tenant con umbral de alerta; sin revisiones periódicas formales |
| Herramientas | 3 | Medición excelente; **cero optimización** (sin caché, sin batching, sin routing por complejidad) |
| Equipo | 2 | Sin función FinOps (esperable a esta escala) |
| Métricas | 4 | Costo por modelo/tenant/mes, costo estimado en dashboard, presupuesto vs. gasto del mes |

**🔍 Justificación.** El lado de **medición** está resuelto: `MeteredLLM` registra tokens por etapa, `llm_pricing` por tenant convierte a USD por modelo (`compute_cost`), el dashboard muestra costo estimado y desglose, y `_budget_sweep` alerta una vez por tenant/mes/umbral con correo. El costo por candidato es derivable de los datos existentes (usage por conversación) aunque no está expuesto como métrica directa.

El lado de **optimización** no existe: no hay prompt caching, ni batching, ni enrutamiento de modelo por complejidad de tarea (todas las etapas usan el mismo modelo, cuando `classify` — un binario respuesta/duda — podría resolverse con un modelo mucho más barato o con la heurística que ya existe como fallback), y la caché semántica heredada está sin cablear. Mitigante estructural: los guardrails (TurnGovernor, topes de iteración, cortes sin-LLM como la revalidación determinista y el guard de respuesta vacía) ya evitan gasto inútil por diseño.

**✅ Fortalezas**
- Visibilidad de costo por defecto (precios del modelo demo sembrados) — el operador ve dólares, no solo tokens.
- El presupuesto es por tenant: compatible con el modelo SaaS desde ya.

**⚠️ Debilidades**
- Ninguna palanca activa de reducción de costo; a volumen real (cientos de entrevistas/mes) el costo escala linealmente sin amortiguador.

**🎯 Recomendaciones**
1. Enrutar `classify` (y quizá `slot`) a un modelo pequeño/barato — medible de inmediato con el `by_model` ya existente.
2. Cablear la caché semántica en `answer` con las preguntas frecuentes de candidatos.
3. Revisión mensual de `cost_by_model` (10 minutos con el dashboard actual) como rutina.

#### 2.3.4 Gobernanza y Seguridad — 📊 Puntaje: 3.5/5

| Dimensión | Puntaje | Nota |
|---|:---:|---|
| Procesos | 4 | Auditorías internas iterativas con backlog cerrado; runbook de rotación de secretos; cumplimiento Ley 29733 |
| Herramientas | 4 | JWT con rotación grácil + bcrypt + RBAC + RLS + rate limiting + guardrails de entrada; sin secret manager aún |
| Equipo | 2 | Sin equipo de seguridad/cumplimiento; auditorías hechas por el propio desarrollador (con herramientas LLM) |
| Métricas | 3 | Bitácora de auditoría completa y visible; sin KPIs formales de cumplimiento/incidentes |

**🔍 Justificación.** **Control de accesos:** JWT (firma con rotación grácil vía `jwt_secret_previous`), bcrypt, RBAC jerárquico de 3 roles aplicado en los 45 endpoints, aislamiento multi-tenant en capa de aplicación **más** RLS en base de datos como defensa en profundidad (16 tablas, claim de tenant del JWT), y un test estructural que hace fallar el CI si un endpoint futuro olvida los guards. El servidor MCP hereda el mismo perímetro (JWT + tenancy + RBAC + auditoría), viene apagado por defecto, y sus mutaciones exigen confirmación en dos pasos con token HMAC de dominio separado — "capability ≠ autoridad" implementado, no declarado.

**Guardrails de entrada/salida contra prompt injection:** sanitización + delimitadores en el 100 % de las entradas del candidato, guard de respuesta vacía, límites de tasa e iteración (detallados en 2.2.1/2.2.2), validación de documentos (solo PDF, 20 MB, anti path-traversal). **Fugas de datos:** RLS, enmascaramiento por rol (credenciales de examen psicológico ocultas a viewer), Sentry sin PII, purga de PII residual en outbox/audit al ejercer el erasure. **Cumplimiento:** consentimiento, retención configurable y derecho al olvido (Ley 29733), todo auditado en `audit_log` con actor/acción/entidad.

Pendientes honestos: secretos en `.env` plano (el runbook `docs/gestion_secretos.md` existe y el gate de producción bloquea defaults, pero la migración a un gestor está declarada como pre-prod pendiente); sin red teaming continuo; sin política formal de gestión de riesgos más allá de las auditorías internas.

**✅ Fortalezas**
- Defensa en capas coherente (app + DB + telemetría) con la privacidad del candidato como criterio de diseño transversal.
- Cultura de auditoría interna con cierre verificado: dos auditorías completas (integraciones externas F1–F5; e2e de 10 dimensiones) con backlog llevado a cero y documentado.

**⚠️ Debilidades**
- Secretos planos en disco hasta que se adopte un gestor.
- El RLS es latente para el backend (usa service_role); protege ante fuga de anon key, no ante un bug del propio backend.

**🎯 Recomendaciones**
1. Ejecutar la migración a secret manager ya planificada en el runbook antes de cualquier despliegue con datos reales.
2. Sesión de red teaming (aunque sea asistida por LLM, como las auditorías previas) enfocada en el flujo Telegram → prompt → scorecard.

---

## 3. Matriz de Riesgos Críticos 🚩

| # | Riesgo | Tipo | Detalle y evidencia | Impacto | Probabilidad |
|:-:|---|---|---|:--:|:--:|
| 1 | **Cadena de calidad no ejecutada: CI inerte + evaluación LLM manual** | Arquitectura / Calidad | El repo no tiene remote ⇒ `.github/workflows/ci.yml` (pytest, lint, tsc, docker, kubeconform) **nunca ha corrido**; golden (28 casos) y juez de fundamentación son manuales. Toda la red de seguridad — que existe y es buena — depende de que una persona se acuerde de ejecutarla. Una regresión de prompt o de dependencia puede llegar a producción sin ninguna barrera automática. | Alto | Alta |
| 2 | **Punto único de fallo operativo y humano** | Operacional | `replicas: 1` + `Recreate` (restricción real del polling de Telegram), un solo desarrollador/operador, alertas a un buzón personal y secretos en `.env` plano. Cualquier indisponibilidad (del proceso o de la persona) detiene entrevistas en curso y nadie más puede rotar un secreto comprometido siguiendo el runbook. | Alto | Media |
| 3 | **Calidad del LLM sin medición continua en producción** | Calidad / Reputacional | La fidelidad se audita offline (juez con umbral 0.9) y la relevancia de respuesta/contexto no se mide en absoluto. Una degradación silenciosa (cambio del proveedor, drift del modelo servido por Groq) afectaría puntajes de candidatos reales — decisiones sobre personas — hasta que alguien corra el juez manualmente. `review_required` mitiga solo los fallos *detectables* (excepciones), no las respuestas plausibles-pero-peores. | Alto | Media |

*Riesgo financiero evaluado y no incluido en el top 3: el gasto LLM está bien contenido por presupuesto/alertas por tenant y guardrails de consumo; el riesgo de costo es real solo a volumen alto y sin las optimizaciones de la sección 2.3.3.*

---

## 4. Plan de Acción Recomendado (Roadmap)

Ordenado por relación impacto/esfuerzo; los pasos 1-2 atacan el Riesgo 1, el 3 el Riesgo 2, el 4 el Riesgo 3.

1. **Activar la cadena de CI (esfuerzo: horas).** Crear remote (GitHub privado), push, y verificar que los 4 jobs existentes pasan. Añadir un job **nightly** que corra `scripts/golden_eval.py` (las 4 suites) y `scripts/groundedness_judge.py --min-rate 0.9` con presupuesto de tokens acotado, fallando en rojo visible. Gate adicional: si cambia `agent/prompts.py` sin subir `PROMPT_VERSION`, fallar el build (chequeo de 10 líneas).
2. **Secretos y entornos (esfuerzo: 1-2 días).** Ejecutar la migración a secret manager ya diseñada en `docs/gestion_secretos.md` (Doppler/Vault/Supabase Vault) y crear overlays kustomize `dev`/`prod`. Con esto, `assert_secure_config` deja de ser la única línea de defensa de configuración.
3. **Webhook de Telegram (esfuerzo: días).** Sustituir polling por webhook detrás del ingress ya definido. Desbloquea `replicas > 1` (el scheduler ya es multi-réplica por advisory lock), habilita canary/rolling y el camino serverless documentado en `docs/despliegue.md`. Redirigir alertas SLA/ops a un destino de equipo.
4. **Medición continua de calidad (esfuerzo: días).** Sobre las trazas O-1 existentes: programar el juez de fundamentación como barrido del scheduler (patrón `_sla_sweep`, muestreo diario, alerta si la tasa baja del umbral) y añadir métricas de relevancia de respuesta/contexto (RAGAS o juez propio ampliado) + un golden de retrieval (pregunta → chunk esperado). Esto convierte la evaluación de "foto offline" en "signo vital".
5. **Optimización de costos (esfuerzo: días, cuando haya volumen).** Enrutar `classify`/`slot` a un modelo barato (la arquitectura inyectable lo permite sin tocar el motor) y cablear la caché semántica dormida en la etapa `answer`; medir el efecto con el desglose `by_model` y el hit-rate. Documentar de paso el ADR de selección de modelo (matriz latencia/costo/privacidad).

**Trayectoria esperada:** los pasos 1-2 consolidan el Nivel 3 real (todo lo construido pasa a ejecutarse solo); los pasos 3-4 son la puerta al Nivel 4 (Gestionado: métricas cuantitativas continuas y despliegue progresivo); el paso 5 es oportunista según volumen.

---

## 5. Anexo — Tabla resumen de puntajes

| Fase | Sub-área | Procesos | Herramientas | Equipo | Métricas | **Global** |
|---|---|:--:|:--:|:--:|:--:|:--:|
| Ideación | Data Sourcing | 3 | 3 | 2 | 3 | **3.0** |
| Ideación | Selección de modelo | 2 | 2 | 3 | 3 | **2.5** |
| Desarrollo | Prompt Engineering | 4 | 3 | 3 | 4 | **3.5** |
| Desarrollo | Cadenas y Agentes | 4 | 4 | 4 | 4 | **4.0** |
| Desarrollo | RAG vs Fine-Tuning | 4 | 4 | 3 | 2 | **3.0** |
| Desarrollo | Testing | 4 | 3 | 2 | 3 | **3.0** |
| Operación | Despliegue y UX | 3 | 4 | 3 | 2 | **3.0** |
| Operación | Monitoreo y Observabilidad | 4 | 5 | 2 | 5 | **4.0** |
| Operación | Gestión de Costos | 3 | 3 | 2 | 4 | **3.0** |
| Operación | Gobernanza y Seguridad | 4 | 4 | 2 | 3 | **3.5** |
| | **Promedio por dimensión** | **3.5** | **3.5** | **2.6** | **3.3** | **3.2 / 5** |

**Lectura:** las dimensiones técnicas (Procesos 3.5, Herramientas 3.5, Métricas 3.3) sostienen un
nivel "Definido sólido con picos Gestionado"; la dimensión Equipo (2.6) refleja honestamente la
realidad unipersonal del proyecto y es, junto con la activación del CI, el factor limitante para
declarar Nivel 4. Traducción a la escala 0–100 del framework 1: **72/100**, ponderando al alza las
sub-áreas de mayor riesgo para un sistema que decide sobre personas (gobernanza y observabilidad,
ambas por encima de la media).

### Mapeo rápido de evidencia por afirmación clave

| Afirmación | Evidencia |
|---|---|
| Prompts versionados y sellados | `agent/prompts.py` (`PROMPT_VERSION`), migraciones `0021`/`0024`, `tests/test_pipeline_llm.py` |
| Anti-inyección en todas las entradas | `evaluation/scorer.py::sanitize_answer_for_prompt`, delimitadores en `agent/prompts.py`, caso golden `inyeccion-score` |
| Grafo de un nodo + checkpointer durable | `agent/graph.py` (`StateGraph`, `PostgresSaver`), `agent/nodes.py::handle_turn` |
| Topes de bucle | `agent/nodes.py` (`MAX_CANDIDATE_QUESTIONS`, `MAX_SLOT_RETRIES`), `api/ratelimit.py::TurnGovernor`, `tests/test_iteration_limits.py` |
| RAG híbrido con re-ranker, FT descartado con razón | `agent/rag.py`, `scripts/seed_company_kb.py`, `docs/arquitectura.md` |
| Juez LLM de fundamentación (manual) | `scripts/groundedness_judge.py` (`--min-rate 0.9`) |
| Golden 28 casos, 4 suites | `tests/golden/golden_set.json`, `scripts/golden_eval.py` |
| CI definido pero inerte | `.github/workflows/ci.yml` (4 jobs); `git remote -v` vacío |
| Observabilidad O-1…O-6 | `agent/llm.py::MeteredLLM`, `api/httpmetrics.py`, `api/scheduler.py` (barridos budget/SLA/reconciliación), migraciones `0024`/`0025` |
| Costos por tenant + presupuesto | `api/deps.py::compute_cost`, `_budget_sweep`, endpoints `llm-pricing`/`llm-budget` |
| RBAC + RLS + auditoría + Ley 29733 | `api/auth.py`, migraciones `0013`/`0015`/`0018`, `tests/test_tenant_guards.py`, `docs/gestion_secretos.md` |
| HITL (revisión humana y decisiones) | `evaluation/scorecard.py::review_required`, `POST /api/candidates/{id}/decision`, tabla `stage_feedback` |
| Caché semántica muerta / sin streaming vivo | `src/semantic_cache.py` (sin consumidores), `agent/llm.py` (`.invoke()` bloqueante) |
| Sin A/B / canary; réplica única deliberada | `deploy/k8s/backend-deployment.yaml` (`Recreate`, `replicas: 1`), `docs/despliegue.md` |

---

*Informe generado auditando el código fuente del repositorio en su estado al 2026-07-03 (rama `main`, 297 tests en verde). Los frameworks de evaluación aplicados están en `audit/auditoria_one.md` y `audit/auditoria_two.md`.*
