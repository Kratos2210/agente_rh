# Auditoría end-to-end — `agente_rh`

**Fecha:** 2026-07-01 · **Alcance:** todo el sistema (backend FastAPI + motor LangGraph +
evaluación LLM + Supabase + bot Telegram + dashboard Next.js), evaluado en 10 dimensiones:
seguridad, arquitectura, base de datos, UX, observabilidad, rate limiting, diseño de pipeline
LLM, estado conversacional/memoria, agente-como-grafo/consistencia transaccional y
razonamiento iterativo/control de bucles.

> Complementa `auditoria_integraciones_externas.md` (F1–F5, todos cerrados). Esta auditoría
> cubre el resto del sistema. Los hallazgos M1, G1, G2, I1 e I2 se **implementaron el mismo
> día** (ver bitácora); el resto queda como backlog priorizado.

## Resumen ejecutivo

El proyecto está muy por encima del estándar de un MVP: outbox durable con dead-letter,
advisory lock, RLS latente, tenant guards blindados por CI, anti-inyección en la evaluación,
degradación visible, 170 tests. Las brechas más importantes detectadas fueron **la ausencia
total de rate limiting** (✅ corregida), la **sanitización de prompts incompleta** (✅ corregida)
y **el routing del bot que ignora el multi-tenancy** (pendiente — bloqueante para el objetivo
SaaS, junto con el N+1 de los listados).

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
| R4 | MEDIA | Listados sin paginación amplifican el costo por request (ver D1). | Backlog. `sync-applicants` sin throttle por tenant: backlog. |

Nota de alcance: los límites son por proceso (una réplica = exacto; N réplicas = N× como peor
caso). Un límite global compartido requeriría Redis — fuera del MVP.

## 2. Seguridad

Fortalezas: JWT con rotación grácil, bcrypt, RBAC jerárquico, `test_tenant_guards` en CI, RLS
en 16 tablas, gate de secretos en prod, anti path-traversal doble, redacción del token Telegram,
escaping HTML en correos, auditoría, retención + erasure.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| S1 | **MEDIA** | Anti-inyección incompleta: `classify_turn`, `answer_candidate_question` y `parse_slot_choice` interpolaban el texto del candidato **crudo** en el prompt. | ✅ **Corregido**: los 3 prompts usan `sanitize_answer_for_prompt` (quita delimitadores + cap 4000) + marco `<<<respuesta>>>…<<<fin>>>` con instrucción anti-inyección (en `answer` además: no confirmar salario/condiciones fuera de `company_info`). |
| S2 | MEDIA | Sesión 12 h sin revocación: logout solo borra localStorage; desactivar un usuario no corta su token (`get_current_user` no relee la DB). Token en localStorage exfiltrable por XSS futura. | Bajar expiración; chequear `active` en mutaciones; evaluar cookie httpOnly. |
| S3 | MEDIA | `candidates.psych_exam` guarda link+código+**clave** en claro, visible para cualquier `viewer` vía `GET /api/candidates/{id}` y repetido en `outbox.payload`. | Restringir por rol; evaluar cifrado. |
| S4 | BAJA | Erasure deja PII residual: `outbox.payload` (correos completos) y `audit_log.summary` (nombres) sobreviven; `outbox.candidate_id` sin FK cascade (`0014`). | Purga de outbox/audit en el erasure o al vencer retención. |
| S5 | BAJA | CORS hardcodeado a `localhost:3000` (`api/main.py`). | Parametrizar por settings antes de desplegar. |

## 3. Arquitectura

Fortalezas: separación por capas ejemplar (channels/agent/evaluation/db/notifications/
integrations), motor puro sin I/O, LLM inyectable (`Protocol`), Protocol+factory en
scheduling/sourcing, servicio agnóstico al canal, settings por-tenant.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| A1 | **ALTA (SaaS)** | El bot rompe el multi-tenancy: `service.process()` usa `get_default_open_vacancy()` **global** — un `/start` entrante cae en la primera vacante abierta de CUALQUIER tenant. | ✅ **Corregido**: `_resolve_context` resuelve en orden ① conversación existente del thread (sticky — de paso corrige el bug latente de que la respuesta de un candidato contactado para la vacante B cayera/duplicara en la default), ② deep-link `t.me/<bot>?start=<vacancy_id>` (payload validado como UUID; inexistente/cerrada → aviso sin crear candidato), ③ default (retrocompat mono-vacante). `TELEGRAM_BOT_USERNAME` habilita el deep-link copiable por vacante en el dashboard. |
| A2 | MEDIA | `api/main.py` (~1 600 líneas) mezcla scheduler, contacto, endpoints y modelos. | Extraer `api/scheduler.py` + `APIRouter`s por dominio. |
| A3 | MEDIA | Carrera en el motor: el sweep (`finalize_inactive`) y un mensaje del candidato pueden invocar `runner.send()` del mismo thread desde dos hilos; LangGraph no serializa. | `threading.Lock` por `thread_id` en el servicio. |
| A4 | BAJA | El set `fired` del scheduler crece sin límite (fuga lenta). | Purgar slots de fechas pasadas. |
| A5 | BAJA | `get_or_create_candidate` select-then-insert sin unique → posible duplicado ante mensajes concurrentes. | Unique `(vacancy_id, channel, channel_user_id)` + upsert. |

## 4. Base de datos

Fortalezas: 19 migraciones ordenadas, doble persistencia delimitada (negocio vía PostgREST /
checkpoints vía `DATABASE_URL`), PK compuesta en settings, `unique(conversation_id, stage)`,
RLS con `SECURITY DEFINER`, claim atómico de contacto.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| D1 | **MEDIA** | N+1 severo: `GET /api/candidates` = 1 query + 1/vacante + **2/candidato** (`_enrich_candidate_row`). 100 candidatos ≈ 200+ round-trips. Igual `list_vacancies`/`list_recruiters`. | ✅ **Corregido**: `repo.list_candidate_rows` (embed `conversations(scorecards)` vía FKs, columnas livianas, `count=exact` en la misma consulta) + `repo.count_candidates_by_status` (1 consulta de 2 columnas para stage_counts/carga del roster). Los 4 listados quedaron en 2–3 consultas fijas. Gotcha: PostgREST embebe `scorecards` como **objeto** (unique de `conversation_id`), no lista — `_candidate_row_from_embed` acepta ambas formas. |
| D2 | MEDIA | `candidate_documents.content_b64`: PDF de 20 MB ≈ 27 MB de JSON por request. | Umbral (>5 MB → Storage/S3) antes de escalar. |
| D3 | BAJA | `replace_vacancy_questions` (delete+insert) y `_claim_chat` son multi-request sin transacción: un fallo a mitad deja estado inconsistente. | RPC de Postgres (atómico). |
| D4 | BAJA | Checkpoints LangGraph crecen sin límite (solo el erasure y ahora la retención los borran). | Purga de checkpoints de conversaciones cerradas > N días. |
| D5 | INFO | Retención mide antigüedad por `created_at` (proxy). | `updated_at`/fecha de decisión. |

## 5. UX (dashboard)

Fortalezas: stepper multi-etapa, semáforo + radar, toasts por acción, redirect 401, guía
nativa, observabilidad admin, confirm en erasure.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| U1 | MEDIA | Sin paginación ni búsqueda en listas de candidatos. | ✅ **Corregido**: `q`/`limit`/`offset` en `GET /api/candidates` y `GET /api/vacancies/{id}/candidates` (respuesta `{items,total,limit,offset}`, limit clampeado a 500, ilike con comodines escapados); UI con input de búsqueda (debounce 300 ms) + controles ‹ Anteriores / Siguientes › en el detalle de vacante y el Pipeline global (aparecen solo si `total > 100`). |
| U2 | BAJA | Errores crudos (`"Error: " + String(e)`). | Mapear 409/403/404 a mensajes humanos. |
| U3 | BAJA | Erasure con `window.confirm` nativo. | Modal "escribe el nombre para confirmar". |
| U4 | BAJA | Sesión expira en silencio (12 h) — riesgo de perder formularios. | Aviso "sesión expirada" en el login. |

## 6. Observabilidad

Fortalezas: tokens por etapa → `llm_usage` → métricas con costo, outbox health + retry en UI,
audit log en UI, reconciliación, health con degradación visible.

| # | Sev. | Hallazgo | Remediación |
|---|------|----------|-------------|
| O1 | **MEDIA** | Fallbacks del LLM invisibles y sin latencia: `classify/answer/parse/scorecard` degradaban con `except: pass` sin contar. | ✅ **Corregido**: `MeteredLLM` acumula `calls/errors/duration_ms` por etapa → `llm_usage` (migración `0020`, aplicada en vivo; `record_usage` con fallback retro-compatible) → `avg_ms`/`errors` en `/api/metrics`; cada rama de fallback loggea `logger.warning("LLM fallback en …")`. |
| O2 | MEDIA | Alertas de reconciliación solo van a logs. | Email a ops vía outbox o avisos en `/observabilidad`. |
| O3 | BAJA | Sin métricas HTTP ni error tracking; LangSmith no cubre el motor (`llm.complete` directo). | Sentry / middleware de latencia cuando toque prod. |

## 7. Diseño de pipeline LLM

El turno implementa la cadena canónica: validación de input (`is_meaningful_answer`) →
clasificación → recuperación de contexto (`cv_context`) → generación → validación de output
(`parse_json_object` + clamp + caps + `low_confidence`) → registro (metering + persistencia).

- **RAG desconectado**: el motor Chroma+hybrid de `src/` no se usa; `answer_candidate_question`
  usa `company_info` plano. Conectarlo (lazy, sin torch en el arranque) cuando crezca la base
  de conocimiento.
- **Sin versionado de prompts**: cambiar `EVALUATE_ANSWER_PROMPT` deja scorecards no
  comparables sin registro de versión. Añadir `prompt_version` al scorecard/`llm_usage`.
- **Sin suite golden**: los 160 tests validan la lógica (FakeLLM), no la calidad del prompt
  contra el modelo real. Golden set (respuestas reales) con rangos de score esperados, corrida
  manual/nightly.

## 8. Estado conversacional y memoria

Fortalezas: `InterviewState` es curaduría contextual de libro (estado mínimo estructurado, no
chat crudo); **costo por turno constante** (nunca se envía historial acumulado al LLM; cap
4 000 chars); memoria larga en Supabase consultable por usuario/evento con retención y erasure;
`cv_profile` = personalización entre sesiones (revalidación).

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| M1 | **MEDIA** | La retención NO purgaba los checkpoints de LangGraph: la PII (`raw_answer`, `cv_profile`) sobrevivía a la anonimización en el blob del estado. | ✅ **Corregido**: `_retention_sweep` borra el checkpoint del thread. |
| M2 | BAJA | `answer_candidate_question` envía `company_info` completo (sin recuperación selectiva). OK hoy; es el punto de entrada del RAG. | Backlog (con el RAG). |

## 9. Agente como grafo + consistencia transaccional

Fortalezas: máquina de estados explícita (7 fases, transiciones concentradas en `handle_turn`),
checkpointer Postgres reanudable, claim atómico con reversión (contacto), idempotencia por
(conversación, etapa) en reuniones y por existencia en scorecard, compensación visible
(reunión sin link → alerta de reconciliación).

| # | Sev. | Hallazgo | Estado |
|---|------|----------|--------|
| G1 | **MEDIA** | Doble escritura sin transacción (checkpoint → proyección → envío): `_sync_business` se auto-repara al siguiente mensaje, pero si el candidato no vuelve la divergencia era permanente e invisible. | ✅ **Corregido**: la reconciliación compara fase del motor vs `conversations.state` y alerta (`state_divergence`). |
| G2 | **MEDIA** | El evento de Calendar se creaba ANTES de registrar la reunión: un fallo entre ambos dejaba un evento huérfano y el reintento lo **duplicaba** (el guard lee la DB). | ✅ **Corregido**: registro-primero (`save_meeting` → `create_meeting` → `update_meeting` con link/event_id). |
| G3 | BAJA | Mensajes persistidos en la transcripción antes de enviarse por Telegram: si el envío falla, la transcripción afirma algo que el candidato no recibió (el recordatorio de inactividad re-envía la pregunta — mitigación parcial). | Backlog (documentado). |
| G4 | BAJA | Sin registro de transiciones con timestamp (solo estado actual): no se puede medir tiempo-por-estado ni reconstruir el flujo formalmente. | Backlog: tabla/log `state_transitions`. |

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
