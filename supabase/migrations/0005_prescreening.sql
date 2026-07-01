-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Pre-filtro automático de postulantes (sourcing + CV gate) + métricas        ║
-- ║ Añade el perfil del CV, el resultado del pre-screen y los documentos al      ║
-- ║ candidato; el mapeo pregunta↔campo del CV; y una tabla de uso de tokens.     ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Candidatos: origen, perfil del CV, pre-screen y documentos ─────────────────
alter table candidates
    add column if not exists source       text  not null default 'telegram',  -- bumeran | linkedin | telegram
    -- Perfil parseado del CV (lo trae el conector de sourcing):
    --   {name, headline, education:{level,career}, years_experience, skills[],
    --    location, salary_expectation, raw_cv_text}
    add column if not exists cv_profile    jsonb not null default '{}'::jsonb,
    -- Resultado del pre-filtro automático del CV:
    --   {pre_score, verdict (pass|borderline|reject), summary,
    --    per_requirement:[{requirement, met, note}]}
    add column if not exists prescreen     jsonb not null default '{}'::jsonb,
    -- Documentos recibidos del candidato (CUL real por Telegram, CV de plataforma):
    --   [{type:'cul'|'cv', filename, file_id, received_at}]
    add column if not exists documents     jsonb not null default '[]'::jsonb;

-- Los postulantes importados aún no tienen chat real; el conector usa el id de
-- plataforma como channel_user_id, así que dejamos de exigirlo no vacío (ya era text).
-- (La unicidad (vacancy_id, channel, channel_user_id) se mantiene.)

-- ── Preguntas: a qué campo del CV revalida cada una ────────────────────────────
-- null = pregunta "fría" (se pregunta siempre); si trae un campo y el CV lo tiene,
-- el motor la reformula como confirmación/profundización.
alter table vacancy_questions
    add column if not exists cv_field text;   -- education|years_experience|location|skills|salary_expectation

-- ── Métricas: uso de tokens del LLM por etapa ──────────────────────────────────
create table if not exists llm_usage (
    id              uuid primary key default gen_random_uuid(),
    vacancy_id      uuid references vacancies(id)  on delete cascade,
    candidate_id    uuid references candidates(id) on delete cascade,
    conversation_id uuid references conversations(id) on delete cascade,
    stage           text not null,                  -- prescreen|classify|evaluate|scorecard|revalidate|answer
    model           text not null default '',
    input_tokens    int  not null default 0,
    output_tokens   int  not null default 0,
    total_tokens    int  not null default 0,
    created_at      timestamptz not null default now()
);

create index if not exists idx_llm_usage_vacancy on llm_usage(vacancy_id);
create index if not exists idx_llm_usage_created  on llm_usage(created_at);
