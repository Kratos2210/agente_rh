-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Fase 0 — Cimientos SaaS: multi-tenancy + autenticación (usuarios/roles).     ║
-- ║                                                                             ║
-- ║ • tenants: cada empresa cliente del SaaS (aislamiento de datos).            ║
-- ║ • users: cuentas del dashboard (email + hash bcrypt + rol), atadas a tenant.║
-- ║ • tenant_id en vacancies/recruiters: raíz del aislamiento; los hijos        ║
-- ║   (candidates, conversations, …) heredan el tenant vía su vacante.          ║
-- ║ El aislamiento se hace a nivel de app/API (el service_role saltea RLS).     ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Tenants (empresas cliente) ──────────────────────────────────────────────────
create table if not exists tenants (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    slug        text not null,                  -- identificador legible/único
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    unique (slug)
);

grant all privileges on tenants to service_role;

-- Tenant por defecto: agrupa todos los datos que ya existían antes de la Fase 0.
insert into tenants (name, slug)
values ('Empresa demo', 'default')
on conflict (slug) do nothing;

-- ── Usuarios del dashboard ──────────────────────────────────────────────────────
-- role: admin | recruiter | viewer. La contraseña se guarda como hash bcrypt
-- (nunca en claro). El admin inicial lo crea el backend al arrancar si no hay ninguno.
create table if not exists users (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    email           text not null,
    password_hash   text not null,
    name            text not null default '',
    role            text not null default 'recruiter',   -- admin | recruiter | viewer
    active          boolean not null default true,
    created_at      timestamptz not null default now(),
    unique (email)
);

create index if not exists idx_users_tenant on users(tenant_id);
grant all privileges on users to service_role;

-- ── tenant_id en las tablas raíz (aislamiento) ──────────────────────────────────
alter table vacancies  add column if not exists tenant_id uuid references tenants(id) on delete cascade;
alter table recruiters add column if not exists tenant_id uuid references tenants(id) on delete cascade;

-- Backfill: todo lo preexistente pasa al tenant por defecto.
update vacancies  set tenant_id = (select id from tenants where slug = 'default') where tenant_id is null;
update recruiters set tenant_id = (select id from tenants where slug = 'default') where tenant_id is null;

create index if not exists idx_vacancies_tenant  on vacancies(tenant_id);
create index if not exists idx_recruiters_tenant on recruiters(tenant_id);

-- ── Índice de apoyo al scheduler (audit #9): filtra candidatos por estado ────────
create index if not exists idx_candidates_status on candidates(status);
