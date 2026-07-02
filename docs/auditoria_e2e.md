# Auditoría end-to-end — `agente_rh`

**Fecha:** 2026-07-01 · **Alcance:** todo el sistema (backend FastAPI + motor LangGraph +
evaluación LLM + Supabase + bot Telegram + dashboard Next.js), evaluado en 10 dimensiones:
seguridad, arquitectura, base de datos, UX, observabilidad, rate limiting, diseño de pipeline
LLM, estado conversacional/memoria, agente-como-grafo/consistencia transaccional y
razonamiento iterativo/control de bucles.

> Complementa `auditoria_integraciones_externas.md` (F1–F5, todos cerrados). Esta auditoría
> cubre el resto del sistema. Los hallazgos M1, G1, G2, I1 e I2 se **implementaron el mismo
> día** (ver bitácora); el resto se cerró por lotes el 2026-07-01/02. **Backlog: vacío**
> (quedan solo los "pendientes menores" anotados dentro de algunos hallazgos, p. ej. cookie
> httpOnly en S2 o Sentry en O3 — mejoras de producción, no hallazgos abiertos).

## Resumen ejecutivo

El proyecto está muy por encima del estándar de un MVP: outbox durable con dead-letter,
advisory lock, RLS latente, tenant guards blindados por CI, anti-inyección en la evaluación,
degradación visible, 170 tests (222 al cierre). Las brechas más importantes detectadas fueron
**la ausencia total de rate limiting** (✅ corregida), la **sanitización de prompts incompleta**
(✅ corregida) y **el routing del bot que ignora el multi-tenancy** (✅ corregida — deep-links
por vacante, junto con el N+1 de los listados). A 2026-07-02 los hallazgos de las 10
dimensiones están **todos cerrados**.

### Top 5 priorizado

1. ✅ **Rate limiting** (R1+R2+R3): login 5/min por IP + cooldown/tope diario por chat en el bot
   + guard de reenvío del examen psicológico. *(Implementado 2026-07-01.)*
2. ✅ **Sanitizar los 3 prompts restantes** (S1): `sanitize_answer_for_prompt` + delimitadores.
   *(Implementado 2026-07-01.)*
3. ✅ **Routing multi-tenant del bot** (A1): deep-links `t.me/<bot>?start=<vacancy_id>` +
   resolución sticky por conversación existente. *(Implementado 2026-07-01.)*
4. ✅ **N+1 + paginación** (D1+U1): embedded selects de PostgREST (`list_candidate_rows` +
   `count_candidates_by_status`) + `q`/`limit`/`offset` con búsqueda y controles en la UI.
   *(Implementado 2026-07-01.)*
5. ✅ **Visibilidad de fallbacks + latencia LLM** (O1): `calls/errors/duration_ms` por etapa en
   `llm_usage` (migración 0020) + logs de fallback. *(Implementado 2026-07-01.)*

---

## 1. Rate limiting

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| R1 | **ALTA** | `POST /api/auth/login` sin límite de intentos: brute force posible. | ✅ **Corregido**: `SlidingWindowLimiter` 5/min por IP → 429 (`api/ratelimit.py`, sin dependencias; por proceso). |
| R2 | **ALTA (costo)** | Bot Telegram: cualquiera hace `/start`; cada mensaje dispara 1–3 llamadas LLM, sin cooldown ni tope. | ✅ **Corregido**: `TurnGovernor` — cooldown 2 s por chat (silencio) + tope diario 120 turnos (aviso único, luego silencio); configurable (`BOT_TURN_COOLDOWN_SECONDS`/`BOT_MAX_TURNS_PER_DAY`). El corte corre ANTES de gastar LLM. |
| R3 | MEDIA | `psych-exam` no era idempotente (doble click = doble correo con credenciales). | ✅ **Corregido**: reenvío de las MISMAS credenciales → 409; credenciales nuevas se permiten. (De paso se corrigió un **bug latente**: el endpoint usaba `_now_iso()` sin definir → 500 en runtime.) |
| R4 | MEDIA | Listados sin paginación amplifican el costo por request (ver D1). | ✅ **Corregido (2026-07-02)**: la paginación la cerró D1+U1; `sync-applicants` ahora con throttle por tenant (`_sync_limiter` 2/min → 429). |

Nota de alcance: los límites son por proceso (una réplica = exacto; N réplicas = N× como peor
caso). Un límite global compartido requeriría Redis — fuera del MVP.

## 2. Seguridad

Fortalezas: JWT con rotación grácil, bcrypt, RBAC jerárquico, `test_tenant_guards` en CI, RLS
en 16 tablas, gate de secretos en prod, anti path-traversal doble, redacción del token Telegram,
escaping HTML en correos, auditoría, retención + erasure.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| S1 | **MEDIA** | Anti-inyección incompleta: `classify_turn`, `answer_candidate_question` y `parse_slot_choice` interpolaban el texto del candidato **crudo** en el prompt. | ✅ **Corregido**: los 3 prompts usan `sanitize_answer_for_prompt` (quita delimitadores + cap 4000) + marco `<<<respuesta>>>…<<<fin>>>` con instrucción anti-inyección (en `answer` además: no confirmar salario/condiciones fuera de `company_info`). |
| S2 | MEDIA | Sesión 12 h sin revocación: logout solo borra localStorage; desactivar un usuario no corta su token (`get_current_user` no relee la DB). Token en localStorage exfiltrable por XSS futura. | ✅ **Corregido (2026-07-02)**: `get_current_user` consulta `users.active` con caché TTL 60 s (`api.auth._is_user_revoked`) — desactivar al usuario corta su sesión viva; fail-open ante DB caída (revocar = desactivar, no borrar). Pendiente menor: cookie httpOnly (con XSS el token sigue en localStorage). |
| S3 | MEDIA | `candidates.psych_exam` guarda link+código+**clave** en claro, visible para cualquier `viewer` vía `GET /api/candidates/{id}` y repetido en `outbox.payload`. | ✅ **Corregido (2026-07-02)**: `_psych_exam_for_role` enmascara link/código/clave para `viewer` (recruiter+ las ve; el viewer ve cuándo/quién envió). El payload del outbox sigue en claro (admin-only) → cubierto por S4 (purga). |
| S4 | BAJA | Erasure deja PII residual: `outbox.payload` (correos completos) y `audit_log.summary` (nombres) sobreviven; `outbox.candidate_id` sin FK cascade (`0014`). | ✅ **Corregido (2026-07-02)**: erasure y `_retention_sweep` purgan `outbox` (`delete_outbox_by_candidate`) y vacían los resúmenes de auditoría (`scrub_audit_for_entity`); el audit del borrado ya no incluye el nombre; FK cascade en `outbox.candidate_id` (migración 0022). |
| S5 | BAJA | CORS hardcodeado a `localhost:3000` (`api/main.py`). | ✅ **Corregido (2026-07-02)**: `CORS_ORIGINS` (CSV) en settings/.env. |

## 3. Arquitectura

Fortalezas: separación por capas ejemplar (channels/agent/evaluation/db/notifications/
integrations), motor puro sin I/O, LLM inyectable (`Protocol`), Protocol+factory en
scheduling/sourcing, servicio agnóstico al canal, settings por-tenant.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| A1 | **ALTA (SaaS)** | El bot rompe el multi-tenancy: `service.process()` usa `get_default_open_vacancy()` **global** — un `/start` entrante cae en la primera vacante abierta de CUALQUIER tenant. | ✅ **Corregido**: `_resolve_context` resuelve en orden ① conversación existente del thread (sticky — de paso corrige el bug latente de que la respuesta de un candidato contactado para la vacante B cayera/duplicara en la default), ② deep-link `t.me/<bot>?start=<vacancy_id>` (payload validado como UUID; inexistente/cerrada → aviso sin crear candidato), ③ default (retrocompat mono-vacante). `TELEGRAM_BOT_USERNAME` habilita el deep-link copiable por vacante en el dashboard. |
| A2 | MEDIA | `api/main.py` (~1 600 líneas) mezcla scheduler, contacto, endpoints y modelos. | ✅ **Corregido (2026-07-02)**: partido en `api/runtime.py` (estado+defaults), `api/scheduler.py` (loop+barridos+contacto), `api/deps.py` (guards/helpers) y `api/routes/{vacancies,candidates,recruiters,settings,observability}.py`; `main.py` (~240 líneas) = lifespan + ensamblaje + re-exports de compat. |
| A3 | MEDIA | Carrera en el motor: el sweep (`finalize_inactive`) y un mensaje del candidato pueden invocar `runner.send()` del mismo thread desde dos hilos; LangGraph no serializa. | ✅ **Corregido (2026-07-02)**: `threading.Lock` por `thread_id` en `InterviewService` (process/finalize_inactive/initiate_contact/initiate_scheduling). |
| A4 | BAJA | El set `fired` del scheduler crece sin límite (fuga lenta). | ✅ **Corregido (2026-07-02)**: `_prune_fired_slots` purga los slots de fechas pasadas en cada tick (conserva ±1 día por zonas horarias). |
| A5 | BAJA | `get_or_create_candidate` select-then-insert sin unique → posible duplicado ante mensajes concurrentes. | ✅ **Corregido (2026-07-02)**: retry-on-conflict — el unique `(vacancy_id, channel, channel_user_id)` existía desde 0001; ante insert perdedor se relee la fila del ganador (sin duplicar). |

## 4. Base de datos

Fortalezas: 19 migraciones ordenadas, doble persistencia delimitada (negocio vía PostgREST /
checkpoints vía `DATABASE_URL`), PK compuesta en settings, `unique(conversation_id, stage)`,
RLS con `SECURITY DEFINER`, claim atómico de contacto.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| D1 | **MEDIA** | N+1 severo: `GET /api/candidates` = 1 query + 1/vacante + **2/candidato** (`_enrich_candidate_row`). 100 candidatos ≈ 200+ round-trips. Igual `list_vacancies`/`list_recruiters`. | ✅ **Corregido**: `repo.list_candidate_rows` (embed `conversations(scorecards)` vía FKs, columnas livianas, `count=exact` en la misma consulta) + `repo.count_candidates_by_status` (1 consulta de 2 columnas para stage_counts/carga del roster). Los 4 listados quedaron en 2–3 consultas fijas. Gotcha: PostgREST embebe `scorecards` como **objeto** (unique de `conversation_id`), no lista — `_candidate_row_from_embed` acepta ambas formas. |
| D2 | MEDIA | `candidate_documents.content_b64`: PDF de 20 MB ≈ 27 MB de JSON por request. | ✅ **Corregido (2026-07-02)**: `document_db_max_bytes` (config, 5 MB) — sobre el umbral el PDF queda `stored="disk"` y no viaja por PostgREST; migrar a Storage/S3 queda como optimización de escala. |
| D3 | BAJA | `replace_vacancy_questions` (delete+insert) y `_claim_chat` son multi-request sin transacción: un fallo a mitad deja estado inconsistente. | ✅ **Corregido (2026-07-02)**: RPCs atómicos `app_replace_vacancy_questions`/`app_claim_candidate_chat` (migración 0022) con fallback retro-compatible al camino multi-request si el RPC no existe. |
| D4 | BAJA | Checkpoints LangGraph crecen sin límite (solo el erasure y ahora la retención los borran). | ✅ **Corregido (2026-07-02)**: `purge_stale_checkpoints` + `_checkpoint_purge_sweep` en el scheduler (a lo sumo cada 6 h; config `checkpoint_retention_days`, default 30, 0 = off) — borra checkpoints de conversaciones terminales viejas. |
| D5 | INFO | Retención mide antigüedad por `created_at` (proxy). | ✅ **Corregido (2026-07-02)**: `_retention_reference_ts` usa `updated_at` (trigger de 0022 lo sella en cada cambio) con fallback `created_at` — el reloj corre desde el descarte, no desde la postulación. |

## 5. UX (dashboard)

Fortalezas: stepper multi-etapa, semáforo + radar, toasts por acción, redirect 401, guía
nativa, observabilidad admin, confirm en erasure.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| U1 | MEDIA | Sin paginación ni búsqueda en listas de candidatos. | ✅ **Corregido**: `q`/`limit`/`offset` en `GET /api/candidates` y `GET /api/vacancies/{id}/candidates` (respuesta `{items,total,limit,offset}`, limit clampeado a 500, ilike con comodines escapados); UI con input de búsqueda (debounce 300 ms) + controles ‹ Anteriores / Siguientes › en el detalle de vacante y el Pipeline global (aparecen solo si `total > 100`). |
| U2 | BAJA | Errores crudos (`"Error: " + String(e)`). | ✅ **Corregido (2026-07-02)**: `req()` extrae el `detail` en español del backend (fallback humano por código HTTP) + `errorMessage()` en todas las páginas. |
| U3 | BAJA | Erasure con `window.confirm` nativo. | ✅ **Corregido (2026-07-02)**: modal "escribe el nombre para confirmar" en el detalle del candidato (candidato anonimizado sin nombre → palabra `BORRAR`). |
| U4 | BAJA | Sesión expira en silencio (12 h) — riesgo de perder formularios. | ✅ **Corregido (2026-07-02)**: el 401 con sesión previa redirige a `/login?expired=1` y el login muestra el aviso. |

## 6. Observabilidad

Fortalezas: tokens por etapa → `llm_usage` → métricas con costo, outbox health + retry en UI,
audit log en UI, reconciliación, health con degradación visible.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| O1 | **MEDIA** | Fallbacks del LLM invisibles y sin latencia: `classify/answer/parse/scorecard` degradaban con `except: pass` sin contar. | ✅ **Corregido**: `MeteredLLM` acumula `calls/errors/duration_ms` por etapa → `llm_usage` (migración `0020`, aplicada en vivo; `record_usage` con fallback retro-compatible) → `avg_ms`/`errors` en `/api/metrics`; cada rama de fallback loggea `logger.warning("LLM fallback en …")`. |
| O2 | MEDIA | Alertas de reconciliación solo van a logs. | ✅ **Corregido (2026-07-02)**: `_collect_ops_alerts` (fuente única, filtrable por tenant) + `GET /api/ops/alerts` (admin) + sección "Alertas operativas" en `/observabilidad` con link al candidato. De paso: `list_meetings_without_link` excluye reuniones presenciales (falso positivo detectado en vivo). |
| O3 | BAJA | Sin métricas HTTP ni error tracking; LangSmith no cubre el motor (`llm.complete` directo). | ✅ **Corregido (2026-07-02)**: `api/httpmetrics.py` (acumulador thread-safe por PLANTILLA de ruta, cardinalidad acotada) + middleware en `api/main.py` + `GET /api/ops/http-metrics` (admin) + tarjeta "Rendimiento HTTP" en `/observabilidad`. Sentry queda como gancho para prod. |

## 7. Diseño de pipeline LLM

El turno implementa la cadena canónica: validación de input (`is_meaningful_answer`) →
clasificación → recuperación de contexto (`cv_context`) → generación → validación de output
(`parse_json_object` + clamp + caps + `low_confidence`) → registro (metering + persistencia).

- ✅ **RAG conectado (2026-07-02)**: `agent/rag.py` (`build_company_retriever`, config-gated por
  `INTERVIEW_RAG_ENABLED`, default off) inyecta un retriever en el runner (mismo patrón que el
  LLM: motor puro); las dudas del candidato se responden con fragmentos de Chroma + el
  `company_info` de la vacante. Carga lazy (torch recién en la primera duda) y fail-safe
  (degrada a `company_info` sin reintentar).
- ✅ **Versionado de prompts (2026-07-02)**: `agent/prompts.PROMPT_VERSION` se sella en cada
  scorecard y en `llm_usage` (migración `0021`, aplicada en vivo; escritura retro-compatible
  sin la columna). Subir la versión al cambiar materialmente un prompt de evaluación.
- ✅ **Suite golden (2026-07-02)**: `tests/golden/golden_set.json` (respuestas reales de Alberto
  + contraejemplos débiles, rangos esperados) + `scripts/golden_eval.py` (corrida manual contra
  el LLM real; exit 1 si algo cae fuera de rango). **Primera corrida: 9/9 con qwen3-32b.**

## 8. Estado conversacional y memoria

Fortalezas: `InterviewState` es curaduría contextual de libro (estado mínimo estructurado, no
chat crudo); **costo por turno constante** (nunca se envía historial acumulado al LLM; cap
4 000 chars); memoria larga en Supabase consultable por usuario/evento con retención y erasure;
`cv_profile` = personalización entre sesiones (revalidación).

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| M1 | **MEDIA** | La retención NO purgaba los checkpoints de LangGraph: la PII (`raw_answer`, `cv_profile`) sobrevivía a la anonimización en el blob del estado. | ✅ **Corregido**: `_retention_sweep` borra el checkpoint del thread. |
| M2 | BAJA | `answer_candidate_question` envía `company_info` completo (sin recuperación selectiva). OK hoy; es el punto de entrada del RAG. | ✅ **Cubierto (2026-07-02)** con la conexión del RAG (sección 7): recuperación selectiva por duda cuando `INTERVIEW_RAG_ENABLED=true`. |

## 9. Agente como grafo + consistencia transaccional

Fortalezas: máquina de estados explícita (7 fases, transiciones concentradas en `handle_turn`),
checkpointer Postgres reanudable, claim atómico con reversión (contacto), idempotencia por
(conversación, etapa) en reuniones y por existencia en scorecard, compensación visible
(reunión sin link → alerta de reconciliación).

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| G1 | **MEDIA** | Doble escritura sin transacción (checkpoint → proyección → envío): `_sync_business` se auto-repara al siguiente mensaje, pero si el candidato no vuelve la divergencia era permanente e invisible. | ✅ **Corregido**: la reconciliación compara fase del motor vs `conversations.state` y alerta (`state_divergence`). |
| G2 | **MEDIA** | El evento de Calendar se creaba ANTES de registrar la reunión: un fallo entre ambos dejaba un evento huérfano y el reintento lo **duplicaba** (el guard lee la DB). | ✅ **Corregido**: registro-primero (`save_meeting` → `create_meeting` → `update_meeting` con link/event_id). |
| G3 | BAJA | Mensajes persistidos en la transcripción antes de enviarse por Telegram: si el envío falla, la transcripción afirma algo que el candidato no recibió (el recordatorio de inactividad re-envía la pregunta — mitigación parcial). | ✅ **Corregido (2026-07-02)**: `_mark_delivery_result` en el bot + barrido de inactividad marcan `conversations.last_delivery_failed_at` (0022); alerta `delivery_failed` en `_collect_ops_alerts` solo si el candidato no interactuó después del fallo. |
| G4 | BAJA | Sin registro de transiciones con timestamp (solo estado actual): no se puede medir tiempo-por-estado ni reconstruir el flujo formalmente. | ✅ **Corregido (2026-07-02)**: tabla `state_transitions` (0022, RLS por tenant); `_sync_business` registra cada cambio de fase (una vez por transición) y `get_candidate_detail` devuelve `transitions`. |

## 10. Razonamiento iterativo y control de bucles

Fortalezas: follow-ups acotados por pregunta, `llm_max_retries` + timeout, outbox 6 intentos con
backoff → dead-letter, inactividad `max_reminders` → cierre, escalamiento humano
(`low_confidence` → `review_required`, decisiones de etapa siempre humanas), acciones
irreversibles con confirmación (claim atómico, decisiones manuales).

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| I1 | **MEDIA** | Loop de dudas del candidato infinito y con costo LLM (2 llamadas por duda, sin tope; cada mensaje resetea el reloj de inactividad). | ✅ **Corregido**: `MAX_CANDIDATE_QUESTIONS=3` por pregunta → corte `QUESTIONS_EXHAUSTED` sin LLM. |
| I2 | BAJA | `SCHEDULING_PICK_AGAIN` re-proponía sin límite (1 llamada LLM por intento; el barrido recuerda pero nunca cierra). | ✅ **Corregido**: `MAX_SLOT_RETRIES=3` → escala a RR.HH. (`SCHEDULING_ESCALATE`, una sola vez; una elección válida tardía sigue agendando). |
| I3 | INFO | `EMPTY_ANSWER_NUDGE` y el re-saludo ambiguo son ilimitados pero sin costo LLM; la inactividad cierra el saludo. | Aceptado. |
| I4 | BAJA | Observabilidad del ciclo: contadores ahora en el estado (`questions_asked`, `slot_retries`) → auditables vía checkpoint. | ✅ Cubierto con I1/I2. |
