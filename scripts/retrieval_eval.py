"""Golden de RECUPERACIÓN (paso 4) — mide la calidad del retriever SIN LLM.

Cierra la brecha de la auditoría (2.2.3: "sin métricas de recuperación"): corre el MISMO
retriever de `agent/rag.py` (híbrido BM25 + vectorial + cross-encoder sobre la colección
`company_kb`) contra un set de dudas típicas y verifica que el fragmento esperado aparezca
en el contexto recuperado (**hit@k**). Es offline y determinista: no llama al LLM juez, solo
al retriever, así que mide recuperación (no generación).

Requiere: colección sembrada (`scripts/seed_company_kb.py`) + `INTERVIEW_RAG_ENABLED=true` +
el bloque RAG del `.env` (embeddings/Chroma). En Mac Intel la primera consulta tarda (~90 s
por torch). Sale con 1 si el hit-rate cae bajo `min_hit_rate` del golden (usable en nightly).

Uso:
    uv run python scripts/retrieval_eval.py
    uv run python scripts/retrieval_eval.py --min-rate 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "retrieval_set.json"


def hit(context: str, expect: str) -> bool:
    """¿El fragmento esperado aparece en el contexto recuperado? (case-insensitive). Puro."""
    return expect.strip().lower() in (context or "").lower()


def evaluate_retrieval(
    cases: list[dict[str, Any]], retrieve: Callable[[str], str]
) -> list[dict[str, Any]]:
    """Corre cada caso por el retriever y marca hit/miss. Puro respecto del retriever
    (se le inyecta: real en la corrida, fake en los tests). Devuelve la lista de resultados."""
    results: list[dict[str, Any]] = []
    for case in cases:
        context = retrieve(case["question"])
        results.append({
            "id": case.get("id", "?"),
            "hit": hit(context, case["expect"]),
            "expect": case["expect"],
        })
    return results


def hit_rate(results: list[dict[str, Any]]) -> float:
    """Proporción de casos con hit (1.0 si no hay casos). Puro."""
    return sum(1 for r in results if r["hit"]) / len(results) if results else 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden de recuperación (hit@k) del retriever")
    parser.add_argument("--min-rate", type=float, default=None,
                        help="Hit-rate mínimo para salir 0 (default: el del golden)")
    args = parser.parse_args()

    load_dotenv()
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = golden["cases"]
    min_rate = args.min_rate if args.min_rate is not None else float(golden.get("min_hit_rate", 0.8))

    from agent.rag import build_company_retriever
    from src.config import get_settings

    settings = get_settings()
    retrieve = build_company_retriever(settings)
    if retrieve is None:
        print("RAG desactivado (INTERVIEW_RAG_ENABLED=false): nada que evaluar.")
        return 0

    print(f"Recuperación · {len(cases)} caso(s) · colección "
          f"{getattr(settings, 'company_kb_collection', 'company_kb')}\n")
    results = evaluate_retrieval(cases, retrieve)
    for r in results:
        print(f"{'✅' if r['hit'] else '❌'} {r['id']:24s}  espera «{r['expect']}»")

    rate = hit_rate(results)
    print(f"\n{sum(1 for r in results if r['hit'])}/{len(results)} hit "
          f"(tasa {rate:.0%}, mínimo {min_rate:.0%}).")
    if rate < min_rate:
        print("⚠ Recuperación bajo el umbral: revisar el seed/chunking o el pipeline de retrieval.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
