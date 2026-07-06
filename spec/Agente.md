# Agente — Cerebro conversacional (LangGraph)

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `agente/`
> Spec normativo: `openspec/specs/entrevista/spec.md`

## 1. Propósito y alcance

El motor que conduce la conversación con el candidato: saludo/consentimiento, entrevista
pregunta-por-pregunta con follow-ups, respuesta a dudas (RAG), recepción de documentos,
coordinación de horarios multi-etapa y cierres (inactividad, rechazo, agendado). Cubre
`agente/state.py`, `graph.py`, `nodes.py`, `prompts.py`, `service.py`. La evaluación de
respuestas está en [Evaluacion.md](Evaluacion.md); el transporte en [Canales.md](Canales.md).

## 2. Decisiones de diseño

- **Un solo nodo LangGraph (`handle_turn`) + máquina de fases en el estado**: el valor de
  LangGraph aquí es el **checkpointer durable** (la conversación sobrevive reinicios) y la
  inyección de dependencias, no el ruteo entre nodos. El control de flujo en Python plano es
  más testeable y legible que un grafo de 15 nodos.
- **Motor puro, dependencias inyectadas**: LLM, retriever RAG y caché de respuestas entran por
  el runner (`graph.py`); en tests se inyectan fakes y el motor corre sin red ni torch.
- **Checkpointer dual**: `MemorySaver` en tests/demos, `PostgresSaver` en producción
  (`DATABASE_URL`, `PostgresSaver.setup()` al arrancar). `thread_id = canal:chat`
  (p. ej. `telegram:12345`) — identidad única de la conversación.
- **`service.py` como núcleo agnóstico de canal**: recibe `InboundMessage`, corre el turno,
  proyecta el estado al negocio (`_sync_business`) y notifica. El bot de Telegram y el script
  de verificación e2e usan EXACTAMENTE el mismo servicio.
- **Señales fuera-de-banda por `send()`**: además del texto del candidato, el runner acepta
  `timeout=True` (barrido de inactividad), `start_scheduling=`/`stage=`/`modality=`/
  `interviewer=` (RR.HH. dispara coordinación de horario) — el mismo nodo las despacha.

## 3. Diseño e implementación

### Estado (`agente/state.py`)

Fases: `greeting` → `interviewing` → (`awaiting_docs`) → `scheduling` ↔ `scheduled` → `closed`.
Campos clave: índice de pregunta, follow-ups usados, `questions_asked` (dudas), `answers`
(registros por criterio), `pending_timeout`, `closed_reason` (`no_response`/`declined`),
`proposed_slots`/`meeting_slot`/`slot_retries`, `scheduling_stage`/`modality`/`interviewer`,
documentos recibidos, `cv_context` (revalidación del CV en el scoring).

### Un turno (camino feliz `interviewing`)

1. Gobierno del turno en el bot (cooldown/cap/dedupe — antes de gastar LLM).
2. `service.process()`: resuelve contexto (ver abajo), refresca el LLM del tenant (BYOK),
   toma el **lock por thread** y corre `runner.send(texto)`.
3. `nodes.handle_turn` → `_handle_interview`: guard de respuesta vacía
   (`is_meaningful_answer` → repregunta sin gastar LLM) → clasificador de turno (¿respuesta,
   duda del puesto u off-topic?) → si es duda: tope 3, caché semántica, detección de eco-inyección,
   RAG; si es respuesta: evaluación contra el criterio → follow-up (si vaga y quedan) o
   siguiente pregunta.
4. `_sync_business`: proyecta fase→`candidates.status` (con `core/estados.py`), persiste
   mensajes/respuestas/scorecard, sella `consent_at`, registra `state_transitions`, resetea el
   reloj de inactividad.
5. `_record_usage`: tokens/latencia por etapa + fila sintética `stage="turn"` (latencia e2e).

### Resolución de contexto (`_resolve_context`, multi-tenant)

Orden: ① conversación existente del thread (sticky — su vacante/candidato), ② deep-link
`t.me/<bot>?start=<vacancy_id>` (payload validado como UUID **antes** de tocar la DB; vacante
inexistente/cerrada → aviso sin crear candidato), ③ fallback a la vacante abierta default
(retrocompat demo). Evita que un candidato de un tenant caiga en la vacante de otro.

### Límites de iteración (anti-bucle)

- `INTERVIEW_MAX_FOLLOW_UPS=1` por pregunta.
- `MAX_CANDIDATE_QUESTIONS=3` dudas por pregunta → corte `QUESTIONS_EXHAUSTED` sin LLM.
- `MAX_SLOT_RETRIES=3` al elegir horario → escala a RR.HH. una sola vez (`SCHEDULING_ESCALATE`);
  una elección válida tardía sigue agendando.

### Inactividad y cierres

El barrido manda `REMINDER_*` según fase (saludo/entrevista/docs/scheduling) y al agotar
recordatorios envía `timeout=True`: `interviewing`→`closed` (`no_response`),
`awaiting_docs`→`finished` sin penalizar, `greeting`→`closed`. La coordinación de horario no se
auto-cierra (escala). Estados médicos/`hired` cortocircuitan con un acuse sin reprocesar
(`_MEDICAL_HOLD_STATUSES`).

### Concurrencia

`threading.Lock` por `thread_id` (barrido vs mensaje simultáneo en el mismo proceso) + **lock
distribuido por thread** en `agente/service.py` para multi-réplica (webhook con `replicas:2`).
El claim del chat demo purga conversación + checkpoint del thread reasignado antes de asignarlo
(atribución correcta de mensajes; RPC atómico `app_claim_candidate_chat`).

## 4. Contratos e invariantes

- El motor **nunca llama a la DB de negocio**: todo pasa por el servicio.
- Todo texto del candidato que entra a un prompt va **sanitizado y delimitado**
  (ver [Seguridad.md](Seguridad.md)); las rutas sin LLM (respuesta vacía, tope de dudas,
  eco-inyección, acuse terminal) no consumen tokens.
- Un turno es atómico por thread: dos entradas concurrentes al mismo thread se serializan.
- `prompts.py` lleva `PROMPT_VERSION` (hoy `2026-07-05.2`) + changelog embebido; cambiar
  prompts sin subir la versión **rompe CI**.

## 5. Patrones reutilizables

- **Grafo mínimo + máquina de fases explícita**: usar el framework de agentes por su
  durabilidad, no por su DSL de control de flujo.
- **Servicio agnóstico de canal**: si el e2e se puede driver por llamadas directas al mismo
  servicio que usa el transporte real, la verificación es barata y fiel.
- **Señales tipadas al motor** (`timeout`, `start_scheduling`) en vez de mensajes mágicos:
  el mismo entrypoint procesa candidato, barridos y acciones de RR.HH.
- **Presupuestos de iteración en el estado**: todo ciclo LLM↔usuario lleva contador y salida
  determinista; un bucle sin tope es un costo/DoS latente.

## 6. Pendientes conocidos

- Adapter WhatsApp (el motor ya es agnóstico; falta el canal).
- Recordatorios de inactividad para estados `medical_*` (hoy solo acuse).

## 7. Trazabilidad

- Tests: `test_interview.py`, `test_routing.py`, `test_iteration_limits.py` (8),
  `test_inactivity.py`, `test_conversation_lock.py`, `test_multistage.py`, `test_scheduling.py`.
- E2E vivo: `scripts/verify_multistage.py` (mismo servicio, DB + LLM reales).
- Migraciones: `0009` (inactividad), `0019` (multi-etapa), `0022` (RPC claim + transiciones).
