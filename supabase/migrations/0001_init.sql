-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Agente de Selección de Talento — esquema de negocio (Supabase / Postgres)  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
-- Aplicar en Supabase: SQL Editor → pegar y ejecutar, o vía psql:
--   psql "$DATABASE_URL" -f supabase/migrations/0001_init.sql
--
-- El checkpointer durable de LangGraph (PostgresSaver) crea sus propias tablas
-- (checkpoints*, en el esquema public) la primera vez que corre .setup() — no se
-- declaran aquí.

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ── Vacantes ──────────────────────────────────────────────────────────────────
create table if not exists vacancies (
    id              uuid primary key default gen_random_uuid(),
    title           text not null,
    description     text not null default '',
    requirements    text not null default '',
    -- Mensaje de primer contacto (presentación del bot al candidato).
    intro_message   text not null default '',
    -- Información de la empresa/puesto para responder dudas del candidato.
    company_info    text not null default '',
    -- Umbrales del semáforo sobre el score total 0-100 (override de los .env).
    --   {"green_min": 75, "yellow_min": 50}
    semaphore_thresholds jsonb not null default '{"green_min": 75, "yellow_min": 50}'::jsonb,
    status          text not null default 'open',   -- open | closed | paused
    created_at      timestamptz not null default now()
);

-- ── Preguntas de la vacante (con su criterio de evaluación y peso) ─────────────
create table if not exists vacancy_questions (
    id              uuid primary key default gen_random_uuid(),
    vacancy_id      uuid not null references vacancies(id) on delete cascade,
    position        int  not null,                  -- orden de la pregunta (1..N)
    text            text not null,                  -- la pregunta tal como se formula
    criterion       text not null default '',       -- qué se evalúa en la respuesta
    weight          numeric not null default 1.0,   -- peso en el score ponderado
    max_follow_ups  int  not null default 1,        -- follow-ups ante respuesta vaga
    unique (vacancy_id, position)
);

-- ── Candidatos ────────────────────────────────────────────────────────────────
create table if not exists candidates (
    id              uuid primary key default gen_random_uuid(),
    vacancy_id      uuid not null references vacancies(id) on delete cascade,
    channel         text not null,                  -- telegram | whatsapp
    channel_user_id text not null,                  -- chat_id / phone
    name            text not null default '',
    -- pending | consented | declined | interviewing | finished | advanced | rejected
    status          text not null default 'pending',
    consent         boolean not null default false,
    created_at      timestamptz not null default now(),
    unique (vacancy_id, channel, channel_user_id)
);

-- ── Conversaciones (una entrevista por candidato) ─────────────────────────────
create table if not exists conversations (
    id                  uuid primary key default gen_random_uuid(),
    candidate_id        uuid not null references candidates(id) on delete cascade,
    vacancy_id          uuid not null references vacancies(id) on delete cascade,
    -- estado del flujo: greeting | interviewing | finished | closed
    state               text not null default 'greeting',
    current_question_idx int  not null default 0,
    -- thread_id que usa el checkpointer de LangGraph ("{channel}:{chat_id}")
    langgraph_thread_id text not null,
    created_at          timestamptz not null default now(),
    unique (langgraph_thread_id)
);

-- ── Mensajes (transcripción de la conversación) ───────────────────────────────
create table if not exists messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    role            text not null,                  -- user | assistant
    content         text not null,
    created_at      timestamptz not null default now()
);

-- ── Respuestas evaluadas (una por pregunta) ───────────────────────────────────
create table if not exists answers (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    question_id     uuid not null references vacancy_questions(id) on delete cascade,
    raw_answer      text not null default '',
    score           numeric,                        -- 0-100
    justification   text not null default '',
    follow_up_count int  not null default 0,
    created_at      timestamptz not null default now(),
    unique (conversation_id, question_id)
);

-- ── Scorecard final ───────────────────────────────────────────────────────────
create table if not exists scorecards (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    total_score     numeric not null default 0,     -- 0-100 (ponderado)
    semaphore       text not null,                  -- green | yellow | red
    summary         text not null default '',       -- resumen de respuestas clave
    recommendation  text not null default '',       -- avanza / no avanza + porqué
    per_criterion   jsonb not null default '[]'::jsonb,  -- [{question, criterion, score, weight, justification}]
    created_at      timestamptz not null default now(),
    unique (conversation_id)
);

-- Índices de apoyo a las consultas del dashboard.
create index if not exists idx_candidates_vacancy   on candidates(vacancy_id);
create index if not exists idx_questions_vacancy     on vacancy_questions(vacancy_id, position);
create index if not exists idx_messages_conversation on messages(conversation_id, created_at);
create index if not exists idx_answers_conversation  on answers(conversation_id);
