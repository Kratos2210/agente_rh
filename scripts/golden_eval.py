"""Suite GOLDEN del prompt de evaluación — corrida manual contra el LLM real.

Los 200+ tests de la suite validan la LÓGICA con FakeLLM; esta suite valida la
CALIDAD del prompt (`EVALUATE_ANSWER_PROMPT`) contra el modelo real: cada caso del
golden set (respuestas reales de la entrevista de Alberto + contraejemplos débiles)
debe caer dentro de su rango de score esperado. Correrla al cambiar de modelo o al
subir `PROMPT_VERSION` (agent/prompts.py).

Uso (requiere OPENAI_API_KEY/BASE/MODEL reales en .env):
    uv run python scripts/golden_eval.py            # corre todos los casos
    uv run python scripts/golden_eval.py --case id  # corre un caso puntual

Sale con código 1 si algún caso queda fuera de rango (utilizable en un job nightly).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "golden_set.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite golden del prompt de evaluación")
    parser.add_argument("--case", default="", help="ID de un caso puntual (default: todos)")
    args = parser.parse_args()

    load_dotenv()
    from agent.llm import build_default_llm
    from agent.prompts import PROMPT_VERSION
    from evaluation.scorer import evaluate_answer

    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"Caso '{args.case}' no existe en {GOLDEN_PATH}")
            return 2

    llm = build_default_llm()
    model = getattr(llm, "model", "?")
    print(f"Golden eval · modelo={model} · prompt_version={PROMPT_VERSION} · {len(cases)} caso(s)\n")

    failures = 0
    for c in cases:
        result = evaluate_answer(
            llm,
            question=c["question"],
            criterion=c["criterion"],
            answer=c["answer"],
            can_follow_up=False,
        )
        lo, hi = c["expected_min"], c["expected_max"]
        ok = lo <= result.score <= hi and not result.low_confidence
        mark = "✅" if ok else "❌"
        if not ok:
            failures += 1
        extra = " (low_confidence: fallo del LLM)" if result.low_confidence else ""
        print(f"{mark} {c['id']:<22} score={result.score:>5.1f}  esperado=[{lo}, {hi}]{extra}")
        if not ok:
            print(f"   justificación: {result.justification[:160]}")

    print(f"\n{len(cases) - failures}/{len(cases)} dentro de rango.")
    if failures:
        print("⚠ El prompt/modelo se desvió del comportamiento esperado: revisar antes de desplegar.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
