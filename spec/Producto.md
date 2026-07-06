# Producto — Flujo funcional end-to-end

> Parte de [spec/](README.md) · Última revisión: 2026-07-06
> Specs normativos del flujo: `openspec/specs/contacto/spec.md` · `openspec/specs/entrevista/spec.md` · `openspec/specs/examenes-contratacion/spec.md`

## 1. Propósito y alcance

Agente de selección de talento que **automatiza el embudo completo de reclutamiento**: desde la
importación de postulantes de un portal hasta el onboarding del contratado. El corazón es una
**entrevista conversacional por Telegram** conducida por IA que evalúa cada respuesta contra los
criterios de la vacante y produce un **scorecard con semáforo (🟢/🟡/🔴) + recomendación** para el
reclutador. Inspirado en "SofIA" de Sifrah. Fuera de alcance (hoy): WhatsApp (stub), conectores
reales de portales (simulado con fixture).

## 2. Decisiones de diseño

- **Telegram primero, WhatsApp después**: Telegram en modo polling no requiere infraestructura
  pública para desarrollar; la interfaz `Channel` deja el canal intercambiable (ver [Canales.md](Canales.md)).
- **El agente entrevista, el humano decide**: toda transición de etapa (avanzar/rechazar,
  asistencia, resultado médico) la ejecuta RR.HH. desde el dashboard; el agente propone
  (scorecard, recomendación) pero no decide.
- **Proceso multi-etapa config-gated**: sin líder/gerencia asignados a la vacante, el proceso
  cierra en la Fase 1 (retrocompatibilidad con el MVP de una sola entrevista). Igual el examen
  médico: con el setting apagado, aprobar gerencia contrata directo.
- **Contacto solo en horario laboral** (regla de negocio): 9–18, L–V, zona horaria por tenant;
  el sync fuera de hora difiere el contacto y un barrido lo recoge después.

## 3. Diseño e implementación

### Actores

| Actor | Interfaz | Rol |
|---|---|---|
| Candidato | Telegram (bot) | Responde la entrevista, sube CV/CUL, elige horarios |
| Reclutador (RR.HH.) | Dashboard Next.js | Carga vacantes, decide avanzar/rechazar, coordina etapas |
| Líder del proyecto / Gerencia | Reunión (Meet/presencial) | Entrevistas Fase 2 y 3; feedback vía RR.HH. |
| Admin | Dashboard | Configuración, usuarios, observabilidad, erasure |
| Asistente LLM externo | Servidor MCP | Consulta (y muta con confirmación) el pipeline |

### Flujo end-to-end

1. **Vacante**: RR.HH. la crea con requisitos, preguntas (con `cv_field` y `label`), criterios,
   reclutador responsable, líder y gerencia opcionales, kit de onboarding. Se genera un
   **deep-link** `t.me/<bot>?start=<vacancy_id>` para publicar en el aviso.
2. **Sourcing**: "Sincronizar postulantes" importa candidatos del portal
   (`integrations/sourcing.py`, dedupe por `source_ref`) → estado `sourced`.
3. **Pre-filtro (gate de CV)**: `evaluation/prescreen.py` puntúa el CV contra la vacante
   (umbral `PRESCREEN_PASS_MIN=60`) → `prescreen_passed` o `prescreen_rejected`.
4. **Contacto**: automático al pasar el gate (`AUTO_CONTACT_ON_PASS=true`, solo en horario
   laboral) o manual con el botón del dashboard. Idempotente → `invited`.
5. **Consentimiento**: saludo + botones **Acepto / No interesado**. Acepto sella `consent_at`
   (Ley 29733) y envía el detalle del puesto → `interviewing`; rechazo → `declined`.
6. **Entrevista**: pregunta por pregunta con follow-ups si la respuesta es vaga; las preguntas
   con `cv_field` se reformulan "Según tu CV: «…»" (revalidación); el candidato puede hacer
   dudas sobre el puesto (respondidas con RAG, tope 3 por pregunta). Ver [Agente.md](Agente.md).
7. **Scorecard**: al terminar se evalúa todo → semáforo + recomendación; email al reclutador y
   detalle en el dashboard → `finished`. Si es 🟢, felicita y pide **CV y CUL** (PDF por
   Telegram, se pueden omitir).
8. **Fase 1 (RR.HH.)**: "Continuar" → el agente coordina horario con el candidato (2–3 opciones
   de agenda real o simulada) → reunión con Meet → `scheduled`. Asistencia: `attended`/`no_show`.
9. **Fase 2 (Líder)** y **Fase 3 (Gerencia)**: mismo mecanismo generalizado por `stage`
   (`hr`/`lead`/`manager`); líder elige modalidad (Meet o presencial), gerencia es 100%
   presencial (evento con `location`, sin Meet). Feedback + decisión por etapa en `stage_feedback`.
10. **Exámenes**: psicológico (RR.HH. envía link+credenciales por correo, en cualquier momento)
    y **médico** (config-gated: aprobar gerencia → `medical_pending` → cita → resultado
    `apto`→`hired` / `no_apto`→`rejected`).
11. **Contratación y onboarding**: `hired` notifica por Telegram + correo; RR.HH. fija
    `start_date` y el día de ingreso un barrido envía el **kit de onboarding** (correo +
    Telegram, idempotente, con respaldo manual). `hired` es terminal.

### Estados del candidato (catálogo único)

Definidos en `core/estados.py` (22 estados; espejo frontend en `frontend/src/lib/stages.ts`;
todo write pasa por `ensure_valid_status` — un typo lanza `ValueError` en el punto de escritura):

| Grupo | Estados |
|---|---|
| Sourcing / pre-filtro | `pending`, `sourced`, `prescreen_passed`, `prescreen_rejected` |
| Contacto y entrevista | `invited`, `consented`, `interviewing`, `finished` |
| Multi-etapa | `advanced`, `scheduling`, `scheduled`, `lead_scheduling`, `lead_scheduled`, `mgr_scheduling`, `mgr_scheduled` |
| Cierre | `medical_pending`, `medical_scheduled`, `hired`, `rejected` |
| Fuera del camino feliz | `declined`, `no_response`, `no_show` |

### Reglas de negocio transversales

- **Inactividad**: recordatorios configurables por tenant (`reminder_minutes`, `max_reminders`)
  en saludo, entrevista, documentos y coordinación de horario; agota → cierre `no_response`
  (entrevista) o `finished` con docs pendientes (sin penalizar). La coordinación de horario no
  se auto-cierra: escala a RR.HH.
- **Idempotencias**: re-sync no duplica ni re-contacta (`source_ref`); contactar solo desde
  `prescreen_passed` (409 si no); reenviar credenciales de examen idénticas → 409; kit de
  onboarding se sella antes de despachar (carrera manual-vs-barrido cerrada).
- **Privacidad**: consentimiento sellado una sola vez; retención anonimiza descartados > N días
  (PII + transcripción + checkpoint + exámenes); erasure total por admin. Ver [Seguridad.md](Seguridad.md).

## 4. Contratos e invariantes

- `hired` y los estados de cierre son **terminales para el motor**: un mensaje del candidato en
  `medical_*`/`hired` recibe un acuse sin reprocesar el turno (`_MEDICAL_HOLD_STATUSES` en
  `agente/service.py`).
- El scorecard se guarda y notifica **apenas existe** (no espera los documentos CV/CUL).
- Un candidato pertenece a UNA vacante y las conversaciones se identifican por
  `langgraph_thread_id = canal:chat` único.
- Sin `recruiter_id`/líder/gerencia en la vacante, cada fase ausente se salta con gracia.

## 5. Patrones reutilizables

- **Embudo con estados explícitos + catálogo central**: un módulo de constantes con guard de
  escritura evita typos silenciosos y da paridad backend↔frontend testeada.
- **Capability ≠ autoridad**: la IA evalúa y propone; las transiciones las dispara un humano
  (endpoints auditados). Reduce el radio de daño de errores del LLM.
- **Config-gated para features de proceso**: cada etapa nueva (líder, gerencia, médico,
  onboarding) nace detrás de un flag/asignación opcional → retrocompatibilidad garantizada.
- **Idempotencia como default**: toda acción disparable dos veces (sync, contacto, credenciales,
  kit) debe ser segura de repetir; devolver 409 o no-op explícito.

## 6. Pendientes conocidos

- WhatsApp Cloud API (stub en `channels/whatsapp.py`).
- Conectores reales de portales (Bumeran/LinkedIn); hoy `SimulatedConnector` + fixture.
- Recordatorio de inactividad post-gerencia para estados `medical_*` (backlog auditoría v3).

## 7. Trazabilidad

- Tests: `test_interview.py`, `test_multistage.py` (16), `test_medical.py`/`test_onboarding.py`
  (24), `test_contact.py`, `test_inactivity.py`, `test_routing.py`, `test_estados.py` (paridad
  con el frontend), `test_sourcing.py`, `test_prescreen.py`.
- Migraciones: `0001` (núcleo), `0005` (prescreening), `0010` (scheduling), `0019` (multi-etapa),
  `0027` (médico + onboarding).
- Verificación e2e viva: `scripts/verify_multistage.py` (flujo completo contra DB real + LLM real).
