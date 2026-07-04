# `ranking/` — Re-ranking

Componente **Ranking** de la rúbrica: prioriza los pasajes recuperados por `retrieval/` antes de pasarlos
al LLM, mejorando la relevancia del contexto (menos ruido → respuestas mejor fundamentadas).

| Archivo | Rol |
|---|---|
| `reranker.py` | `CrossEncoderReranker` (cross-encoder liviano) + `SemanticReranker` + `RerankConfig`. |

Se usa en el camino vivo del RAG (`retrieval/rag.py`): sobre-muestreo híbrido → re-ranker → top-k final.
Config-gated por `RERANKER`/`CROSS_ENCODER_MODEL` (`core/config.py`); si el modelo no está disponible,
`retrieval/rag.py` degrada a orden vectorial sin romper.

> Nota: el *scoring* de candidatos (priorizar personas contra los criterios de la vacante) vive en
> `evaluation/scorer.py` + `evaluation/scorecard.py` — es re-ranking de personas, no de documentos.
