-- Observabilidad del pipeline LLM (auditoría O1): latencia, llamadas y errores por etapa.
-- `errors` cuenta las excepciones del proveedor (el caller degrada al fallback heurístico):
-- es el indicador de "% de evaluaciones que cayó al fallback", antes invisible.

alter table llm_usage add column if not exists calls       int    not null default 0;
alter table llm_usage add column if not exists errors      int    not null default 0;
alter table llm_usage add column if not exists duration_ms bigint not null default 0;
