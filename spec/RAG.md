# RAG — Base de conocimiento de la empresa

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `retrieval/`, `ranking/`, `agente/rag.py`

## 1. Propósito y alcance

El agente responde dudas del candidato sobre el puesto ("¿cuál es el rango salarial?",
"¿es remoto?") citando la base de conocimiento de la empresa, no inventando. Cubre indexación,
recuperación híbrida, re-ranking, degradación y evaluación de retrieval. Heredado del motor RAG
de `agente_pro` y adaptado a corpus por-vacante.

## 2. Decisiones de diseño

- **Todo local**: Chroma persistido en disco (`PERSIST_DIRECTORY`) + embeddings HuggingFace
  (`intfloat/multilingual-e5-base`) + cross-encoder liviano. Los datos de la empresa y la PII
  no salen de la infraestructura propia (Ley 29733).
- **Híbrido BM25 + vectorial, luego re-rank**: la búsqueda vectorial sola ordenaba mal casos
  con keywords exactas (verificado en vivo: la pregunta de salario traía "Funciones" primero);
  BM25 sobre el corpus + sobre-muestreo vectorial (`RETRIEVE_K=10`) → dedupe →
  `CrossEncoderReranker` → top `FINAL_K=6`.
- **Corpus = un documento compuesto por vacante abierta** (título/área/modalidad/ubicación/
  descripción/company_info/detalles/requisitos/beneficios/rango salarial/temas), indexado
  idempotente por hash en la colección `COMPANY_KB_COLLECTION=company_kb`. Simple de regenerar
  y de razonar; los chunks llevan `source` = id de vacante (purga selectiva).
- **Apertura en modo lectura en runtime**: `agente/rag.py` abre la colección persistida SIN
  reindexar (la variante que reindexaba exigía PDFs en `data/` y degradaba siempre). El
  reindex es un proceso aparte.
- **Reindex vía outbox, nunca en el request**: crear/editar vacante encola el kind `kb_reindex`
  (sin intento en línea — torch jamás corre en el request path); el drain del scheduler lo
  procesa (`retrieval/company_kb.py::reindex_vacancy`, purga chunks previos de esa vacante).
- **Degradación en capas**: sin re-ranker → híbrido; sin BM25 → vectorial; sin colección →
  `company_info` plano de la vacante; sin embeddings → idem. La duda SIEMPRE se responde.

## 3. Diseño e implementación

- `retrieval/vectorstore.py` + `retrieval/embeddings.py`: Chroma + e5 (carga lazy: torch tarda
  ~90 s en Mac Intel; el fail-safe corta antes del import pesado).
- `agente/rag.py::build_company_retriever`: pipeline híbrido (BM25 materializado con
  `collection.get()` + vectorial) → dedupe → re-rank (`ranking/reranker.py`, config `RERANKER=cross`
  + `CROSS_ENCODER_MODEL` liviano) → contexto para `ANSWER_CANDIDATE_PROMPT`. Inyectado al motor
  como dependencia (fake en tests). Flag `INTERVIEW_RAG_ENABLED=true` (default on).
- Seed manual: `uv run python scripts/seed_company_kb.py`. Reindex automático: hook en
  create/update de vacante → outbox.
- **Caché de respuestas** (capa previa al RAG): ver [Orquestacion.md](Orquestacion.md) — hit
  semántico por vacante devuelve la respuesta con 0 tokens.
- Anti-alucinación: el prompt prohíbe confirmar salario/condiciones fuera de `company_info`/
  contexto recuperado; deriva al equipo si no sabe. El juez de groundedness audita esto en
  producción (ver [Evaluacion.md](Evaluacion.md)).

### Parámetros (bloque RAG del `.env`)

`CHUNK_SIZE=1600` / `CHUNK_OVERLAP=200` / `RETRIEVE_K=10` / `FINAL_K=6` /
`EMBEDDING_MODEL=intfloat/multilingual-e5-base` /
`CROSS_ENCODER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.

## 4. Contratos e invariantes

- La respuesta a una duda solo puede afirmar lo que está en el contexto recuperado o en
  `company_info`; todo lo demás se deriva ("el equipo te confirmará…").
- El reindex es **idempotente y selectivo**: reindexar la vacante V purga solo los chunks
  `source=V` antes de escribir.
- Ninguna ruta de RAG puede tumbar un turno: toda capa tiene fallback y try/except con log.
- El retriever se inyecta: los tests del motor no tocan Chroma ni torch.

## 5. Patrones reutilizables

- **Híbrido + re-rank como default** para corpus chicos con vocabulario exacto (precios,
  siglas, nombres): lo vectorial puro pierde justo esas queries.
- **Separar indexación (batch, pesada) de recuperación (runtime, lectura)** con una cola
  durable en medio.
- **Degradación en capas declarada**: cada dependencia opcional tiene su fallback definido y
  testeado; el sistema pierde calidad, no disponibilidad.
- **Golden de retrieval (hit@k) sin LLM**: pares pregunta→substring esperado corriendo el
  retriever real (`scripts/retrieval_eval.py`, exit 1 bajo `min_hit_rate`) — barato, offline,
  detecta regresiones de chunking/embedding.

## 6. Pendientes conocidos

- Ampliar el golden de retrieval (hoy 6 casos de la vacante demo; objetivo 20+).
- Corpus más rico que el doc por vacante (políticas de la empresa, FAQs) cuando haya fuentes.

## 7. Trazabilidad

- Tests: `test_pipeline_llm.py` (retriever + gating), `test_reranker.py`,
  `test_semantic_cache.py`, `test_kb_reindex.py`, `test_quality.py` (golden retrieval con fake).
- Sets: `tests/golden/retrieval_set.json`. Scripts: `seed_company_kb.py`, `retrieval_eval.py`.
- Verificado en vivo: chunk de rango salarial primero tras el re-ranker (antes quedaba 4.º).
