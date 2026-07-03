"""Juez de calidad de las respuestas del bot (paso 4 — medición continua).

Evalúa, en UNA sola llamada al LLM, dos dimensiones de una respuesta a la duda de un
candidato (traza `stage="answer"` de O-1):

  - **grounded (fidelidad)**: ¿se apoya EXCLUSIVAMENTE en la "Información disponible sobre
    el puesto y la empresa" del prompt (o deriva al equipo cuando el dato no está)? Caza
    alucinaciones operativas: salario/horarios/beneficios/direcciones inventados.
  - **answer_relevant (relevancia de respuesta)**: ¿la respuesta ATIENDE la pregunta del
    candidato (no evade ni responde otra cosa)? Derivar al equipo por falta de dato SIGUE
    siendo relevante.

Es la fuente única compartida por el script manual (`scripts/groundedness_judge.py`) y el
barrido continuo del scheduler (`api/scheduler.py::_quality_sweep`). Las funciones de
parseo/agregación son PURAS (testeables sin LLM ni red).

La relevancia de CONTEXTO/recuperación (¿el chunk recuperado era el correcto?) NO se mide
aquí —no está en la traza de forma fiable— sino offline con el golden de retrieval
(`tests/golden/retrieval_set.json`, `scripts/retrieval_eval.py`).
"""

from __future__ import annotations

from typing import Any

# Métricas que persiste el barrido (claves de `quality_metrics.metric`).
METRIC_GROUNDED = "grounded"
METRIC_ANSWER_RELEVANCE = "answer_relevance"

QUALITY_JUDGE_PROMPT = """Sos un auditor de calidad de un asistente de selección de personal.
El asistente debía responder la duda de un candidato usando SOLO la "Información disponible
sobre el puesto y la empresa" incluida en el prompt; si el dato no estaba, debía decir con
amabilidad que el equipo lo confirmará más adelante (eso TAMBIÉN cuenta como fundamentado y
como relevante).

Prompt original que recibió el asistente:
---
{prompt}
---

Respuesta que dio el asistente:
---
{response}
---

Evaluá DOS cosas:
1) grounded: ¿la respuesta se fundamenta EXCLUSIVAMENTE en esa información? Marcá false si
   inventa, promete o confirma datos (salario, horarios, beneficios, dirección, fechas,
   condiciones) que NO aparecen en la información del prompt.
2) answer_relevant: ¿la respuesta ATIENDE la pregunta concreta del candidato? Marcá false si
   evade, cambia de tema o responde algo distinto de lo preguntado. (Derivar al equipo por
   falta de dato SÍ es relevante.)

Devolvé SOLO un JSON (sin markdown):
{{"grounded": true|false, "answer_relevant": true|false, "reason": "<1 frase>"}}
JSON:"""


def judge_verdict(raw: str) -> dict[str, Any]:
    """Parsea el veredicto del juez. Puro y conservador: lo ilegible cuenta como NO
    fundamentado y NO relevante (así una degradación del juez no oculta problemas).

    Devuelve {"grounded": bool, "answer_relevant": bool, "reason": str}."""
    from agent.llm import parse_json_object

    try:
        data = parse_json_object(raw)
        return {
            "grounded": bool(data.get("grounded")),
            "answer_relevant": bool(data.get("answer_relevant")),
            "reason": str(data.get("reason", "")).strip(),
        }
    except Exception:  # noqa: BLE001
        return {"grounded": False, "answer_relevant": False, "reason": "veredicto del juez ilegible"}


def rate(flags: list[bool]) -> float:
    """Proporción de True (1.0 si no hay muestras — nada que reprochar). Pura."""
    return sum(1 for f in flags if f) / len(flags) if flags else 1.0
