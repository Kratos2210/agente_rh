-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Fase 0.1 — Settings POR-TENANT.                                              ║
-- ║                                                                             ║
-- ║ Hasta ahora `app_settings` era global (key/value, una sola fila por clave): ║
-- ║ auto_contact / inactivity / scheduling / retention aplicaban a todo el       ║
-- ║ proceso. En un SaaS multi-empresa cada tenant debe configurar lo suyo. Se    ║
-- ║ añade `tenant_id` y la clave pasa a ser compuesta (tenant_id, key). Las      ║
-- ║ filas existentes (globales) se adjudican al tenant `default` (el mismo al    ║
-- ║ que 0013 adjudicó vacantes/reclutadores). Un tenant sin fila cae a los        ║
-- ║ defaults del código (_DEFAULT_* en api/main.py).                             ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

alter table app_settings
    add column if not exists tenant_id uuid references tenants(id) on delete cascade;

-- Backfill: las settings globales previas pasan a ser del tenant `default`.
update app_settings
set tenant_id = (select id from tenants where slug = 'default' limit 1)
where tenant_id is null;

alter table app_settings alter column tenant_id set not null;

-- La clave pasa de `key` a la compuesta (tenant_id, key).
alter table app_settings drop constraint app_settings_pkey;
alter table app_settings add primary key (tenant_id, key);

grant all privileges on app_settings to service_role;
