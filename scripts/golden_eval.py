"""Suite GOLDEN del pipeline LLM — corrida manual contra el LLM real (O-5).

Los 260+ tests de la suite validan la LÓGICA con FakeLLM; esta suite valida la
CALIDAD de los prompts contra el modelo real, en 4 frentes:

  - evaluate:  EVALUATE_ANSWER_PROMPT — respuestas reales de la entrevista de Alberto
               (16/06/2026) + contraejemplos débiles/inyección, con rango de score esperado.
  - classify:  CLASSIFY_TURN_PROMPT — ¿el mensaje del candidato es respuesta o duda?
  - slot:      SCHEDULING_PARSE_PROMPT — elección de horario en lenguaje natural.
  - prescreen: PRESCREEN_CV_PROMPT — gate del CV contra la vacante (rango de pre_score).

Correrla al cambiar de modelo o al subir `PROMPT_VERSION` (agent/prompts.py), o como
job nightly (cron/launchd): sale con código 1 si algún caso queda fuera de rango.

Uso (requiere OPENAI_API_KEY/BASE/MODEL reales en .env):
    uv run python scripts/golden_eval.py                    # todas las suites
    uv run python scripts/golden_eval.py --suite classify   # una suite
    uv run python scripts/golden_eval.py --case id          # un caso puntual
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "golden_set.json"

# Suite → clave del JSON con sus casos.
SUITE_KEYS = {
    "evaluate": "cases",
    "classify": "classify_cases",
    "slot": "slot_cases",
    "prescreen": "prescreen_cases",
}


def run_evaluate(llm, cases: list[dict]) -> int:
    from evaluation.scorer import evaluate_answer

    failures = 0
    for c in cases:
        result = evaluate_answer(
            llm, question=c["question"], criterion=c["criterion"],
            answer=c["answer"], can_follow_up=False,
        )
        lo, hi = c["expected_min"], c["expected_max"]
        ok = lo <= result.score <= hi and not result.low_confidence
        if not ok:
            failures += 1
        extra = " (low_confidence: fallo del LLM)" if result.low_confidence else ""
        print(f"{'✅' if ok else '❌'} {c['id']:<28} score={result.score:>5.1f}  esperado=[{lo}, {hi}]{extra}")
        if not ok:
            print(f"   justificación: {result.justification[:160]}")
    return failures


def run_classify(llm, cases: list[dict]) -> int:
    from evaluation.scorer import classify_turn

    failures = 0
    for c in cases:
        kind = classify_turn(llm, current_question=c["question"], message=c["message"])
        ok = kind == c["expected"]
        if not ok:
            failures += 1
        print(f"{'✅' if ok else '❌'} {c['id']:<28} kind={kind:<9} esperado={c['expected']}")
    return failures


def run_slot(llm, cases: list[dict]) -> int:
    from evaluation.scorer import parse_slot_choice

    failures = 0
    for c in cases:
        choice = parse_slot_choice(llm, c["options"], c["message"])
        ok = choice == c["expected"]
        if not ok:
            failures += 1
        print(f"{'✅' if ok else '❌'} {c['id']:<28} choice={choice!s:<5} esperado={c['expected']!s}")
    return failures


def run_prescreen(llm, cases: list[dict]) -> int:
    from evaluation.prescreen import prescreen_cv

    failures = 0
    for c in cases:
        result = prescreen_cv(
            llm, vacancy=c["vacancy"], cv_profile=c["cv_profile"], criteria=c["criteria"],
        )
        lo, hi = c["expected_min"], c["expected_max"]
        ok = lo <= result.pre_score <= hi
        if not ok:
            failures += 1
        print(f"{'✅' if ok else '❌'} {c['id']:<28} pre_score={result.pre_score:>5.1f}  esperado=[{lo}, {hi}]  verdict={result.verdict}")
        if not ok:
            print(f"   resumen: {result.summary[:160]}")
    return failures


SUITE_RUNNERS = {
    "evaluate": run_evaluate,
    "classify": run_classify,
    "slot": run_slot,
    "prescreen": run_prescreen,
}


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite golden del pipeline LLM")
    parser.add_argument("--suite", default="all", choices=["all", *SUITE_KEYS],
                        help="Suite a correr (default: todas)")
    parser.add_argument("--case", default="", help="ID de un caso puntual (default: todos)")
    args = parser.parse_args()

    load_dotenv()
    from agent.llm import MeteredLLM, build_default_llm
    from agent.prompts import PROMPT_VERSION

    data = load_golden()
    suites = list(SUITE_KEYS) if args.suite == "all" else [args.suite]
    plan: list[tuple[str, list[dict]]] = []
    for suite in suites:
        cases = data.get(SUITE_KEYS[suite]) or []
        if args.case:
            cases = [c for c in cases if c["id"] == args.case]
        if cases:
            plan.append((suite, cases))
    if not plan:
        print(f"Caso '{args.case}' no existe en {GOLDEN_PATH}" if args.case else "Sin casos.")
        return 2

    # MeteredLLM para que cada llamada quede etiquetada por etapa (los runners internos
    # ya marcan stage con complete_staged).
    llm = MeteredLLM(build_default_llm())
    total = sum(len(cases) for _, cases in plan)
    print(f"Golden eval · modelo={llm.model} · prompt_version={PROMPT_VERSION} · {total} caso(s)\n")

    failures = 0
    ran = 0
    for suite, cases in plan:
        print(f"── {suite} ({len(cases)}) " + "─" * 40)
        failures += SUITE_RUNNERS[suite](llm, cases)
        ran += len(cases)
        print()

    print(f"{ran - failures}/{ran} dentro de rango.")
    if failures:
        print("⚠ El prompt/modelo se desvió del comportamiento esperado: revisar antes de desplegar.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
