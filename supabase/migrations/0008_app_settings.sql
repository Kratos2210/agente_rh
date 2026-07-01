-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Configuración de la app editable desde el dashboard (key/value).             ║
-- ║ Primer uso: auto-contacto programado de candidatos aptos.                   ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

create table if not exists app_settings (
    key        text primary key,
    value      jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

-- Default: auto-contacto apagado, a las 11:00 y 15:00 (America/Lima).
insert into app_settings (key, value)
values ('auto_contact', '{"enabled": false, "times": ["11:00", "15:00"], "timezone": "America/Lima"}'::jsonb)
on conflict (key) do nothing;
