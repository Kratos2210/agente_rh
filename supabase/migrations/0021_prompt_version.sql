-- 0021 — Versionado de prompts (auditoría e2e · pipeline LLM).
-- Cambiar EVALUATE_ANSWER_PROMPT/SCORECARD_PROMPT deja scorecards no comparables si no
-- queda registrado con qué versión se evaluó. Se sella agent/prompts.PROMPT_VERSION en
-- cada scorecard y en cada fila de llm_usage. El código es retro-compatible: sin estas
-- columnas, save_scorecard/record_usage reintentan sin el campo.

alter table scorecards add column if not exists prompt_version text not null default '';
alter table llm_usage  add column if not exists prompt_version text not null default '';
