-- 0024: trazas LLM con contenido (observabilidad O-1 — replay/debug de evaluaciones).
--
-- `llm_usage` registra el AGREGADO por etapa (tokens/latencia/errores) pero no el
-- contenido: una evaluación disputada no se puede reproducir. Esta tabla guarda el
-- prompt y la respuesta cruda POR LLAMADA, config-gated (`LLM_TRACE_ENABLED`, default
-- off) y con cap de tamaño (`LLM_TRACE_MAX_CHARS`). Los prompts contienen respuestas
-- del candidato (PII): la retención y el erasure la purgan igual que documentos/outbox.

create table if not exists llm_traces (
    id              uuid primary key default gen_random_uuid(),
    vacancy_id      uuid references vacancies(id) on delete set null,
    candidate_id    uuid references candidates(id) on delete cascade,
    conversation_id uuid references conversations(id) on delete cascade,
    stage           text not null default '',
    model           text not null default '',
    prompt_version  text not null default '',
    prompt_text     text not null default '',
    response_text   text,
    error           text,
    duration_ms     integer not null default 0,
    created_at      timestamptz not null default now()
);

create index if not exists idx_llm_traces_candidate on llm_traces (candidate_id, created_at desc);
create index if not exists idx_llm_traces_conversation on llm_traces (conversation_id);

grant all privileges on llm_traces to service_role;

-- RLS por tenant (patrón 0018: latente para service_role, defensa en profundidad).
alter table llm_traces enable row level security;
drop policy if exists tenant_isolation on llm_traces;
create policy tenant_isolation on llm_traces for all to anon, authenticated
    using (app_vacancy_tenant(llm_traces.vacancy_id) = app_current_tenant())
    with check (app_vacancy_tenant(llm_traces.vacancy_id) = app_current_tenant());
