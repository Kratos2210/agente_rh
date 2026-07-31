"""Juez de calidad sobre trazas reales (paso 4 / O-5) — corrida manual/nightly.

Muestrea las trazas `stage="answer"` de `llm_traces` (respuestas del bot a dudas del
candidato, capturadas por O-1 con `LLM_TRACE_ENABLED=true`) y le pregunta a un LLM juez, en
UNA llamada, las tres dimensiones RAGAS de `evaluation/quality.py`:
  (1) **grounded** — la respuesta se apoya SOLO en la info del prompt (fidelidad);
  (2) **answer_relevant** — atiende la pregunta del candidato;
  (3) **context_relevant** — la info recuperada por el RAG contenía lo necesario para
      responder (precisión/recall de CONTEXTO — dimensión A de la auditoría v4).

Comparte el juez con el barrido continuo del scheduler (`api/scheduler.py::_quality_sweep`).

Requiere: DB con trazas (LLM_TRACE_ENABLED=true en el bot) + OPENAI_* reales en .env.

Uso:
    uv run python scripts/groundedness_judge.py                       # últimas 20 trazas
    uv run python scripts/groundedness_judge.py --sample 50
    uv run python scripts/groundedness_judge.py --min-rate 0.9 --min-context-rate 0.8

Sale con 1 si la tasa de fundamentadas cae bajo `--min-rate` O la de contexto pertinente
cae bajo `--min-context-rate` (cada dimensión con su propio umbral, para el nightly); sin
trazas que juzgar sale con 0 (no es un fallo: el tracing es opt-in).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402


def gate(
    grounded_rate: float,
    context_rate: float,
    min_grounded: float,
    min_context: float,
) -> tuple[int, list[str]]:
    """Decide el exit code del nightly a partir de las tasas y sus umbrales propios. Pura.

    Cada dimensión gatilla por separado (fundamentación y pertinencia de contexto tienen
    umbrales independientes: la recuperación puede degradarse aunque las respuestas sigan
    fundamentadas, y viceversa). Devuelve (exit_code, motivos)."""
    reasons: list[str] = []
    if grounded_rate < min_grounded:
        reasons.append(
            f"fundamentación {grounded_rate:.0%} < mínimo {min_grounded:.0%} "
            "(respuestas inventan/confirman datos fuera del prompt)"
        )
    if context_rate < min_context:
        reasons.append(
            f"contexto pertinente {context_rate:.0%} < mínimo {min_context:.0%} "
            "(la recuperación no trajo el dato — revisar seed/chunking/retrieval)"
        )
    return (1 if reasons else 0), reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Juez de calidad sobre trazas answer")
    parser.add_argument("--sample", type=int, default=20, help="Trazas recientes a juzgar (default 20)")
    parser.add_argument("--min-rate", type=float, default=0.9,
                        help="Tasa mínima de fundamentadas para salir 0 (default 0.9)")
    parser.add_argument("--min-context-rate", type=float, default=0.8,
                        help="Tasa mínima de contexto pertinente para salir 0 (default 0.8)")
    args = parser.parse_args()

    load_dotenv()
    from orquestacion.llm import build_default_llm, complete_staged
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
          f"· Contexto pertinente {sum(context_flags)}/{len(context_flags)} ({c_rate:.0%}, "
          f"mínimo {args.min_context_rate:.0%}).")
    code, reasons = gate(g_rate, c_rate, args.min_rate, args.min_context_rate)
    for reason in reasons:
        print(f"⚠ {reason}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
