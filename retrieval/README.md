# `retrieval/` — Recuperación (BD vectorial + búsqueda híbrida)

Componente **Retrieval** de la rúbrica: base de conocimiento de la empresa para responder dudas del
candidato sobre el puesto, fundamentadas en documentos reales (RAG).

| Archivo | Rol |
|---|---|
| `vectorstore.py` | Chroma (persistente) + indexación idempotente por hash + búsqueda híbrida **BM25 + vectorial**. |
| `embeddings.py` | Embeddings `intfloat/multilingual-e5-base` (multilingüe, apto español). |
| `semantic_cache.py` | Caché semántica por coseno (`best_match`) reutilizada por RAG y por la caché de dudas. |
| `rag.py` | Pipeline vivo: híbrido → dedupe → re-ranker (`ranking/`) → top-k; degradación en capas. |
| `answer_cache.py` | Caché de respuestas a dudas por vacante (hit ⇒ 0 tokens, sin RAG ni LLM). |

Seed de la base: `scripts/seed_company_kb.py` (colección `company_kb`). Config-gated por
`INTERVIEW_RAG_ENABLED` (default on). Depende de `ranking/` (re-ranker) y `core/` (config/logging).

**Cómo ejecutar:** `uv run python scripts/seed_company_kb.py` (indexa las vacantes abiertas) y luego el
retriever se inyecta en el runner del agente automáticamente.
