"""Juez de GROUNDEDNESS sobre trazas reales (O-5) — corrida manual/nightly.

Muestrea las trazas `stage="answer"` de `llm_traces` (las respuestas del bot a dudas
del candidato, capturadas por O-1 con `LLM_TRACE_ENABLED=true`) y le pregunta a un LLM
juez si cada respuesta se fundamenta EXCLUSIVAMENTE en la "Información disponible sobre
el puesto y la empresa" que traía el prompt (o deriva al equipo cuando el dato no está).
Detecta alucinaciones operativas: salario/horarios/beneficios/direcciones inventados.

Requiere: DB con trazas (LLM_TRACE_ENABLED=true en el bot) + OPENAI_* reales en .env.

Uso:
    uv run python scripts/groundedness_judge.py                  # últimas 20 trazas
    uv run python scripts/groundedness_judge.py --sample 50
    uv run python scripts/groundedness_judge.py --min-rate 0.9   # umbral de salida

Sale con 1 si la tasa de respuestas fundamentadas queda bajo `--min-rate` (nightly);
sin trazas que juzgar sale con 0 (no es un fallo: el tracing es opt-in).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

GROUNDEDNESS_JUDGE_PROMPT = """Sos un auditor de calidad de un asistente de selección de personal.
El asistente debía responder la duda de un candidato usando SOLO la "Información disponible
sobre el puesto y la empresa" incluida en el prompt; si el dato no estaba, debía decir con
amabilidad que el equipo lo confirmará más adelante (eso TAMBIÉN cuenta como fundamentado).

Prompt original que recibió el asistente:
---
{prompt}
---

Respuesta que dio el asistente:
---
{response}
---

¿La respuesta está fundamentada exclusivamente en esa información? Marcá false si inventa,
promete o confirma datos (salario, horarios, beneficios, dirección, fechas, condiciones)
que NO aparecen en la información del prompt.

Devolvé SOLO un JSON (sin markdown): {{"grounded": true|false, "reason": "<1 frase>"}}
JSON:"""


def judge_verdict(raw: str) -> tuple[bool, str]:
    """Parsea el veredicto del juez. Ilegible → NO fundamentado (conservador). Puro."""
    from agent.llm import parse_json_object

    try:
        data = parse_json_object(raw)
        return bool(data.get("grounded")), str(data.get("reason", "")).strip()
    except Exception:  # noqa: BLE001
        return False, "veredicto del juez ilegible"


def grounded_rate(verdicts: list[bool]) -> float:
    """Proporción de respuestas fundamentadas (1.0 si no hay muestras). Puro."""
    return sum(verdicts) / len(verdicts) if verdicts else 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Juez de groundedness sobre trazas answer")
    parser.add_argument("--sample", type=int, default=20, help="Trazas recientes a juzgar (default 20)")
    parser.add_argument("--min-rate", type=float, default=0.9,
                        help="Tasa mínima de fundamentadas para salir 0 (default 0.9)")
    args = parser.parse_args()

    load_dotenv()
    from agent.llm import build_default_llm, complete_staged
    from db import repositories as repo

    traces = repo.list_llm_traces_by_stage("answer", limit=max(1, args.sample))
    if not traces:
        print("Sin trazas stage='answer' que juzgar (¿LLM_TRACE_ENABLED está activo en el bot?).")
        return 0

    llm = build_default_llm()
    print(f"Groundedness · juez={llm.model} · {len(traces)} traza(s)\n")

    verdicts: list[bool] = []
    for t in traces:
        raw = complete_staged(
            llm,
            GROUNDEDNESS_JUDGE_PROMPT.format(
                prompt=t.get("prompt_text", ""), response=t.get("response_text", "")
            ),
            "judge",
        )
        grounded, reason = judge_verdict(raw)
        verdicts.append(grounded)
        stamp = str(t.get("created_at", ""))[:19]
        print(f"{'✅' if grounded else '❌'} {t.get('id', '?')} ({stamp})  {reason[:140]}")
        if not grounded:
            print(f"   respuesta: {str(t.get('response_text', ''))[:160]}")

    rate = grounded_rate(verdicts)
    print(f"\n{sum(verdicts)}/{len(verdicts)} fundamentadas (tasa {rate:.0%}, mínimo {args.min_rate:.0%}).")
    if rate < args.min_rate:
        print("⚠ Respuestas no fundamentadas por encima del tolerado: revisar company_info/prompt.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
