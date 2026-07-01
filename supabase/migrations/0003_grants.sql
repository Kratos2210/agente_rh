-- Permisos para el backend (rol service_role de Supabase).
-- El backend (FastAPI + supabase-py) usa la service-role key y accede vía PostgREST,
-- que exige GRANTs a nivel de tabla aunque service_role saltee RLS.
grant usage on schema public to anon, authenticated, service_role;

grant all privileges on all tables in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;

-- Tablas/seqs futuras creadas por el rol postgres heredan estos permisos.
alter default privileges in schema public
    grant all on tables to service_role;
alter default privileges in schema public
    grant all on sequences to service_role;
