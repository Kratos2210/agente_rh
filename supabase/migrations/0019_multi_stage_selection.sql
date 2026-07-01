-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Proceso de selección multi-etapa (Fases 2 y 3).                              ║
-- ║                                                                             ║
-- ║ Hoy el proceso termina en la Fase 1 (entrevista con RR.HH.). Se generaliza  ║
-- ║ el agendamiento a un `stage` con 3 valores:                                 ║
-- ║   • hr      → Fase 1: entrevista con RR.HH. (ya existente)                    ║
-- ║   • lead    → Fase 2: entrevista con el líder del proyecto (presencial/Meet) ║
-- ║   • manager → Fase 3: entrevista final con gerencia (100% presencial)        ║
-- ║                                                                             ║
-- ║ Cada etapa: agenda su reunión, RR.HH. marca asistencia y registra el         ║
-- ║ feedback del entrevistador + decisión (aprobar/rechazar). En la Fase 1       ║
-- ║ además se comparten por correo los exámenes psicológicos (link+código+clave).║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Reclutadores: dirección de oficina (para entrevistas presenciales) ───────────
alter table recruiters add column if not exists location text not null default '';

-- ── Vacante: entrevistadores de las fases 2 y 3 (además del RR.HH. = recruiter_id) ─
alter table vacancies add column if not exists lead_recruiter_id uuid references recruiters(id) on delete set null;
alter table vacancies add column if not exists manager_recruiter_id uuid references recruiters(id) on delete set null;

-- ── Reuniones: generalizar a multi-etapa ─────────────────────────────────────────
alter table meetings add column if not exists stage text not null default 'hr';          -- hr | lead | manager
alter table meetings add column if not exists modality text not null default 'virtual';   -- virtual (Meet) | onsite (presencial)
alter table meetings add column if not exists location text not null default '';          -- dirección presencial
alter table meetings add column if not exists attendance text not null default '';        -- '' | attended | no_show

-- Una reunión por (conversación, etapa): el candidato puede tener hasta 3 reuniones.
alter table meetings drop constraint if exists meetings_conversation_id_key;
alter table meetings add constraint meetings_conversation_stage_key unique (conversation_id, stage);

-- ── Candidato: examen psicológico enviado en Fase 1 ──────────────────────────────
-- {link, code, key, sent_at, sent_by} — credenciales de la plataforma externa que
-- RR.HH. pega en el dashboard; el sistema envía el correo con formato y registra aquí.
alter table candidates add column if not exists psych_exam jsonb;

-- ── Feedback + decisión por etapa (líder / gerencia / RR.HH.) ────────────────────
create table if not exists stage_feedback (
    id              uuid primary key default gen_random_uuid(),
    candidate_id    uuid not null references candidates(id) on delete cascade,
    conversation_id uuid references conversations(id) on delete cascade,
    stage           text not null,                 -- hr | lead | manager
    feedback        text not null default '',
    decision        text not null default '',      -- approved | rejected
    decided_by      uuid,                           -- users.id de quien decidió
    decided_email   text not null default '',
    created_at      timestamptz not null default now()
);

create index if not exists idx_stage_feedback_candidate on stage_feedback(candidate_id, created_at desc);
grant all privileges on stage_feedback to service_role;

-- ── RLS por tenant (defensa en profundidad, igual patrón que 0018) ───────────────
-- stage_feedback es tabla hija → sube al tenant por el candidato.
alter table stage_feedback enable row level security;
drop policy if exists tenant_isolation on stage_feedback;
create policy tenant_isolation on stage_feedback for all to anon, authenticated
    using (app_candidate_tenant(candidate_id) = app_current_tenant())
    with check (app_candidate_tenant(candidate_id) = app_current_tenant());

-- ── Seed demo: líder del proyecto + gerencia asignados a la vacante de ejemplo ───
insert into recruiters (name, email, company, role, calendar_id, location)
select 'Christian Benites', '', 'SIFRAH', 'Jefe de TI (Líder del proyecto)', 'primary',
       'Av. El Derby 254, Piso 19, Santiago de Surco, Lima'
where not exists (select 1 from recruiters where name = 'Christian Benites');

insert into recruiters (name, email, company, role, calendar_id, location)
select 'Gerencia', '', 'SIFRAH', 'Gerencia', 'primary',
       'Av. El Derby 254, Piso 19, Santiago de Surco, Lima'
where not exists (select 1 from recruiters where role = 'Gerencia');

update vacancies set
    lead_recruiter_id = coalesce(lead_recruiter_id, (select id from recruiters where name = 'Christian Benites' limit 1)),
    manager_recruiter_id = coalesce(manager_recruiter_id, (select id from recruiters where role = 'Gerencia' limit 1))
where title = 'Analista de Automatizaciones e IA';

-- Recordatorio operativo: si se aplica por psql directo, recargar el esquema de PostgREST:
--   NOTIFY pgrst, 'reload schema';
