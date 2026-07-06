# Auditoría v3 — madurez + diseño de mejoras: examen médico y onboarding

**Fecha:** 2026-07-05 · **Alcance:** evaluación/diseño → **Partes B y C IMPLEMENTADAS el mismo día**
(migración `0027`, endpoints, outbox, scheduler, frontend; 403/403 tests verde — ver bitácora en CLAUDE.md)
**Guía:** `audit/chekeo.md` (rúbrica "Repositorio de IA en Producción") · **Línea base:** `audit/auditoria_v2.md`

## Contexto

Auditoría de madurez del agente guiada por `audit/chekeo.md` + evaluación de mejoras de robustez
del **proceso de negocio**, en particular dos brechas identificadas por el usuario: **examen
médico** pre-contratación (RR.HH. programa fecha+clínica, se notifica por correo y Telegram, con
el resultado se contrata y termina el proceso) y **onboarding** (materiales/guías para el primer
día de trabajo).

**Decisiones tomadas:** el examen médico lo programa RR.HH. (patrón psych-exam, sin tocar el
motor conversacional); el kit de onboarding se envía **automático en la fecha de ingreso**
(scheduler) con respaldo manual; **alcance de esta iteración = solo evaluar** — este documento
es el entregable; la implementación queda diseñada para una iteración futura.

## Parte A — Evaluación de madurez (estado verificado 2026-07-05)

- **Rúbrica `audit/chekeo.md`: 13/13 ✅** — las 7 carpetas existen con código real en `main`
  (reorg 2026-07-04 mergeada); evidencia: `docs/mapa_rubrica.md`. Incluye la sustitución
  consciente Arize→Phoenix self-hosted (Ley 29733) y el perfil prod con guard
  (`tests/test_prod_profile.py`).
- **Madurez LLMOps** (`audit/auditoria_v2.md`): 81/100, Nivel 3 consolidado / umbral Nivel 4;
  3.6/5 (Procesos 4.1; ancla Equipo 2.6, unipersonal).
- **Riesgos v2 aún abiertos**: R2 (unipersonal + secretos planos — pendiente acción externa del
  usuario), R3 (carrera de checkpoint con `replicas:2` webhook — advisory lock por `thread_id`
  pendiente, roadmap v2 paso 2), paso 3 del roadmap (relevancia de contexto RAGAS).
- **Brecha NUEVA que esta auditoría aporta (dimensión funcional/negocio, no cubierta por v1/v2)**:
  el proceso muere abruptamente en `hired` — verificado:
  - `api/routes/candidates.py::advance_stage` (líneas ~434-440): aprobar `manager` →
    `status="hired"` + `deliver_candidate_notify(DECISION_HIRED)` → **solo Telegram, sin email**;
    `NOTIFY_HIRED` (`agente/prompts.py:401`) promete "coordinar los detalles de tu incorporación"
    pero no existe nada después.
  - **Cero rastro** de examen médico ni onboarding en el repo (greps verificados; los hits de
    "medic" son "medición" de observabilidad).
  - Sin fecha de ingreso, sin materiales por vacante, sin estado post-`hired`.

## Parte B — Diseño: examen médico pre-contratación (patrón psych-exam) — ✅ IMPLEMENTADO

Implementado 2026-07-05 tal como se diseñó (más los endurecimientos de la revisión de robustez):

- **Reordenar el cierre**: aprobar la etapa `manager` ya NO contrata directo; pasa a
  `status="medical_pending"`. Nuevo flujo: `mgr_scheduled` → feedback aprobado →
  `medical_pending` → RR.HH. programa cita → `medical_scheduled` → RR.HH. registra resultado →
  apto: `hired` + `NOTIFY_HIRED` (y recién ahí termina) / no apto: `rejected` + notificación.
  Config-gated (`medical_exam_enabled`, patrón `auto_contact_on_pass`): apagado = comportamiento
  actual (retrocompat, mismo criterio que líder/gerencia opcionales).
- **Migración `0027_medical_exam.sql`**: `candidates.medical_exam jsonb` (espejo de
  `psych_exam` de `0019`: `{clinic, address, scheduled_at, instructions, sent_at, sent_by,
  result, result_at, result_by}`).
- **Endpoints** (rol `recruiter`, tenant-guarded, auditados, patrón
  `send_psych_exam` en `api/routes/candidates.py:329`):
  - `POST /api/candidates/{id}/medical-exam` — programa cita (idempotencia 409 si mismos datos,
    como psych-exam) → notifica **correo + Telegram** vía outbox.
  - `POST /api/candidates/{id}/medical-result` — registra `apto|no_apto` (+notas) → dispara la
    contratación o el rechazo con sus notificaciones.
- **Notificaciones**: `build_medical_exam_email` en `notifications/email.py` (espejo de
  `build_psych_exam_email`, líneas 231-285: destinatario = `cv_profile.email`); kind nuevo
  `medical_exam_email` en `_HANDLERS` de `notifications/outbox.py` (+ builder
  `deliver_medical_exam`); Telegram por `deliver_candidate_notify`/kind `candidate_notify` con
  prompt nuevo `NOTIFY_MEDICAL_EXAM` (fecha, clínica, dirección, indicaciones) en
  `agente/prompts.py`. El hired final debería ganar TAMBIÉN correo (hoy solo Telegram — ver
  Parte D, ítem 1).
- **Estados** (los 3 puntos de emisión/render existentes): `frontend/src/lib/stages.ts` (`STAGE`
  + `KANBAN_COLUMNS`), `frontend/src/lib/api.ts` (`phaseMeta` + `PHASE_STEPS` — insertar
  "Examen médico" entre Gerencia y Decisión), backend emite en los endpoints (no en
  `_sync_business`, igual que `advanced`/`hired`).
- **Frontend**: panel "🩺 Examen médico" en `frontend/src/app/candidatos/[id]/page.tsx` (espejo
  del panel psych-exam, líneas ~196-217): formulario clínica/fecha/indicaciones → enviado →
  botones de resultado Apto/No apto. Enmascarado por rol NO necesario (no hay credenciales),
  pero sí mostrar quién programó/registró.
- **Barrido de reconciliación**: alerta si `medical_pending`/`medical_scheduled` estancado
  > N días (patrón scheduling-stuck de `_reconciliation_sweep`).
- **Tests**: `test_medical.py` espejo de los tests de psych-exam/multistage (idempotencia 409,
  RBAC, tenant, apto→hired+notifs, no_apto→rejected, retrocompat con flag off).

## Parte C — Diseño: onboarding automático en la fecha de ingreso — ✅ IMPLEMENTADO

- **Migración (misma `0027`)**: `candidates.start_date date` + `candidates.onboarding jsonb`
  (`{sent_at, sent_by, channel}`); `vacancies.onboarding_kit jsonb` (lista de materiales:
  `[{title, url?, note?}]` + mensaje de bienvenida) — editable por vacante en el dashboard.
- **Flujo**: al registrar resultado apto (o después), RR.HH. fija `start_date` en el detalle del
  candidato. Barrido nuevo **`_onboarding_sweep`** en `api/scheduler.py` (patrón
  budget/SLA: por-tenant, dedupe una-vez, respeta horario laboral de `scheduling`): el día
  `start_date` por la mañana envía el kit por **correo + Telegram** (outbox kind
  `onboarding_email` + `candidate_notify` con prompt `NOTIFY_ONBOARDING` que lista los
  materiales) y sella `onboarding.sent_at`. **Respaldo manual**: botón "Enviar kit ahora" →
  `POST /api/candidates/{id}/onboarding` (idempotente).
- **Estado**: `hired` se mantiene; el kanban/detalle muestran badge "Onboarding enviado ✓" desde
  `onboarding.sent_at` (sin estado nuevo — hired sigue siendo terminal, menos superficie).
- **Frontend**: editor del kit en el detalle/alta de vacante; en el detalle del candidato
  contratado: fecha de ingreso + estado del kit + botón manual.
- **Tests**: sweep (envía solo el día correcto, dedupe, tenant), endpoint manual, retrocompat
  sin kit configurado (no envía nada, sin error).

## Parte D — Otras mejoras de robustez (backlog priorizado)

0. ✅ (implementado junto con B/C) **Fix del gap de anonimización**: `anonymize_candidate`
   ahora purga también `psych_exam`/`medical_exam`/`onboarding`/`start_date` (antes las
   credenciales del examen psicológico sobrevivían a la retención) + alerta `medical_stuck`
   en reconciliación (ítem 5 de esta lista, cubierto para los estados médicos).
1. **Email de contratación** — `hired` hoy solo notifica por Telegram; añadir kind
   `hired_email` (brecha detectada en esta auditoría).
2. **Roadmap v2 paso 2** — advisory lock por `thread_id` en `InterviewService.process()`
   (Riesgo 3: carrera de checkpoint con `replicas:2` webhook) + e2e webhook real.
3. **Roadmap v2 paso 3** — relevancia de contexto (tercer criterio RAGAS) en
   `evaluation/quality.py` + `quality_metrics`.
4. **Enum central de estados** — los status strings se emiten en múltiples sitios sin
   validación (columna text libre); consolidar en un módulo único compartido backend/frontend.
5. **Inactividad post-manager** — los estados nuevos (`medical_*`) deben entrar al barrido de
   recordatorios/reconciliación para no crear un limbo nuevo.
6. **Pendientes externos del usuario** (recordatorio, no código): secret manager real,
   segundo operador, branch protection (roadmap v2 paso 4).

## Verificación de este informe

- Cita rutas/archivos reales verificados en la sesión de auditoría (advance_stage, psych-exam,
  outbox kinds, stages.ts/api.ts, migración 0019, ausencia de onboarding/médico por grep).
- Contrastado contra `audit/auditoria_v2.md` (no contradice puntajes previos; añade la
  dimensión funcional del proceso).
- La implementación de las Partes B y C queda pendiente de aprobación del usuario (preguntar
  antes de arrancar, como con las fases previas).
