-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Fase 1 (cierre) — Almacenamiento DURABLE de documentos (CV/CUL).             ║
-- ║                                                                             ║
-- ║ Hasta ahora los PDFs vivían solo en uploads/ (disco local) y se perdían en   ║
-- ║ cada redeploy. Ahora el contenido se guarda en Postgres (base64) con FK al    ║
-- ║ candidato: sobrevive reinicios y el borrado/erasure lo elimina por cascada    ║
-- ║ (sin objetos huérfanos). Para escalar luego se puede migrar a S3/Storage.     ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

create table if not exists candidate_documents (
    id              uuid primary key default gen_random_uuid(),
    candidate_id    uuid not null references candidates(id) on delete cascade,
    conversation_id uuid references conversations(id) on delete set null,
    type            text not null default 'doc',      -- cv | cul
    filename        text not null default '',
    mime            text not null default 'application/pdf',
    size_bytes      int  not null default 0,
    content_b64     text not null default '',          -- contenido del archivo (base64)
    created_at      timestamptz not null default now(),
    unique (candidate_id, type)                        -- un CV y un CUL por candidato (reemplaza al re-subir)
);

create index if not exists idx_candidate_documents_candidate on candidate_documents(candidate_id);
grant all privileges on candidate_documents to service_role;
