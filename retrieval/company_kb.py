"""Base de conocimiento RAG de la empresa (colección `company_kb`) por vacante.

Compone el documento de conocimiento de una vacante y lo (re)indexa en la colección
Chroma que lee `agente.rag.build_company_retriever` para responder dudas del candidato.

Auditoría v4 (R4, linaje de la KB): editar la vacante en el dashboard encola un
`kb_reindex` en el outbox — el drain del scheduler llama a `reindex_vacancy`, que
PURGA los chunks previos de la vacante antes de indexar. Sin la purga, el upsert por
hash solo agrega: el rango salarial viejo seguiría respondiendo dudas.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import Settings, get_settings
from core.logging_config import get_logger

logger = get_logger(__name__)


def compose_vacancy_text(vacancy: dict, questions: list[dict]) -> str:
    """Arma el documento de conocimiento de una vacante (formato legible para RAG)."""
    parts: list[str] = [f"# Vacante: {vacancy.get('title', '')}"]
    for label, key in (
        ("Área", "area"),
        ("Modalidad", "modality"),
        ("Ubicación", "location"),
        ("Descripción del puesto", "description"),
        ("Información para el candidato", "company_info"),
        ("Detalle del puesto", "details_message"),
        ("Requisitos", "requirements"),
        ("Beneficios", "benefits"),
    ):
        value = vacancy.get(key)
        if isinstance(value, list):
            value = "\n".join(f"- {v}" for v in value)
        if value:
            parts.append(f"## {label}\n{value}")
    if vacancy.get("salary_min") or vacancy.get("salary_max"):
        lo, hi = vacancy.get("salary_min"), vacancy.get("salary_max")
        rango = f"{lo} a {hi}" if lo and hi else (lo or hi)
        parts.append(f"## Rango salarial referencial\n{rango}")
    if questions:
        temas = ", ".join(q.get("label") or q.get("question", "")[:40] for q in questions)
        parts.append(f"## Temas que cubre la entrevista\n{temas}")
    return "\n\n".join(parts).strip()


def vacancy_source(vacancy: dict) -> str:
    """`source` estable de los chunks de una vacante (por id: sobrevive al cambio de título)."""
    return f"vacante:{vacancy.get('id', '')}"


def reindex_vacancy(vacancy_id: str, settings: Settings | None = None) -> int:
    """Purga los chunks previos de la vacante y la re-indexa. Devuelve chunks nuevos.

    Import pesado (torch/Chroma) LAZY: esto corre en el drain del outbox (hilo del
    scheduler), nunca en el request path. Lanza si la vacante no existe (el outbox
    reintenta con backoff y dead-letter)."""
    settings = settings or get_settings()
    from db import repositories as repo
    from langchain_chroma import Chroma

    from retrieval.embeddings import get_embeddings
    from retrieval.vectorstore import index_document

    vacancy = repo.get_vacancy(vacancy_id)
    if not vacancy:
        raise ValueError(f"kb_reindex: la vacante {vacancy_id} no existe")
    questions = repo.get_vacancy_questions(vacancy_id)
    text = compose_vacancy_text(vacancy, questions)
    collection = getattr(settings, "company_kb_collection", "company_kb")

    store = Chroma(
        collection_name=collection,
        persist_directory=settings.persist_directory,
        embedding_function=get_embeddings(settings.embedding_model),
    )
    # Purga por `source`: el actual (por id) y el legado del seed (por título — solo
    # cubre el título vigente; restos de títulos anteriores se limpian con --rebuild).
    for source in {vacancy_source(vacancy), f"vacante:{vacancy.get('title', '')}"}:
        try:
            store.delete(where={"source": source})
        except Exception as e:  # noqa: BLE001 — colección nueva/vacía no debe frenar el index
            logger.debug("kb_reindex: purga de '%s' sin efecto (%s)", source, e)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        _, chunks = index_document(tmp, collection, settings, source_name=vacancy_source(vacancy))
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("kb_reindex: vacante '%s' → %d chunks", vacancy.get("title", vacancy_id), chunks)
    return chunks


def enqueue_reindex(vacancy_id: str, tenant_id: str | None = None) -> None:
    """Encola el reindex en el outbox SIN intento en línea.

    A diferencia de `outbox.deliver`, aquí nunca se ejecuta el handler en el request:
    la primera indexación importa torch (~90 s en Mac Intel) y bloquearía el PUT de la
    vacante. El drain del scheduler (tick 30 s) lo procesa con reintentos/backoff."""
    from db import repositories as repo

    try:
        repo.enqueue_outbox(
            {
                "kind": "kb_reindex",
                "payload": {"vacancy_id": vacancy_id},
                "status": "pending",
                "attempts": 0,
                "max_attempts": 6,
                # Ya vencido: el próximo drain (tick 30 s) lo toma. NULL no matchea el
                # `lte` de list_due_outbox — tiene que ser un timestamp concreto.
                "next_attempt_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": tenant_id,
            }
        )
    except Exception as e:  # noqa: BLE001 — best-effort: no romper el CRUD de la vacante
        logger.warning("kb_reindex: no se pudo encolar para %s: %s", vacancy_id, e)


__all__: list[Any] = ["compose_vacancy_text", "vacancy_source", "reindex_vacancy", "enqueue_reindex"]
