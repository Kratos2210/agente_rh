"""Tests del caché semántico: hit/miss por similitud coseno, con embeddings falsos."""

from __future__ import annotations

import numpy as np

from src import semantic_cache


class FakeEmbeddings:
    """Embeddings deterministas para test: mapea texto → vector L2-normalizado."""

    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping

    def embed_query(self, text: str) -> list[float]:
        v = np.array(self.mapping[text], dtype=np.float32)
        return (v / np.linalg.norm(v)).tolist()


def test_store_then_exact_lookup_is_hit(temp_db, sample_doc):
    emb = FakeEmbeddings({"¿Qué es el plazo?": [1.0, 0.0, 0.0]})
    semantic_cache.store(
        sample_doc["id"], "¿Qué es el plazo?", emb, "El plazo es 12 meses.",
        [{"source": "contrato.pdf", "pagina_libro": 3}],
    )
    hit = semantic_cache.lookup(sample_doc["id"], "¿Qué es el plazo?", emb, threshold=0.95)
    assert hit is not None
    assert hit["answer"] == "El plazo es 12 meses."
    assert hit["sources"][0]["pagina_libro"] == 3
    assert hit["score"] > 0.99


def test_different_question_is_miss(temp_db, sample_doc):
    emb = FakeEmbeddings({
        "¿Qué es el plazo?": [1.0, 0.0, 0.0],
        "¿Cuál es la dirección?": [0.0, 1.0, 0.0],  # ortogonal → score 0
    })
    semantic_cache.store(sample_doc["id"], "¿Qué es el plazo?", emb, "12 meses.", [])
    assert semantic_cache.lookup(
        sample_doc["id"], "¿Cuál es la dirección?", emb, threshold=0.95
    ) is None


def test_near_duplicate_above_threshold_is_hit(temp_db, sample_doc):
    emb = FakeEmbeddings({
        "¿Qué es el plazo?": [1.0, 0.0, 0.0],
        "¿Cuál es el plazo?": [0.99, 0.14, 0.0],  # coseno ≈ 0.99 con la anterior
    })
    semantic_cache.store(sample_doc["id"], "¿Qué es el plazo?", emb, "12 meses.", [])
    hit = semantic_cache.lookup(
        sample_doc["id"], "¿Cuál es el plazo?", emb, threshold=0.95
    )
    assert hit is not None and hit["answer"] == "12 meses."


def test_threshold_is_respected(temp_db, sample_doc):
    emb = FakeEmbeddings({
        "a": [1.0, 0.0],
        "b": [0.8, 0.6],  # coseno 0.8 con "a"
    })
    semantic_cache.store(sample_doc["id"], "a", emb, "respuesta", [])
    # Umbral 0.95: 0.8 < 0.95 → miss.
    assert semantic_cache.lookup(sample_doc["id"], "b", emb, threshold=0.95) is None
    # Umbral 0.75: 0.8 >= 0.75 → hit.
    assert semantic_cache.lookup(sample_doc["id"], "b", emb, threshold=0.75) is not None


def test_empty_cache_returns_none(temp_db, sample_doc):
    emb = FakeEmbeddings({"hola": [1.0, 0.0]})
    assert semantic_cache.lookup(sample_doc["id"], "hola", emb, threshold=0.95) is None
