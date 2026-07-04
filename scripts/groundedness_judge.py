"""Juez de calidad sobre trazas reales (paso 4 / O-5) — corrida manual/nightly.

Muestrea las trazas `stage="answer"` de `llm_traces` (respuestas del bot a dudas del
candidato, capturadas por O-1 con `LLM_TRACE_ENABLED=true`) y le pregunta a un LLM juez si
cada respuesta (1) se fundamenta SOLO en la info del prompt y (2) atiende la pregunta.
Comparte el juez con el barrido continuo del scheduler (`evaluation/quality.py`).

Requiere: DB con trazas (LLM_TRACE_ENABLED=true en el bot) + OPENAI_* reales en .env.

Uso:
    uv run python scripts/groundedness_judge.py                  # últimas 20 trazas
    uv run python scripts/groundedness_judge.py --sample 50
    uv run python scripts/groundedness_judge.py --min-rate 0.9   # umbral de salida

Sale con 1 si la tasa de fundamentadas queda bajo `--min-rate` (nightly); sin trazas que
juzgar sale con 0 (no es un fallo: el tracing es opt-in).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Juez de calidad sobre trazas answer")
    parser.add_argument("--sample", type=int, default=20, help="Trazas recientes a juzgar (default 20)")
    parser.add_argument("--min-rate", type=float, default=0.9,
                        help="Tasa mínima de fundamentadas para salir 0 (default 0.9)")
    args = parser.parse_args()

    load_dotenv()
    from agent.llm import build_default_llm, complete_staged
    from db import repositories as repo
    from evaluation.quality import QUALITY_JUDGE_PROMPT, judge_verdict, rate

    traces = repo.list_llm_traces_by_stage("answer", limit=max(1, args.sample))
    if not traces:
        print("Sin trazas stage='answer' que juzgar (¿LLM_TRACE_ENABLED está activo en el bot?).")
        return 0

    llm = build_default_llm()
    print(f"Calidad · juez={llm.model} · {len(traces)} traza(s)\n")

    grounded_flags: list[bool] = []
    relevant_flags: list[bool] = []
    context_flags: list[bool] = []
    for t in traces:
        raw = complete_staged(
            llm,
            QUALITY_JUDGE_PROMPT.format(
                prompt=t.get("prompt_text", ""), response=t.get("response_text", "")
            ),
            "judge",
        )
        v = judge_verdict(raw)
        grounded_flags.append(v["grounded"])
        relevant_flags.append(v["answer_relevant"])
        context_flags.append(v.get("context_relevant", False))
        stamp = str(t.get("created_at", ""))[:19]
        marks = (
            f"{'✅' if v['grounded'] else '❌'}fund "
            f"{'✅' if v['answer_relevant'] else '❌'}relev "
            f"{'✅' if v.get('context_relevant') else '❌'}ctx"
        )
        print(f"{marks}  {t.get('id', '?')} ({stamp})  {v['reason'][:120]}")
        if not (v["grounded"] and v["answer_relevant"] and v.get("context_relevant")):
            print(f"   respuesta: {str(t.get('response_text', ''))[:160]}")

    g_rate, r_rate, c_rate = rate(grounded_flags), rate(relevant_flags), rate(context_flags)
    print(f"\nFundamentadas {sum(grounded_flags)}/{len(grounded_flags)} (tasa {g_rate:.0%}, "
          f"mínimo {args.min_rate:.0%}) · Relevantes {sum(relevant_flags)}/{len(relevant_flags)} ({r_rate:.0%}) "
          f"· Contexto {sum(context_flags)}/{len(context_flags)} ({c_rate:.0%}).")
    if g_rate < args.min_rate:
        print("⚠ Respuestas no fundamentadas por encima del tolerado: revisar company_info/prompt.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
