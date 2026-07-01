-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Fase 2 — Integridad y cumplimiento.                                          ║
-- ║  • scorecards.review_required: evaluación de baja confianza → revisión humana ║
-- ║  • audit_log: quién/qué/cuándo de las acciones del dashboard.                 ║
-- ║  • candidates.consent_at + app_settings.retention: consentimiento y retención ║
-- ║    de datos (Ley 29733 Perú / GDPR).                                          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- Revisión humana requerida (alguna respuesta se evaluó con baja confianza).
alter table scorecards add column if not exists review_required boolean not null default false;

-- ── Auditoría de acciones del dashboard ─────────────────────────────────────────
create table if not exists audit_log (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      uuid,
    actor_user_id  uuid,
    actor_email    text not null default '',
    action         text not null,               -- candidate.decide | vacancy.create | settings.update | ...
    entity_type    text not null default '',    -- candidate | vacancy | recruiter | settings
    entity_id      text not null default '',
    summary        text not null default '',
    created_at     timestamptz not null default now()
);

create index if not exists idx_audit_tenant on audit_log(tenant_id, created_at desc);
grant all privileges on audit_log to service_role;

-- ── Consentimiento del candidato (momento de aceptación) ────────────────────────
alter table candidates add column if not exists consent_at timestamptz;

-- ── Retención de datos: config editable (desactivada por defecto) ────────────────
-- Al activarse, el scheduler anonimiza la PII de candidatos descartados con más de
-- `days` días (nombre, chat, CV, documentos + transcripción). Default OFF para no
-- borrar datos de demo por accidente.
insert into app_settings (key, value)
values ('retention', '{"enabled": false, "days": 180}'::jsonb)
on conflict (key) do nothing;
