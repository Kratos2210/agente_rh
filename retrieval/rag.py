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

from core.config import Settings
from core.logging_config import get_logger

logger = get_logger("retrieval.rag")

# Un retriever recibe la duda del candidato y devuelve el contexto recuperado ("" si nada).
Retriever = Callable[[str], str]


def build_company_retriever(settings: Settings) -> Optional[Retriever]:
    """Retriever de la base de conocimiento, o None si el RAG está desactivado.

    Gated por `settings.interview_rag_enabled`. Abre la colección Chroma persistida
    (`company_kb_collection`, la que escribe scripts/seed_company_kb.py) en modo
    LECTURA — acá no se indexa nada — y arma el MISMO pipeline del chatbot clásico
    (`src/qa_chain.RAGChatbot`): hybrid search (BM25 léxico + vectorial) sobre
    `retrieve_k` candidatos y re-rank con cross-encoder al top `final_k`.

    Degradación en capas: si BM25 o el re-ranker fallan se sigue con vectorial puro;
    si la colección no existe o está vacía, degrada a responder solo con company_info
    (una vez, sin reintentar cada turno)."""
    if not getattr(settings, "interview_rag_enabled", False):
        return None

    state: dict = {"store": None, "bm25": None, "reranker": None, "failed": False}

    def _open(retrieve_k: int, final_k: int) -> None:
        logger.info("RAG: abriendo el vectorstore (primera duda; puede tardar)")
        from retrieval import embeddings as emb

        embedding_fn = emb.get_embeddings(settings.embedding_model)
        from langchain_chroma import Chroma

        store = Chroma(
            collection_name=getattr(settings, "company_kb_collection", "company_kb"),
            persist_directory=settings.persist_directory,
            embedding_function=embedding_fn,
        )
        if not (store._collection.count() or 0):
            raise RuntimeError("colección vacía — sembrar con scripts/seed_company_kb.py")
        state["store"] = store

        # BM25 sobre el corpus completo de la colección (es chico: fichas de vacantes).
        try:
            raw = store._collection.get()
            from langchain_community.retrievers import BM25Retriever
            from langchain_core.documents import Document

            corpus = [
                Document(page_content=text, metadata=meta or {})
                for text, meta in zip(raw["documents"], raw["metadatas"])
                if text
            ]
            if corpus:
                bm25 = BM25Retriever.from_documents(corpus)
                bm25.k = retrieve_k
                state["bm25"] = bm25
        except Exception as e:  # noqa: BLE001 — hybrid opcional: seguir vectorial puro
            logger.warning("RAG: BM25 no disponible (%s); búsqueda solo vectorial", e)

        # Re-ranker seleccionable por config (mismo patrón que RAGChatbot).
        try:
            from ranking.reranker import CrossEncoderReranker, RerankConfig, SemanticReranker

            if getattr(settings, "reranker", "cross") == "cross":
                state["reranker"] = CrossEncoderReranker(
                    model_name=settings.cross_encoder_model, top_k=final_k
                )
            else:
                state["reranker"] = SemanticReranker(
                    model_name=settings.embedding_model, config=RerankConfig(top_k=final_k)
                )
        except Exception as e:  # noqa: BLE001 — re-rank opcional: orden vectorial
            logger.warning("RAG: re-ranker no disponible (%s); orden vectorial", e)

    def retrieve(question: str) -> str:
        if state["failed"] or not (question or "").strip():
            return ""
        retrieve_k = max(1, int(getattr(settings, "retrieve_k", 8) or 8))
        final_k = max(1, int(getattr(settings, "final_k", 5) or 5))
        try:
            if state["store"] is None:
                _open(retrieve_k, final_k)

            # Candidatos: vectorial + BM25 (dedupe por contenido, cap antes del
            # cross-encoder para no pagar scoring cuadrático de más).
            docs = state["store"].similarity_search(question, k=retrieve_k)
            if state["bm25"] is not None:
                seen = {d.page_content for d in docs}
                docs += [
                    d for d in state["bm25"].invoke(question) if d.page_content not in seen
                ]
            docs = docs[:retrieve_k]

            if state["reranker"] is not None and len(docs) > 1:
                docs = [d for d, _score in state["reranker"].rerank(question, docs)]
            docs = docs[:final_k]
            return "\n\n".join(d.page_content.strip() for d in docs if d.page_content)
        except Exception as e:  # noqa: BLE001 — degradar a company_info plano, sin reintentar
            state["failed"] = True
            logger.warning("RAG no disponible (%s): se responde solo con company_info", e)
            return ""

    return retrieve
