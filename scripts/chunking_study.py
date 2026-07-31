"""Estudio de sensibilidad de chunking vs hit@k (context precision — dimensión A, auditoría v4).

Mide cómo cambia la RECUPERACIÓN (hit@k del golden `tests/golden/retrieval_set.json`) al
variar el tamaño de chunk con que se indexa la base de conocimiento. Para cada configuración
(chunk chico → grande) reindexa las vacantes abiertas en una colección **temporal** (no toca
la `company_kb` viva), corre el mismo golden con el MISMO retriever de producción
(`retrieval/rag.build_company_retriever`) y reporta la tasa de acierto y el nº de chunks.

Intuición que revela: chunks chicos → más piezas que `final_k`, el retriever DEBE seleccionar
(más preciso, pero arriesga partir un hecho en dos y perderlo); chunks grandes → pocas piezas,
el retriever las devuelve casi todas (hit alto pero contexto menos afinado y más tokens).

Requiere: Supabase con vacantes + INTERVIEW_RAG_ENABLED=true + el bloque RAG del .env
(embeddings/Chroma). En Mac Intel la primera indexación tarda (~90 s por torch). Es un ESTUDIO
informativo: siempre sale 0 (no es un gate; el gate de recuperación es scripts/retrieval_eval.py).

Uso:
    uv run python scripts/chunking_study.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar retrieval_eval

from dotenv import load_dotenv  # noqa: E402

# Configuraciones a comparar (2-3). La "grande" replica el default del repo (chunk_size=1200).
CHUNK_CONFIGS: list[dict] = [
    {"label": "chico", "chunk_size": 350, "chunk_overlap": 60},
    {"label": "mediano", "chunk_size": 700, "chunk_overlap": 120},
    {"label": "grande", "chunk_size": 1200, "chunk_overlap": 150},
]


def rank_configs(rows: list[dict]) -> list[dict]:
    """Ordena las filas de resultado por hit_rate desc; desempata por menos chunks (más
    barato/afinado a igual acierto). Pura (testeable sin torch/DB)."""
    return sorted(rows, key=lambda r: (-r.get("hit_rate", 0.0), r.get("chunks", 0)))


def _index_vacancies(vacancies: list[dict], cfg_settings, collection: str) -> int:
    """Reindexa las vacantes (texto compuesto) en `collection` con el chunking de
    `cfg_settings`. Devuelve el total de chunks nuevos. Import pesado LAZY."""
    import tempfile

    from db import repositories as repo
    from retrieval.company_kb import compose_vacancy_text, vacancy_source
    from retrieval.vectorstore import index_document

    total = 0
    for vacancy in vacancies:
        questions = repo.get_vacancy_questions(vacancy["id"])
        text = compose_vacancy_text(vacancy, questions)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            tmp = Path(fh.name)
        try:
            _, chunks = index_document(tmp, collection, cfg_settings, source_name=vacancy_source(vacancy))
        finally:
            tmp.unlink(missing_ok=True)
        total += chunks
    return total


def _drop_collection(cfg_settings, collection: str) -> None:
    """Borra la colección temporal del estudio (best-effort)."""
    try:
        from langchain_chroma import Chroma

        from retrieval.embeddings import get_embeddings

        Chroma(
            collection_name=collection,
            persist_directory=cfg_settings.persist_directory,
            embedding_function=get_embeddings(cfg_settings.embedding_model),
        ).delete_collection()
    except Exception as e:  # noqa: BLE001 — limpieza best-effort
        print(f"  (aviso: no se pudo borrar la colección temporal {collection}: {e})")


def main() -> int:
    load_dotenv()

    import json

    from core.config import get_settings
    from retrieval.rag import build_company_retriever

    import retrieval_eval  # helpers puros: evaluate_retrieval / hit_rate

    settings = get_settings()
    if not getattr(settings, "interview_rag_enabled", False):
        print("RAG desactivado (INTERVIEW_RAG_ENABLED=false): nada que estudiar.")
        return 0

    golden = json.loads(
        (Path(__file__).resolve().parents[1] / "tests" / "golden" / "retrieval_set.json").read_text(
            encoding="utf-8"
        )
    )
    cases = golden["cases"]

    from db import repositories as repo

    vacancies = repo.list_vacancies(status="open")
    if not vacancies:
        print("No hay vacantes abiertas para indexar (¿Supabase corriendo?).")
        return 1

    print(
        f"Estudio de chunking · {len(vacancies)} vacante(s) · {len(cases)} caso(s) golden · "
        f"retrieve_k={getattr(settings, 'retrieve_k', 8)} final_k={getattr(settings, 'final_k', 5)}\n"
    )

    rows: list[dict] = []
    for cfg in CHUNK_CONFIGS:
        collection = f"chunk_study_{cfg['label']}"
        cfg_settings = settings.model_copy(
            update={
                "chunk_size": cfg["chunk_size"],
                "chunk_overlap": cfg["chunk_overlap"],
                "company_kb_collection": collection,
            }
        )
        _drop_collection(cfg_settings, collection)  # arranca limpio (por si quedó de una corrida previa)
        chunks = _index_vacancies(vacancies, cfg_settings, collection)
        retrieve = build_company_retriever(cfg_settings)
        results = retrieval_eval.evaluate_retrieval(cases, retrieve) if retrieve else []
        rate = retrieval_eval.hit_rate(results)
        misses = [r["id"] for r in results if not r["hit"]]
        rows.append(
            {
                "label": cfg["label"],
                "chunk_size": cfg["chunk_size"],
                "chunk_overlap": cfg["chunk_overlap"],
                "chunks": chunks,
                "hit_rate": rate,
                "misses": misses,
            }
        )
        _drop_collection(cfg_settings, collection)  # limpieza

    print(f"{'config':10s} {'chunk_size':>11s} {'overlap':>8s} {'chunks':>7s} {'hit@k':>7s}   misses")
    print("-" * 74)
    for r in rank_configs(rows):
        miss = ", ".join(r["misses"]) if r["misses"] else "—"
        print(
            f"{r['label']:10s} {r['chunk_size']:>11d} {r['chunk_overlap']:>8d} "
            f"{r['chunks']:>7d} {r['hit_rate']:>6.0%}   {miss}"
        )

    best = rank_configs(rows)[0]
    print(
        f"\nMejor por hit@k: '{best['label']}' (chunk_size={best['chunk_size']}, "
        f"{best['hit_rate']:.0%}). Nota: a igual acierto, menos chunks = contexto más afinado y "
        "menos tokens. El default del repo es chunk_size=1200 ('grande')."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
