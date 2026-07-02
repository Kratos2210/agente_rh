"""Retriever de la base de conocimiento de la empresa para las dudas del candidato.

Conecta (config-gated) el motor RAG heredado de agente_pro (Chroma + embeddings
multilingual-e5) con `answer_candidate_question`: en vez de responder solo con el
`company_info` plano de la vacante, recupera los fragmentos más relevantes a la duda.

Diseño (mismo patrón que el LLM): el retriever es un callable inyectado en el runner
(`make_*_runner(..., retriever=...)`) — el motor (`agent/nodes.py`) sigue puro, sin
saber de Chroma ni de settings. Carga LAZY: importar torch cuesta ~90 s en Mac Intel,
así que el vectorstore se construye recién en la PRIMERA duda, nunca en el arranque.
Fail-safe: cualquier fallo degrada a responder solo con company_info (y no reintenta
en cada turno).
"""

from __future__ import annotations

from typing import Callable, Optional

from src.config import Settings
from src.logging_config import get_logger

logger = get_logger("agent.rag")

# Un retriever recibe la duda del candidato y devuelve el contexto recuperado ("" si nada).
Retriever = Callable[[str], str]


def build_company_retriever(settings: Settings) -> Optional[Retriever]:
    """Retriever de la base de conocimiento, o None si el RAG está desactivado.

    Gated por `settings.interview_rag_enabled` (default False: comportamiento previo).
    El vectorstore (Chroma persistido en `persist_directory`) se abre en el primer uso."""
    if not getattr(settings, "interview_rag_enabled", False):
        return None

    state: dict = {"store": None, "failed": False}

    def retrieve(question: str) -> str:
        if state["failed"] or not (question or "").strip():
            return ""
        try:
            if state["store"] is None:
                logger.info("RAG: abriendo el vectorstore (primera duda; puede tardar)")
                from src.vectorstore import build_vectorstore

                state["store"] = build_vectorstore(settings)
            k = max(1, int(getattr(settings, "final_k", 5) or 5))
            docs = state["store"].similarity_search(question, k=k)
            return "\n\n".join(d.page_content.strip() for d in docs if d.page_content)
        except Exception as e:  # noqa: BLE001 — degradar a company_info plano, sin reintentar
            state["failed"] = True
            logger.warning("RAG no disponible (%s): se responde solo con company_info", e)
            return ""

    return retrieve
