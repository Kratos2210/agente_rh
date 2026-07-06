# 📋 Auditoría v4 — LLMOps Enterprise end-to-end (`agente_rh`)

**Fecha:** 2026-07-05 · **Framework:** `audit/analisis.md` (5 dimensiones A–E, madurez 1–5,
gap analysis ≥4 riesgos, roadmap a 3 plazos)
**Línea base:** v1 `auditoria_final.md` (72/100) → v2 `auditoria_v2.md` (81/100, 3.6/5) →
v3 `auditoria_v3.md` (rúbrica 13/13 + brecha funcional médico/onboarding, ya implementada)
**Método:** evidencia verificada en el código real de `main` (429 tests colectados hoy), no en la
bitácora. Donde el framework pregunta por algo que no existe, se dice explícitamente.

> **Adaptación del stack.** El framework de `analisis.md` asume Next.js + PostgreSQL/pgvector +
> n8n/Airflow. El mapeo honesto a este proyecto: la **orquestación** no es n8n sino **LangGraph**
> (máquina de estados durable con checkpointer Postgres) + un **scheduler asyncio** propio con
> barridos (`api/scheduler.py`); el **motor vectorial** no es pgvector sino **Chroma local**
> (colección `company_kb`) con embeddings `multilingual-e5-base`; el LLM es Groq (`qwen3-32b` +
> `llama-3.1-8b-instant` como modelo barato ruteado). Las preguntas del framework se evalúan
> contra estos equivalentes; las implicaciones de las desviaciones se anotan donde pesan
> (p. ej. Chroma local = sin HA del índice).

## Resumen ejecutivo

**Madurez global: ≈3.9/5 — "Gestionado y medido" (Nivel 4 de la escala del framework), ~85/100
en la escala histórica (+4 vs v2).** Desde la v2 se cerraron los 3 pasos de ingeniería del
roadmap v2 que seguían abiertos — perfil de producción con los signos vitales ENCENDIDOS en el
overlay prod (`despliegue/k8s/overlays/prod/configmap-patch.yaml`: `LLM_TRACE_ENABLED`,
`INTERVIEW_ANSWER_CACHE_ENABLED`, `LLM_CHEAP_MODEL` + guard `warn_production_profile` con
`tests/test_prod_profile.py`), **lock distribuido por `thread_id`** (advisory lock Postgres en
`agente/service.py:144-152` — el Riesgo 3 de la v2 queda cerrado) y **relevancia de contexto**
como tercer criterio RAGAS en el juez (`evaluation/quality.py:33`). Además la v3 cerró la brecha
funcional (examen médico + onboarding) y apareció despliegue real (Caddy + launchd + overlays
kustomize + CD a GHCR). Lo que separa del nivel 5 ya no es tooling: es **cumplimiento de datos
(PII cruda al proveedor LLM en EE.UU.), operación humana (unipersonal, secretos planos)** y
optimización automática (la evaluación no gatea el deploy).

---

## 1. Matriz de Madurez LLMOps (1–5)

| Dimensión | Nota | Resumen |
|---|:--:|---|
| A · Métricas y calidad RAG | **4.0** | Hybrid search + re-ranker en el camino vivo; juez con los 3 criterios RAGAS persistido a diario; golden 28 + retrieval hit@k + red team en nightly CI. Falta context precision/recall con dataset etiquetado y corpus golden mayor. |
| B · Observabilidad end-to-end | **4.0** | Trazas con contenido + Phoenix in-cluster, p50/p95/p99 en 3 capas (HTTP, etapa LLM, turno e2e), request-id + logs JSON, SLA/budget/quality push. Falta TTFT (no hay streaming), drift real y dashboards de series. |
| C · FinOps | **4.0** | Metering por etapa/modelo/tenant, presupuesto con alerta 80 %, model router validado con banco de aceptación, caché semántica activa en prod, governor de turnos. Falta compresión de contexto y el presupuesto solo alerta (no limita). |
| D · Arquitectura, orquestación e integración | **4.0** | Checkpointer durable, outbox con backoff+dead-letter+retry UI, doble advisory lock (scheduler + por conversación), webhook dual-mode con replicas:2, idempotencia generalizada. Falta fallback de proveedor LLM (Groq único) y el CD es deliberadamente manual. |
| E · Gobierno de datos, seguridad y cumplimiento | **3.5** | Anti-injection multicapa validada por red team, JWT+RBAC+revocación+tenancy con test estructural, ciclo PII completo (Ley 29733). Ancla: PII cruda sale a Groq sin enmascarado/DPA, secretos en `.env` plano, RLS no efectivo sobre el backend, linaje de la KB manual. |

**Promedio: 3.9/5.**

### Dimensión A — Métricas y Calidad RAG: 4.0

**Retrieval (existe y está en el camino vivo, no solo en scripts):**
- Hybrid search **BM25 + vectorial** con sobre-muestreo, dedupe y **cross-encoder re-ranker**
  en `agente/rag.py` (mismo pipeline del chatbot heredado, verificado en vivo: la pregunta de
  salario trae el chunk correcto primero). Degradación en capas: sin BM25/re-ranker sigue
  vectorial; sin colección cae a `company_info` del prompt. `INTERVIEW_RAG_ENABLED=True` por
  defecto (`core/config.py:284`).
- Embeddings `intfloat/multilingual-e5-base` (adecuado para español) + chunking configurado por
  settings; colección `company_kb` sembrada idempotente por hash (`scripts/seed_company_kb.py`).
- **Medición offline**: golden de retrieval hit@k (`tests/golden/retrieval_set.json`, 6 casos,
  umbral 0.8, `scripts/retrieval_eval.py`).

**Generación:**
- **Juez compartido con los 3 criterios RAGAS** (`evaluation/quality.py`): *faithfulness*
  (fundamentación en company_info), *answer relevance* y — nuevo desde la v2 —
  ***context relevance*** (`METRIC_CONTEXT_RELEVANCE`, línea 33; veredicto ilegible = conservador
  en las 3 dimensiones). `_quality_sweep` diario por tenant persiste las tasas en
  `quality_metrics` (migración `0026`) con alerta bajo umbral y semáforo en `/observabilidad`.
- Golden multi-suite de 28 casos (evaluate/classify/slot/prescreen) + **red teaming de 12
  ataques** (`tests/redteam/`) corriendo en `nightly-quality.yml` contra el LLM real; few-shot
  de calibración en `EVALUATE_ANSWER_PROMPT` con `PROMPT_VERSION` + changelog exigidos por CI
  (`prompt-version-gate`).

**Brechas (por qué no 5):** no hay *context precision/recall* formales con dataset etiquetado
(el hit@k de 6 casos es un proxy pequeño); el tamaño/solape de chunks no tiene estudio de
sensibilidad; la evaluación no **gatea** el deploy (nightly informa, no bloquea); sin detección
de drift de embeddings/corpus.

### Dimensión B — Observabilidad y Monitoreo: 4.0

**Trazabilidad de ejecución:** request-id propagado (`X-Request-ID` middleware + contextvar +
logs JSON opt-in, `core/logging_config.py`); trazas LLM **con contenido** por llamada
(`llm_traces`, migración `0024`, capadas y con purga PII en retención/erasure); metadata por
conversación hacia LangSmith (dev) y **Phoenix self-hosted in-cluster en el overlay prod**
(decisión de residencia de datos consciente); `state_transitions` + reconciliación
motor↔negocio (`state_divergence`) + `audit_log` completo.

**Telemetría:** latencias en **tres capas** — HTTP con histogramas O(1) y p95/p99 por ruta
(`observabilidad/httpmetrics.py` + snapshots persistidos a DB cada hora), por **etapa LLM**
(p50/p95/p99 desde `llm_usage`) y **turno end-to-end del candidato** (fila sintética
`stage="turn"` medida antes del lock — la espera cuenta como latencia percibida). Tasas de
error por etapa (`calls/errors`) y logging de toda rama de fallback.

**Alertas push:** SLA (`_sla_sweep`: ops alerts + umbral p95 del turno, dedupe por día),
presupuesto (`_budget_sweep`), calidad diaria (`_quality_sweep`), reconciliación
(dead-letters, reuniones sin link, scheduling estancado, `medical_stuck`, `delivery_failed`) —
todas con correo vía outbox y fallback a `ops_alert_email` de equipo. Sentry config-gated con
`send_default_pii=False`.

**Brechas:** **TTFT no se mide** (no hay streaming hacia el candidato — aplica poco al canal
Telegram, pero el framework lo pide); no hay detección de *concept/data drift* real (la tasa
diaria de calidad es el proxy); los snapshots HTTP guardan acumulados y ningún consumidor
deriva series de tiempo todavía (sin dashboards temporales); logs sin agregación centralizada.

### Dimensión C — FinOps: 4.0

**Existente y verificado:**
- **Metering por etapa** (`llm_usage` con tokens/calls/errors/duration por etapa, modelo y
  `prompt_version`), **costo real por modelo/tenant** (`llm_pricing` por-tenant +
  `compute_cost`; precios demo sembrados) y **presupuesto mensual con alerta al 80 %**
  (`_budget_sweep`, dedupe tenant/mes/umbral). Página de costos por vacante/día/candidato en el
  dashboard (commit `85074eb`).
- **Model Router real** (patrón que el framework pide explícitamente): `MeteredLLM` multi-modelo
  despacha `classify,schedule` → `llama-3.1-8b-instant` (~6–7× más barato) **validado con banco
  de aceptación** (golden 13/13, `scripts/golden_eval.py --model`, ADR
  `docs/adr-seleccion-modelo.md`); atribución de modelo por etapa en trazas y costos.
- **Caché semántica** de dudas del candidato (`agente/answer_cache.py` + `best_match` coseno
  con umbral): hit = **0 tokens**, aislada por vacante, **encendida en el overlay prod**.
- Control de gasto por abuso: governor de turnos del bot (cooldown 2 s + cap diario 120),
  rate limit de sync (2/min/tenant), guard de respuesta vacía sin gastar LLM, tope de dudas
  (3/pregunta) y de reintentos de horario — todo corta ANTES de llamar al LLM.
- Poda de contexto básica: `sanitize_answer_for_prompt` capa la respuesta a 4 000 chars; el RAG
  entrega top-k re-rankeado (no el corpus).

**Brechas:** sin **compresión de contexto** (la `company_info` de la vacante viaja completa en
cada duda; con vacantes de texto largo el costo crece lineal); el presupuesto **alerta pero no
limita** (decisión razonable — cortar entrevistas en curso es peor — pero debe quedar declarada);
sin `max_tokens` explícito por etapa como cinturón; el costo del juez de calidad diario no está
presupuestado por separado.

### Dimensión D — Arquitectura, Orquestación e Integración: 4.0

**Resiliencia:**
- **Outbox durable** con reintentos y **backoff exponencial** (1 min→6 h), dead-letter tras 6
  intentos y **retry desde la UI** — fin del fire-and-forget en correo/Telegram
  (`notifications/outbox.py`).
- **Doble advisory lock Postgres**: el del scheduler (un solo proceso ejecuta barridos, standby
  con takeover) y — cerrando el Riesgo 3 de la v2 — el **lock por `thread_id`**
  (`agente/service.py:144-152`, blake2b→key numérica, pool perezoso, degrada al lock local sin
  `database_url`): dos updates del mismo chat en pods distintos ya no pisan el checkpoint.
- **Registro-primero** en agendamiento (crash a mitad no duplica el evento de Calendar),
  idempotencia generalizada (contacto, decisiones, psych/medical 409, re-sync por `source_ref`,
  onboarding sellado antes de despachar), dedupe de updates de Telegram.
- Webhook **dual-mode** (polling dev / webhook prod con secret en tiempo constante) →
  `replicas:2 + RollingUpdate` en prod; probes startup/readiness/liveness; scheduler con gates
  "a lo sumo cada N" corregidos (sentinel `None` — bug real destapado por CI).
- Fallbacks **por etapa** ante fallo del LLM: scorer marca `low_confidence` →
  `review_required` en el scorecard (humano en el loop), classify/prescreen caen a heurística,
  toda rama loggea.

**Desacoplamiento:** motor conversacional **puro** (LLM, retriever, caché y scheduler
**inyectados**; fakes en tests), capas nítidas `agente/ evaluation/ db/ notifications/
orquestacion/ api/`; integraciones con patrón Protocol+factory (sourcing, scheduling);
`api/main.py` partido en routers/runtime/scheduler. El `_state` global de `api/runtime.py` es
mutable pero de escritura única en lifespan (riesgo bajo, vigilado).

**Brechas (por qué no 5):** **un solo proveedor LLM** — si Groq cae, todas las etapas degradan
a heurística/low_confidence a la vez; no hay modelo alterno de otro proveedor ni circuit
breaker (el framework pide fallback de modelos explícitamente); Chroma local sin HA (el índice
vive en el pod; mitigado porque degrada a company_info); el **CD es entrega continua, no
despliegue continuo** (imágenes GHCR versionadas por sha, apply manual — decisión declarada en
`docs/despliegue.md`).

### Dimensión E — Gobierno de Datos, Seguridad y Cumplimiento: 3.5

**Seguridad de inferencia (fortaleza):** defensa multicapa anti prompt-injection —
sanitización + cap, delimitadores `<<<respuesta>>>…<<<fin>>>` con marco anti-inyección en los
4 prompts que reciben texto del candidato, detector **determinista** de echo-injection que
deriva sin llamar al LLM (`is_echo_injection`), y un **proceso repetible de red teaming**
(12 ataques, 12/12 contenidos, en nightly). La primera corrida encontró y cerró una brecha
real — el proceso funciona.

**Acceso y aislamiento:** JWT con rotación grácil de secreto + RBAC jerárquico + revocación
efectiva (TTL 60 s), aislamiento por tenant con **test estructural en CI** que obliga a todo
endpoint nuevo a pasar por los guards (`tests/test_tenant_guards.py`), RLS por tenant en las
tablas de negocio (latente: el backend usa service_role), MCP con confirmación en dos pasos
firmada HMAC para mutaciones. Gates de arranque en producción (`assert_secure_config` bloquea
secretos débiles; verificado que `ENVIRONMENT=production` del overlay lo activa).

**Privacidad (Ley 29733):** consentimiento sellado al aceptar; **retención** que anonimiza
PII de descartados (nombre/chat/CV/documentos/transcripción/checkpoint LangGraph/trazas
LLM/psych/medical/onboarding — los gaps de v2/v3 cerrados); **erasure** con cascada + scrub del
audit; documentos durables en Postgres con borrado íntegro; trazas y Phoenix **self-hosted**
(la telemetría con PII no sale a un SaaS).

**Brechas (el ancla de la nota):**
1. **La PII del candidato sale cruda al proveedor LLM** (nombre, respuestas, CV en los prompts
   de evaluate/prescreen hacia Groq, EE.UU.) sin enmascarado previo ni DPA formal — el propio
   ADR lo declara pendiente de prod real. El framework pide descubrimiento/anonimización de PII
   *antes* de salir al proveedor: hoy no existe.
2. **Secretos en `.env`/Secret plano** — runbook (`docs/gestion_secretos.md`) y scaffolding de
   External Secrets listos, migración pendiente de acción externa (v2 R2, sigue abierto).
3. **RLS no efectivo sobre el backend** (service_role BYPASSRLS): defensa en profundidad solo
   ante fuga de anon key.
4. **Linaje de la KB manual**: `company_kb` se siembra por script; editar la vacante en el
   dashboard **no** reindexa — el bot puede responder dudas con info desactualizada, y no hay
   control de contradicciones ni frescura (el framework lo pide explícitamente).

---

## 2. Análisis de Brechas Críticas (Gap Analysis & Risks)

| # | Riesgo | Sev. | Prob. | Detalle y mitigación parcial |
|:-:|---|:--:|:--:|---|
| R1 | **Cumplimiento PII → proveedor LLM.** Datos personales de candidatos peruanos (nombre, trayectoria, salario, CV) viajan sin enmascarado a Groq (EE.UU.) en cada evaluación. Ante una fiscalización de la ANPD o un candidato ejerciendo derechos ARCO, no hay DPA ni minimización que mostrar. | ALTA | Media | Mitigación parcial: telemetría self-hosted, retención/erasure robustos, ADR declara el pendiente. Falta: minimización en prompts + DPA o proveedor/modelo con residencia. |
| R2 | **Operación unipersonal + secretos planos.** Bus factor 1: sin segundo operador ni gestor de secretos, una ausencia o una fuga del `.env` compromete todo (token del bot, service key, JWT). | ALTA | Media | Runbook + rotación grácil JWT + scaffolding ESO + gestión de usuarios listos; falta ejecutar (acción externa, v2 R2). |
| R3 | **Proveedor LLM único sin fallback.** Un outage/429 sostenido de Groq degrada simultáneamente entrevistas (low_confidence masivo → todo `review_required`), prescreen (heurística) y dudas. El framework pide modelos alternativos ante caída; no existen. | MEDIA | Media | Mitigación parcial: fallbacks heurísticos por etapa + outbox reintenta notificaciones + el turno no crashea. Falta: segundo proveedor OpenAI-compatible + circuit breaker. |
| R4 | **KB desincronizada de la vacante (linaje).** Editar requisitos/salario en el dashboard no reindexa `company_kb`; el candidato puede recibir condiciones viejas por el canal oficial — riesgo reputacional y legal (oferta contradictoria). | MEDIA | Alta | Mitigación parcial: degrada a `company_info` fresco si no hay colección; seed idempotente manual. Falta: reindex hook en PUT vacante + verificación de frescura. |
| R5 | **Estados como strings libres.** Los `status` se emiten en ≥3 sitios (endpoints, `_sync_business`, sweeps) sin enum central compartido backend/frontend; un typo compila y rompe silenciosamente kanban/barridos (v3 Parte D#4, abierto). | MEDIA | Media | Mitigación parcial: tests cubren los flujos actuales. Falta: módulo único de estados. |
| R6 | **Costo sin techo duro ni compresión de contexto.** El presupuesto solo alerta; `company_info` completa viaja en cada duda. Con 10× volumen o una vacante con descripción de 10 páginas, la factura escala lineal sin freno automático. | BAJA | Media | Mitigación parcial: governor de turnos, caché semántica, router barato, caps de input. Falta: `max_tokens` por etapa + modo degradado opcional al agotar presupuesto. |

---

## 3. Roadmap de Remediación y Evolución Técnica

### Corto plazo (quick wins / seguridad crítica — días)
1. **Reindex de `company_kb` al editar vacante** (R4): hook en `PUT /api/vacancies/{id}` que
   reusa `seed_company_kb` para esa vacante (idempotente por hash, ya existe). El quick win con
   mejor ratio impacto/esfuerzo.
2. **Minimización de PII en prompts** (R1, primer paso barato): el nombre completo del candidato
   no aporta al scoring — sustituir por iniciales/placeholder en evaluate/prescreen; inventario
   de qué campo viaja en qué prompt (los prompts ya están centralizados en `agente/prompts.py`).
3. **Enum central de estados** (R5): módulo único (`core/estados.py` + espejo en
   `frontend/src/lib/stages.ts`) y validación al escribir `status`.
4. **`hired_email`** (v3 Parte D#1): la contratación hoy solo notifica por Telegram; añadir el
   kind al outbox (patrón `medical_exam_email`, ~1 h).
5. **Ejecutar la carga de secretos al gestor** (R2, acción externa tuya): Doppler/ESO con el
   scaffolding ya listo; rotar el token del bot y la service key al migrar.

### Mediano plazo (estabilización e infraestructura — semanas)
1. **Fallback de proveedor LLM + circuit breaker** (R3): segundo endpoint OpenAI-compatible
   (p. ej. AI Gateway/OpenRouter) reusando `build_default_llm(model=)`; validarlo con el banco
   de aceptación existente (`golden_eval.py --model`) antes de habilitarlo como fallback.
2. **Context precision/recall formales** (dimensión A): ampliar el golden de retrieval de 6 a
   20–30 casos etiquetados y añadir el juez de pertinencia de contexto ya existente al nightly
   con umbral propio; estudio simple de sensibilidad de chunking (2–3 configuraciones vs hit@k).
3. **Dashboards de series de tiempo** (dimensión B): consumidor de `http_metrics_snapshots` +
   `quality_metrics` + `llm_usage` que derive deltas (página en `/observabilidad` o Grafana
   sobre el mismo Postgres) — los datos ya se persisten, nadie los grafica en el tiempo.
4. **Techo de gasto opcional** (R6): `max_tokens` por etapa + modo degradado config-gated al
   agotar presupuesto (p. ej. pausar auto-contacto de candidatos nuevos, nunca entrevistas en
   curso).
5. **e2e webhook real** + segundo operador con onboarding (postmortem template y
   `create_user.py` ya existen).

### Largo plazo (optimización continua — meses / según volumen)
1. **Residencia de datos PII** (R1, cierre definitivo): DPA con el proveedor o inferencia con
   residencia controlada (Bedrock/Vertex región, o modelo open-source self-hosted) para las
   etapas que ven PII (evaluate/prescreen), manteniendo el router barato para las que no.
2. **Evaluación que gatea el deploy**: promover el nightly a gate de release (golden + red team
   + umbrales de calidad como required checks del tag de prod) — el paso de "medido" a
   "optimizado/automatizado" del framework.
3. **Fine-tuning/destilación con datos propios**: cuando haya volumen real, los scorecards
   revisados por RR.HH. (`review_required` + decisiones) son un dataset de preferencias natural
   para destilar el evaluador a un modelo más barato — hoy sería prematuro.
4. **Despliegue continuo** (GitHub Environments o ArgoCD) sobre la entrega continua existente;
   RLS efectivo por request (claims por conexión) para que el aislamiento no dependa solo de la
   capa de app; WhatsApp Cloud API y conectores reales de sourcing con contrato de calidad.

---

## Anexo — Trayectoria y estado de riesgos previos

| Auditoría | Score | Nivel | Hito |
|---|:--:|---|---|
| v1 (2026-07-03) | 72/100 · 3.2/5 | Nivel 3 | Diagnóstico + roadmap 5 pasos |
| v2 (2026-07-03) | 81/100 · 3.6/5 | Nivel 3 consolidado | Roadmap v1 ejecutado completo |
| v3 (2026-07-05) | rúbrica 13/13 | — | Brecha funcional (médico/onboarding) implementada |
| **v4 (2026-07-05)** | **≈85/100 · 3.9/5** | **Nivel 4 (Gestionado)** | Roadmap v2 pasos 1–3 y 5 cerrados |

**Riesgos v2 → estado en v4:** R1 (signos vitales apagados) **✅ CERRADO** — guard
`warn_production_profile` + overlay prod con trace/caché/router ON + Phoenix in-cluster.
R2 (unipersonal + secretos) **🟡 ABIERTO** — piezas de código listas, acción externa pendiente
(= R2 de esta v4). R3 (carrera de checkpoint entre réplicas) **✅ CERRADO** — advisory lock
distribuido por `thread_id` en `agente/service.py`.

**Backlog v3 Parte D → estado:** #0 anonimización ✅ · #2 lock por thread ✅ · #3 relevancia de
contexto ✅ · #1 hired_email 🔴 (→ corto plazo 4) · #4 enum de estados 🔴 (→ corto plazo 3) ·
#5 inactividad post-manager 🟡 (reconciliación cubre `medical_stuck`; recordatorios al candidato
no aplican — la espera es de RR.HH.).

**Novedades verificadas hoy no registradas en bitácora:** 429 tests colectados (403 en v3);
`deploy/` renombrado a `despliegue/` (+ `launchd/` plist y `Caddyfile` — hay despliegue real
local con reverse proxy); overlay prod con Phoenix in-cluster; página de costos LLM; higiene de
secretos del working tree correcta (`_cred.txt`, `secrets/`, `uvicorn.log` gitignoreados).
