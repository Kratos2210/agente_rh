"""Caché semántica de las dudas del candidato (paso 5 — optimización de costos).

Cablea la lógica de matching semántico dormida (`src/semantic_cache`) en la etapa `answer`:
antes de gastar el prompt (LLM + RAG) para responder una duda sobre el puesto, busca si una
pregunta MUY parecida ya fue respondida para la MISMA vacante. Si hay hit (coseno >= umbral),
devuelve la respuesta cacheada → 0 tokens. Las respuestas por vacante son estables (mismo
company_info + misma base de conocimiento), así que el hit ENTRE candidatos es seguro y es la
mayor palanca de ahorro (las dudas de candidatos son repetitivas: sueldo, horario, beneficios).

Diseño (mismo patrón que `agent/rag.py`): un objeto inyectado en el runner, con carga LAZY de
embeddings (torch ~90 s en Mac Intel) en el primer uso, y fail-open (cualquier fallo degrada a
generar normalmente). Almacén propio SQLite (por `vacancy_id`), SIN la relación con el registry
de agente_pro (cuya `response_cache` exige un `documents.id` que aquí no existe).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from threading import Lock
from typing import Optional

from core.config import Settings
from core.logging_config import get_logger

logger = get_logger("retrieval.answer_cache")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_cache (
    id          TEXT PRIMARY KEY,
    vacancy_id  TEXT NOT NULL,
    question    TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    answer      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answer_cache_vacancy ON answer_cache(vacancy_id);
"""


class AnswerCache:
    """Caché semántica por vacante. `lookup`/`store` fallan en abierto (nunca rompen el turno)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = Path(getattr(settings, "interview_answer_cache_db", "./answer_cache.db"))
        self._threshold = float(getattr(settings, "semantic_cache_threshold", 0.95))
        self._embeddings = None
        self._lock = Lock()
        self._ready = False
        self._failed = False

    def _ensure(self) -> None:
        if self._ready:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path, timeout=30) as conn:
            conn.executescript(_SCHEMA)
        from retrieval import embeddings as emb

        self._embeddings = emb.get_embeddings(self._settings.embedding_model)
        self._ready = True

    def lookup(self, vacancy_id: str, question: str) -> Optional[str]:
        """Respuesta cacheada de una pregunta parecida para la vacante, o None (miss)."""
        if self._failed or not vacancy_id or not (question or "").strip():
            return None
        try:
            with self._lock:
                self._ensure()
            from retrieval.semantic_cache import best_match, embed_question

            with sqlite3.connect(self._db_path, timeout=30) as conn:
                conn.row_factory = sqlite3.Row
                rows = [
                    {"embedding": r["embedding"], "answer": r["answer"], "question": r["question"]}
                    for r in conn.execute(
                        "SELECT question, embedding, answer FROM answer_cache WHERE vacancy_id = ?",
                        (vacancy_id,),
                    )
                ]
            if not rows:
                return None
            hit = best_match(embed_question(self._embeddings, question), rows, self._threshold)
            if hit is None:
                return None
            logger.info("Caché de dudas HIT (vacante=%s, score=%.4f) — 0 tokens", vacancy_id, hit["score"])
            return hit["answer"]
        except Exception:  # noqa: BLE001 — fail-open: sin caché, se genera normalmente
            self._failed = True
            logger.warning("Caché de dudas no disponible; se responde generando", exc_info=True)
            return None

    def store(self, vacancy_id: str, question: str, answer: str) -> None:
        """Guarda una respuesta recién generada (best-effort, no rompe el turno)."""
        if self._failed or not vacancy_id or not (answer or "").strip():
            return
        try:
            with self._lock:
                self._ensure()
            from retrieval.semantic_cache import embed_question

            vec = embed_question(self._embeddings, question).tobytes()
            with sqlite3.connect(self._db_path, timeout=30) as conn:
                conn.execute(
                    "INSERT INTO answer_cache (id, vacancy_id, question, embedding, answer) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, vacancy_id, question, vec, answer),
                )
        except Exception:  # noqa: BLE001
            logger.warning("Caché de dudas: store falló (ignorable)", exc_info=True)


def build_answer_cache(settings: Settings) -> Optional[AnswerCache]:
    """Caché de dudas, o None si está desactivada (`interview_answer_cache_enabled`)."""
    if not getattr(settings, "interview_answer_cache_enabled", False):
        return None
    return AnswerCache(settings)
