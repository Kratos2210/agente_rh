-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Agendamiento de entrevista (fase 2) + roster de reclutadores (RR.HH.).      ║
-- ║ Cuando RR.HH. decide "Continuar", el agente coordina por Telegram un        ║
-- ║ horario contra la disponibilidad del reclutador asignado (Google Calendar), ║
-- ║ crea la reunión (enlace Meet), la registra en Sheets e invita por correo.   ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Roster de reclutadores (RR.HH. que supervisan procesos) ──────────────────────
create table if not exists recruiters (
    id                  uuid primary key default gen_random_uuid(),
    name                text not null,
    email               text not null default '',
    company             text not null default '',          -- firma de los mensajes (ej. "SIFRAH")
    role                text not null default 'Reclutador', -- cargo, para la cartilla
    phone               text not null default '',
    telegram_chat_id    text not null default '',           -- para notificarle por Telegram
    calendar_id         text not null default 'primary',    -- Google Calendar (free/busy + evento)
    active              boolean not null default true,
    created_at          timestamptz not null default now()
);

grant all privileges on recruiters to service_role;

-- ── Vacante: reclutador asignado + duración de la entrevista ─────────────────────
alter table vacancies add column if not exists recruiter_id uuid references recruiters(id) on delete set null;
alter table vacancies add column if not exists meeting_duration_minutes int not null default 45;

-- ── Reunión agendada (una por candidato/conversación) ────────────────────────────
create table if not exists meetings (
    id              uuid primary key default gen_random_uuid(),
    candidate_id    uuid not null references candidates(id) on delete cascade,
    conversation_id uuid not null references conversations(id) on delete cascade,
    vacancy_id      uuid not null references vacancies(id) on delete cascade,
    scheduled_at    timestamptz not null,
    end_at          timestamptz,
    meet_link       text not null default '',
    event_id        text not null default '',     -- id del evento en Google Calendar
    sheet_row       text not null default '',      -- referencia de la fila escrita en Sheets
    candidate_email text not null default '',
    recruiter_email text not null default '',
    status          text not null default 'scheduled',  -- scheduled | cancelled
    created_at      timestamptz not null default now(),
    unique (conversation_id)
);

create index if not exists idx_meetings_candidate on meetings(candidate_id);

grant all privileges on meetings to service_role;

-- ── Configuración de agendamiento (editable desde Configuración) ─────────────────
insert into app_settings (key, value)
values ('scheduling', '{"enabled": true, "provider": "simulated", "slot_minutes": 45,
  "work_days": [1, 2, 3, 4, 5], "work_start": "09:00", "work_end": "18:00",
  "timezone": "America/Lima", "horizon_days": 7, "options": 3}'::jsonb)
on conflict (key) do nothing;

-- ── Reclutador demo (Grace Mendieta / SIFRAH) asignado a la vacante de ejemplo ───
insert into recruiters (name, email, company, role, calendar_id)
select 'Grace Mendieta', '', 'SIFRAH', 'Analista de Atracción de Talento', 'primary'
where not exists (select 1 from recruiters where name = 'Grace Mendieta');

update vacancies set recruiter_id = (select id from recruiters where name = 'Grace Mendieta' limit 1)
where title = 'Analista de Automatizaciones e IA' and recruiter_id is null;
