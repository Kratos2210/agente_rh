-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ F2 (auditoría de integraciones) — RLS por tenant (defensa en profundidad).  ║
-- ║                                                                             ║
-- ║ Activa Row Level Security + políticas de aislamiento por `tenant_id` en     ║
-- ║ TODAS las tablas de negocio. El tenant "actual" se lee del claim `tenant_id`║
-- ║ del JWT de PostgREST (`request.jwt.claims`).                                ║
-- ║                                                                             ║
-- ║ ⚠️  RLS LATENTE: el backend usa la **service_role key**, cuyo rol tiene       ║
-- ║     BYPASSRLS → estas políticas NO afectan al backend actual (sigue viendo  ║
-- ║     todo; el aislamiento operativo lo hace la capa de app: los guards        ║
-- ║     `_require_*_in_tenant` + el test `tests/test_tenant_guards.py`).          ║
-- ║                                                                             ║
-- ║     El valor de esta migración es defensa en profundidad: si mañana un       ║
-- ║     cliente accede con la anon/publishable key (o un rol no-bypass), la      ║
-- ║     propia DB niega el acceso cross-tenant aunque un guard de app falle.     ║
-- ║     Para que RLS aplique al backend habría que dejar de usar service_role    ║
-- ║     y setear `request.jwt.claims.tenant_id` por request (cambio mayor).      ║
-- ║                                                                             ║
-- ║ No se tocan las tablas del checkpointer de LangGraph (checkpoints*): las     ║
-- ║ maneja PostgresSaver por conexión directa (DATABASE_URL), no por PostgREST.  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Helpers: resuelven el tenant "actual" (del JWT) y el de cada recurso ─────────

-- Tenant del token PostgREST. NULL si no hay claim (rol anónimo sin sesión).
-- El `nullif(..., '')` interno es clave: `current_setting(..., true)` devuelve NULL si
-- el GUC no está seteado, pero PostgREST lo setea a la CADENA VACÍA para requests sin
-- JWT (anon); sin este guard, `''::jsonb` lanzaría "invalid input syntax for type json"
-- y tumbaría toda consulta anónima en vez de resolver "sin tenant" → NULL.
create or replace function app_current_tenant() returns uuid
    language sql stable
    as $$
    select nullif(
        nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'tenant_id',
        ''
    )::uuid
$$;

-- Tenant de una vacante / conversación / candidato. SECURITY DEFINER para resolver
-- la jerarquía sin recursión de RLS (la política de la tabla hija no re-evalúa la
-- RLS de la tabla padre). STABLE + search_path fijo (buenas prácticas de seguridad).
create or replace function app_vacancy_tenant(vid uuid) returns uuid
    language sql stable security definer set search_path = public
    as $$ select tenant_id from vacancies where id = vid $$;

create or replace function app_conversation_tenant(cid uuid) returns uuid
    language sql stable security definer set search_path = public
    as $$ select app_vacancy_tenant(vacancy_id) from conversations where id = cid $$;

create or replace function app_candidate_tenant(cid uuid) returns uuid
    language sql stable security definer set search_path = public
    as $$ select app_vacancy_tenant(vacancy_id) from candidates where id = cid $$;

-- ── Activación de RLS + políticas de aislamiento ────────────────────────────────
-- Cada política aplica a los roles `anon` y `authenticated` (los del cliente
-- PostgREST). `service_role` (el backend) las saltea por su atributo BYPASSRLS.
-- FOR ALL con USING (lectura/borrado/update) + WITH CHECK (insert/update): tanto lo
-- que se lee como lo que se escribe debe pertenecer al tenant del token.

do $$
declare
    -- Tablas con `tenant_id` directo → predicado por columna.
    direct  text[] := array['vacancies','recruiters','outbox','audit_log','app_settings','users'];
    -- Tablas hijas → predicado vía función que sube al tenant de la vacante/conv/cand.
    -- Formato: 'tabla=expresion_del_tenant'.
    derived text[] := array[
        'vacancy_questions=app_vacancy_tenant(vacancy_questions.vacancy_id)',
        'candidates=app_vacancy_tenant(candidates.vacancy_id)',
        'conversations=app_vacancy_tenant(conversations.vacancy_id)',
        'meetings=app_vacancy_tenant(meetings.vacancy_id)',
        'llm_usage=app_vacancy_tenant(llm_usage.vacancy_id)',
        'messages=app_conversation_tenant(messages.conversation_id)',
        'answers=app_conversation_tenant(answers.conversation_id)',
        'scorecards=app_conversation_tenant(scorecards.conversation_id)',
        'candidate_documents=app_candidate_tenant(candidate_documents.candidate_id)'
    ];
    t     text;
    tbl   text;
    expr  text;
begin
    -- Tablas con tenant_id directo.
    foreach t in array direct loop
        execute format('alter table %I enable row level security', t);
        execute format('drop policy if exists tenant_isolation on %I', t);
        execute format(
            'create policy tenant_isolation on %I for all to anon, authenticated '
            'using (tenant_id = app_current_tenant()) '
            'with check (tenant_id = app_current_tenant())', t);
    end loop;

    -- La tabla `tenants` se aísla por su propia PK (id = tenant actual).
    alter table tenants enable row level security;
    drop policy if exists tenant_isolation on tenants;
    create policy tenant_isolation on tenants for all to anon, authenticated
        using (id = app_current_tenant())
        with check (id = app_current_tenant());

    -- Tablas hijas (tenant derivado por la jerarquía).
    foreach t in array derived loop
        tbl  := split_part(t, '=', 1);
        expr := split_part(t, '=', 2);
        execute format('alter table %I enable row level security', tbl);
        execute format('drop policy if exists tenant_isolation on %I', tbl);
        execute format(
            'create policy tenant_isolation on %I for all to anon, authenticated '
            'using (%s = app_current_tenant()) '
            'with check (%s = app_current_tenant())', tbl, expr, expr);
    end loop;
end $$;
