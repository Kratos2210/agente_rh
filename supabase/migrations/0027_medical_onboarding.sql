-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Examen médico pre-contratación + onboarding (auditoría v3, Partes B y C).   ║
-- ║                                                                             ║
-- ║ Antes el proceso moría en `hired` al aprobar gerencia. Ahora (config-gated  ║
-- ║ por el app_setting por-tenant `medical_exam`, default off):                 ║
-- ║   aprobar gerencia → medical_pending → RR.HH. programa fecha+clínica        ║
-- ║   (correo + Telegram) → medical_scheduled → resultado apto = hired /        ║
-- ║   no_apto = rejected. Tras la contratación, RR.HH. fija la fecha de ingreso ║
-- ║   y el scheduler envía el kit de onboarding de la vacante ese día.          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Candidato: examen médico ──────────────────────────────────────────────────────
-- {clinic, address, scheduled_at, instructions, sent_at, sent_by,
--  result (apto|no_apto), result_at, result_by, result_notes}
-- El RESULTADO es dato sensible de salud (Ley 29733): se enmascara para viewer en la
-- API y se purga en la anonimización de retención (junto con psych_exam).
alter table candidates add column if not exists medical_exam jsonb;

-- ── Candidato: incorporación ──────────────────────────────────────────────────────
-- Fecha del primer día de trabajo (la fija RR.HH. al contratar) y sello del envío
-- del kit de onboarding: {sent_at, sent_by} — guard de idempotencia del barrido.
alter table candidates add column if not exists start_date date;
alter table candidates add column if not exists onboarding jsonb;

-- ── Vacante: kit de onboarding ────────────────────────────────────────────────────
-- {welcome, materials: [{title, url?, note?}]} — materiales/guías del primer día,
-- editables por vacante desde el dashboard.
alter table vacancies add column if not exists onboarding_kit jsonb;

-- Sin tablas nuevas: grants y RLS a nivel tabla (0003/0018) ya cubren estas columnas.

-- Recordatorio operativo: si se aplica por psql directo, recargar el esquema de PostgREST:
--   NOTIFY pgrst, 'reload schema';
