-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Cierre del backlog de la auditoría e2e (S4 · D3 · D5 · G3 · G4).            ║
-- ║                                                                             ║
-- ║  S4  outbox.candidate_id con FK ON DELETE CASCADE: el erasure (derecho al   ║
-- ║      olvido) elimina también los envíos encolados con PII en el payload.    ║
-- ║  D3  RPCs atómicos: reemplazo de preguntas y claim de chat dejan de ser     ║
-- ║      secuencias multi-request (un fallo a mitad ya no deja estado roto).    ║
-- ║  D5  candidates.updated_at (trigger): la retención mide la antigüedad por    ║
-- ║      la última actividad del registro, no por la fecha de alta.             ║
-- ║  G3  conversations.last_delivery_failed_at: marca los envíos de Telegram    ║
-- ║      que fallaron (la transcripción ya no afirma entregas que no pasaron).  ║
-- ║  G4  state_transitions: bitácora de transiciones de fase con timestamp      ║
-- ║      (tiempo-por-estado + reconstrucción formal del flujo).                 ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── S4: FK cascade del outbox al candidato ───────────────────────────────────────
-- Filas históricas cuyo candidato ya fue borrado: se desvinculan antes de la FK.
update outbox o
   set candidate_id = null
 where candidate_id is not null
   and not exists (select 1 from candidates c where c.id = o.candidate_id);

alter table outbox drop constraint if exists outbox_candidate_id_fkey;
alter table outbox
    add constraint outbox_candidate_id_fkey
    foreign key (candidate_id) references candidates(id) on delete cascade;

-- ── D5: última modificación del candidato (para la retención) ────────────────────
alter table candidates add column if not exists updated_at timestamptz not null default now();

create or replace function app_touch_updated_at() returns trigger
    language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end $$;

drop trigger if exists candidates_touch_updated on candidates;
create trigger candidates_touch_updated
    before update on candidates
    for each row execute function app_touch_updated_at();

-- ── G3: marca de entrega fallida por Telegram ────────────────────────────────────
alter table conversations add column if not exists last_delivery_failed_at timestamptz;

-- ── G4: bitácora de transiciones de fase ─────────────────────────────────────────
create table if not exists state_transitions (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    from_state      text not null default '',
    to_state        text not null,
    created_at      timestamptz not null default now()
);

create index if not exists idx_state_transitions_conv
    on state_transitions(conversation_id, created_at);

grant all privileges on state_transitions to service_role;

-- RLS por tenant (mismo patrón de 0018: sube al tenant vía la conversación).
alter table state_transitions enable row level security;
drop policy if exists tenant_isolation on state_transitions;
create policy tenant_isolation on state_transitions for all to anon, authenticated
    using (app_conversation_tenant(state_transitions.conversation_id) = app_current_tenant())
    with check (app_conversation_tenant(state_transitions.conversation_id) = app_current_tenant());

-- ── D3: RPCs atómicos (una transacción por llamada de PostgREST) ─────────────────

-- Reemplaza el set de preguntas de una vacante en una sola transacción (antes:
-- delete + insert en dos requests; un fallo a mitad dejaba la vacante sin preguntas).
create or replace function app_replace_vacancy_questions(vid uuid, qs jsonb) returns void
    language plpgsql security definer set search_path = public as $$
begin
    delete from vacancy_questions where vacancy_id = vid;
    insert into vacancy_questions
        (vacancy_id, position, text, criterion, weight, max_follow_ups, cv_field, label)
    select vid,
           coalesce((q->>'position')::int, 0),
           coalesce(q->>'text', ''),
           coalesce(q->>'criterion', ''),
           coalesce((q->>'weight')::numeric, 1.0),
           coalesce((q->>'max_follow_ups')::int, 1),
           q->>'cv_field',
           coalesce(q->>'label', '')
      from jsonb_array_elements(coalesce(qs, '[]'::jsonb)) as q;
end $$;

-- Reasigna un chat al candidato `target` en una sola transacción: libera a cualquier
-- otro candidato de la vacante que tuviera ese chat, purga la conversación del thread
-- (cascade: mensajes/respuestas/scorecards) y asigna el chat al target. El checkpoint
-- de LangGraph se borra aparte (otra capa de almacenamiento, conexión directa).
create or replace function app_claim_candidate_chat(
    target uuid, vid uuid, chan text, chat text, thread text
) returns void
    language plpgsql security definer set search_path = public as $$
begin
    update candidates
       set channel_user_id = 'freed-' || left(id::text, 8)
     where vacancy_id = vid and channel = chan and channel_user_id = chat and id <> target;
    delete from conversations where langgraph_thread_id = thread;
    update candidates set channel_user_id = chat where id = target;
end $$;

grant execute on function app_replace_vacancy_questions(uuid, jsonb) to service_role;
grant execute on function app_claim_candidate_chat(uuid, uuid, text, text, text) to service_role;
