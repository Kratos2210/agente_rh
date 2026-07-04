"""Caché semántico de respuestas (ahorro de tokens).

Antes de gastar el prompt RAG completo, busca si una pregunta MUY parecida ya fue
respondida para este documento. Si la encuentra (similitud coseno por encima de un
umbral conservador), devuelve la respuesta cacheada sin llamar al LLM ni recuperar
contexto → 0 tokens.

El umbral es alto a propósito (default 0.95): preferimos un miss y regenerar antes
que servir una respuesta de una pregunta que solo se *parece*. Los embeddings ya
vienen L2-normalizados (`get_embeddings`, `normalize_embeddings=True`), así que el
coseno es simplemente el producto punto.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np

from core import registry
from core.logging_config import get_logger

logger = get_logger(__name__)

_DTYPE = np.float32


def embed_question(embeddings, question: str) -> np.ndarray:
    """Embebe la pregunta y la devuelve como vector float32 (L2-normalizado)."""
    vec = np.asarray(embeddings.embed_query(question), dtype=_DTYPE)
    return vec


def best_match(query: np.ndarray, rows: list[dict[str, Any]], threshold: float) -> Optional[dict[str, Any]]:
    """Fila con mayor coseno (dot, vectores ya L2-normalizados) por encima del umbral, o
    None. Puro (sin DB): lo reusan el caché de RAG y el de dudas de la entrevista (paso 5)."""
    best: Optional[dict[str, Any]] = None
    best_score = -1.0
    for row in rows:
        cached = np.frombuffer(row["embedding"], dtype=_DTYPE)
        if cached.shape != query.shape:
            continue
        score = float(np.dot(query, cached))
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score < threshold:
        return None
    return {**best, "score": best_score}


def lookup(
    document_id: str,
    question: str,
    embeddings,
    threshold: float,
) -> Optional[dict[str, Any]]:
    """Devuelve {answer, sources, question, score} si hay un hit por encima del
    umbral, o None. Falla en abierto (ante error, None → flujo normal)."""
    try:
        rows = registry.list_response_cache(document_id)
        if not rows:
            return None
        query = embed_question(embeddings, question)
        best = best_match(query, rows, threshold)
        if best is None:
            return None
        logger.info(
            "Caché semántico HIT (doc=%s, score=%.4f) — 0 tokens", document_id, best["score"]
        )
        return {
            "answer": best["answer"],
            "sources": json.loads(best["sources"]) if best.get("sources") else [],
            "question": best["question"],
            "score": best["score"],
        }
    except Exception:
        logger.warning("Caché semántico lookup falló; sigo con el flujo normal", exc_info=True)
        return None


def store(
    document_id: str,
    question: str,
    embeddings,
    answer: str,
    sources: list[dict[str, Any]],
) -> None:
    """Guarda una respuesta recién generada. Falla en abierto (no romper el chat)."""
    try:
        if not answer.strip():
            return
        vec = embed_question(embeddings, question)
        registry.add_response_cache(
            document_id,
            question,
            vec.tobytes(),
            answer,
            json.dumps(sources, ensure_ascii=False),
        )
    except Exception:
        logger.warning("Caché semántico store falló (ignorable)", exc_info=True)
