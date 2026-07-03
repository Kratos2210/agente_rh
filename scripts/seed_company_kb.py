"""Siembra la base de conocimiento RAG de la empresa desde las vacantes en Supabase.

Toma cada vacante abierta (o la indicada con --vacancy-id), arma un documento de
texto con su información (título, empresa, descripción/company_info, detalle del
puesto, requisitos) y lo indexa en la colección Chroma `company_kb_collection` —
la que lee `agent/rag.build_company_retriever` para responder dudas del candidato.

Uso:
    uv run python scripts/seed_company_kb.py                 # todas las abiertas
    uv run python scripts/seed_company_kb.py --vacancy-id X  # una en particular
    uv run python scripts/seed_company_kb.py --rebuild       # borra la colección antes

Idempotente: los chunks se identifican por hash de contenido (mismo texto → mismo id),
así que re-correrlo no duplica. Requiere Supabase corriendo y el bloque RAG del .env.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.logging_config import get_logger  # noqa: E402

logger = get_logger("scripts.seed_company_kb")


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


def seed(vacancy_id: str | None = None, rebuild: bool = False) -> int:
    settings = get_settings()
    from db import repositories as repo
    from src.vectorstore import index_document

    if vacancy_id:
        vacancy = repo.get_vacancy(vacancy_id)
        vacancies = [vacancy] if vacancy else []
    else:
        vacancies = repo.list_vacancies(status="open")
    if not vacancies:
        logger.error("No hay vacantes para sembrar (¿Supabase corriendo? ¿id correcto?)")
        return 1

    collection = getattr(settings, "company_kb_collection", "company_kb")
    if rebuild:
        from langchain_chroma import Chroma

        from src.embeddings import get_embeddings

        Chroma(
            collection_name=collection,
            persist_directory=settings.persist_directory,
            embedding_function=get_embeddings(settings.embedding_model),
        ).delete_collection()
        logger.info("Colección %s borrada (--rebuild)", collection)

    total_chunks = 0
    for vacancy in vacancies:
        questions = repo.get_vacancy_questions(vacancy["id"])
        text = compose_vacancy_text(vacancy, questions)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(text)
            tmp = Path(fh.name)
        try:
            _, chunks = index_document(
                tmp, collection, settings, source_name=f"vacante:{vacancy['title']}"
            )
        finally:
            tmp.unlink(missing_ok=True)
        total_chunks += chunks
        logger.info("Indexada '%s': %d chunks nuevos", vacancy["title"], chunks)

    print(
        f"OK: {len(vacancies)} vacante(s) → {total_chunks} chunks nuevos "
        f"en '{collection}' ({settings.persist_directory})"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vacancy-id", help="Sembrar solo esta vacante")
    parser.add_argument(
        "--rebuild", action="store_true", help="Borrar la colección antes de indexar"
    )
    args = parser.parse_args()
    sys.exit(seed(args.vacancy_id, args.rebuild))
