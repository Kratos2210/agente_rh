-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Manejo de inactividad del candidato durante la entrevista.                  ║
-- ║ El scheduler interno barre las conversaciones colgadas: recuerda y, si      ║
-- ║ sigue el silencio, las cierra como "No respondió".                          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- Última actividad del candidato en la conversación (o del último recordatorio enviado).
alter table conversations
    add column if not exists last_activity_at timestamptz not null default now();

-- Recordatorios de inactividad ya enviados en la espera actual.
alter table conversations
    add column if not exists reminders_sent int not null default 0;

-- Default: inactividad activada, recordar a los 2 min, hasta 2 recordatorios.
insert into app_settings (key, value)
values ('inactivity', '{"enabled": true, "reminder_minutes": 2, "max_reminders": 2}'::jsonb)
on conflict (key) do nothing;
