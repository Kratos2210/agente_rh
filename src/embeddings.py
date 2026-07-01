from __future__ import annotations

from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings


@lru_cache(maxsize=4)
def get_embeddings(model_name: str) -> HuggingFaceEmbeddings:
    """
    Devuelve una instancia cacheada de HuggingFaceEmbeddings por modelo.

    Cachear evita recargar el mismo modelo (p. ej. multilingual-e5-base, ~1.1 GB)
    varias veces en memoria cuando lo usan app.py, qa_chain.py y vectorstore.py.
    """
    return HuggingFaceEmbeddings(
        model_name=model_name,
        encode_kwargs={"normalize_embeddings": True},
    )
