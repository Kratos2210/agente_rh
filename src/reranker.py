from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Sequence, Tuple

import numpy as np
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder, SentenceTransformer


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str) -> CrossEncoder:
    """Una sola instancia del cross-encoder por modelo (se comparte entre chatbots)."""
    return CrossEncoder(model_name)


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


PENALTY_PATTERNS = (
    "índice",
    "indice",
    "prefacio",
    "prólogo",
    "prologo",
    "lista de colaboradores",
    "contenido",
)

POSITIVE_PATTERNS = (
    "ejemplo",
    "definición",
    "definicion",
    "función",
    "funcion",
    "clase",
    "código",
    "codigo",
    "print",
    "return",
    "lista",
    "diccionario",
    "condicional",
    "recursividad",
    "objetos",
    "método",
    "metodo",
    "glosario",
)

CODE_MARKERS = ("def ", "class ", ">>>", "```", "print(", "print ", "import ", "from ")


@dataclass
class RerankConfig:
    top_k: int = 5
    semantic_weight: float = 0.72
    lexical_weight: float = 0.18
    structure_weight: float = 0.10
    same_source_penalty: float = 0.03


class SemanticReranker:
    def __init__(self, model_name: str, config: RerankConfig):
        self.config = config
        self.model = _load_sentence_transformer(model_name)

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        toks = re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", text.lower(), flags=re.UNICODE)
        return {t for t in toks if len(t) > 2}

    def _lexical_score(self, query: str, text: str) -> float:
        q = self._tokens(query)
        t = self._tokens(text)
        if not q or not t:
            return 0.0
        overlap = len(q & t)
        return float(min(1.0, overlap / max(len(q), 1)))

    def _structure_score(self, text: str) -> float:
        lower = text.lower()
        score = 0.0

        if any(marker in text for marker in CODE_MARKERS):
            score += 0.55
        if any(word in lower for word in POSITIVE_PATTERNS):
            score += 0.30
        if any(word in lower for word in PENALTY_PATTERNS):
            score -= 0.60
        if len(self._tokens(text)) < 40:
            score -= 0.10

        return float(max(-1.0, min(1.0, score)))

    def rerank(self, query: str, documents: List[Document]) -> List[Tuple[Document, float]]:
        if not documents:
            return []

        query_emb = self._embed([query])[0]
        doc_embs = self._embed([doc.page_content for doc in documents])

        scored: List[Tuple[Document, float]] = []
        source_count: dict[str, int] = {}

        for doc, doc_emb in zip(documents, doc_embs):
            semantic = self._cosine(query_emb, doc_emb)
            lexical = self._lexical_score(query, doc.page_content)
            structural = self._structure_score(doc.page_content)

            source = str(doc.metadata.get("source", ""))
            seen = source_count.get(source, 0)
            source_penalty = seen * self.config.same_source_penalty
            source_count[source] = seen + 1

            score = (
                self.config.semantic_weight * semantic
                + self.config.lexical_weight * lexical
                + self.config.structure_weight * structural
                - source_penalty
            )
            scored.append((doc, float(score)))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: self.config.top_k]


class CrossEncoderReranker:
    """
    Reranker basado en un modelo cross-encoder.

    A diferencia del bi-encoder (que embebe pregunta y documento por separado),
    el cross-encoder procesa el par (pregunta, fragmento) junto y predice un
    puntaje de relevancia directo. Es más preciso para reordenar, aunque más
    costoso, por eso se aplica solo a los pocos candidatos recuperados.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", top_k: int = 5):
        self.model = _load_cross_encoder(model_name)
        self.top_k = top_k

    def rerank(self, query: str, documents: List[Document]) -> List[Tuple[Document, float]]:
        if not documents:
            return []
        pairs = [(query, doc.page_content) for doc in documents]
        scores = self.model.predict(pairs, show_progress_bar=False)
        ranked = sorted(
            zip(documents, (float(s) for s in scores)),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[: self.top_k]
