-- 0023: identidad estable del postulante sourced (fix de idempotencia del re-sync).
--
-- El dedupe del sync era por (vacancy_id, channel, channel_user_id), pero al contactar
-- en modo demo el candidato se reasigna al chat real (DEMO_TELEGRAM_CHAT_ID) y pierde
-- su id de plataforma como channel_user_id → el siguiente sync no lo encontraba y lo
-- DUPLICABA (robándole el chat al anterior). `source_ref` conserva el id de plataforma
-- desde la creación y nunca lo muta el claim de chat.

alter table candidates add column if not exists source_ref text not null default '';

create index if not exists idx_candidates_source_ref
  on candidates (vacancy_id, source_ref)
  where source_ref <> '';

-- Backfill: candidatos sourced que aún conservan su id de plataforma en channel_user_id
-- (los ya reasignados a un chat real o liberados no tienen forma de recuperarlo aquí).
update candidates
   set source_ref = channel_user_id
 where source_ref = ''
   and source <> 'telegram'
   and channel_user_id !~ '^-?[0-9]+$'
   and channel_user_id not like 'freed-%';
