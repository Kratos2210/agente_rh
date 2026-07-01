-- Campos del aviso de empleo para la vacante (rediseño UX "hira").
-- El wizard de "Nueva vacante" captura datos que antes no existían en el esquema.
-- Todos nullable / con default para no romper la vacante demo ya existente.

alter table vacancies add column if not exists area text;
alter table vacancies add column if not exists modality text default 'presencial';
alter table vacancies add column if not exists location text;
alter table vacancies add column if not exists salary_min integer;
alter table vacancies add column if not exists salary_max integer;
-- Beneficios y portales de empleo como listas JSON de strings.
alter table vacancies add column if not exists benefits jsonb not null default '[]'::jsonb;
alter table vacancies add column if not exists portals jsonb not null default '[]'::jsonb;
-- Contacto automático por vacante (hoy global vía settings.auto_contact_on_pass).
alter table vacancies add column if not exists auto_agent boolean not null default true;

-- service_role ya tiene GRANT ALL sobre las tablas (0003_grants.sql); las columnas nuevas heredan.

-- Datos reales del prototipo para la vacante demo "Analista de Automatizaciones e IA".
update vacancies set
    area = coalesce(area, 'Tecnología'),
    modality = 'presencial',
    location = coalesce(location, 'Santiago de Surco, Lima'),
    salary_min = coalesce(salary_min, 5000),
    salary_max = coalesce(salary_max, 7000),
    benefits = case when benefits = '[]'::jsonb then jsonb_build_array(
        'Planilla completa desde el primer día.',
        'EPS al 50%.',
        'Utilidades.',
        'Descuentos en la marca del 40%.'
    ) else benefits end,
    portals = case when portals = '[]'::jsonb then jsonb_build_array('bumeran', 'linkedin') else portals end
where title ilike '%Analista de Automatizaciones e IA%';
