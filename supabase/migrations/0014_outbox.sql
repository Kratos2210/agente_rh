-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Fase 1 — No perder candidatos: OUTBOX durable de envíos salientes.           ║
-- ║                                                                             ║
-- ║ Cada notificación externa (email al reclutador, correo/telegram de la        ║
-- ║ reunión, aviso al candidato) se intenta en línea; si falla, queda encolada   ║
-- ║ aquí y el scheduler la reintenta con backoff exponencial hasta agotarse      ║
-- ║ (dead-letter). Así un fallo transitorio de SMTP/Telegram no pierde el aviso. ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

create table if not exists outbox (
    id              uuid primary key default gen_random_uuid(),
    kind            text not null,                 -- scorecard_email | meeting_email | meeting_recruiter_telegram | candidate_notify
    payload         jsonb not null default '{}'::jsonb,
    status          text not null default 'pending',   -- pending | sent | failed (dead-letter)
    attempts        int  not null default 0,
    max_attempts    int  not null default 6,
    next_attempt_at timestamptz not null default now(),
    last_error      text not null default '',
    -- Trazabilidad / aislamiento (para diagnósticos y futura vista por tenant).
    tenant_id       uuid,
    candidate_id    uuid,
    conversation_id uuid,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- El drenaje busca lo pendiente y vencido: índice por (status, next_attempt_at).
create index if not exists idx_outbox_due on outbox(status, next_attempt_at);
create index if not exists idx_outbox_candidate on outbox(candidate_id);

grant all privileges on outbox to service_role;
