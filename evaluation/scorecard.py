"""Construcción del scorecard final: score ponderado, semáforo y recomendación.

El puntaje y el semáforo son deterministas (regla sobre los pesos). El resumen y la
recomendación los redacta el LLM; ante fallo, se generan reglas de respaldo.
"""

from __future__ import annotations

from typing import Any, Optional

from orquestacion.llm import LLM, complete_staged, parse_json_object
from agente.prompts import PROMPT_VERSION, SCORECARD_PROMPT
from core.logging_config import get_logger

logger = get_logger(__name__)

SEMAPHORE_GREEN = "green"
SEMAPHORE_YELLOW = "yellow"
SEMAPHORE_RED = "red"

_SEMAPHORE_EMOJI = {SEMAPHORE_GREEN: "🟢", SEMAPHORE_YELLOW: "🟡", SEMAPHORE_RED: "🔴"}


def semaphore_emoji(semaphore: str) -> str:
    return _SEMAPHORE_EMOJI.get(semaphore, "⚪")


def weighted_total(answers: list[dict[str, Any]]) -> float:
    """Media ponderada de los scores por su peso. Ignora respuestas sin score."""
    num = 0.0
    den = 0.0
    for a in answers:
        score = a.get("score")
        if score is None:
            continue
        weight = float(a.get("weight", 1.0) or 0.0)
        num += float(score) * weight
        den += weight
    return round(num / den, 1) if den else 0.0


def compute_semaphore(total: float, *, green_min: int, yellow_min: int) -> str:
    if total >= green_min:
        return SEMAPHORE_GREEN
    if total >= yellow_min:
        return SEMAPHORE_YELLOW
    return SEMAPHORE_RED


def _per_criterion(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question": a.get("text", ""),
            "label": a.get("label", ""),
            "criterion": a.get("criterion", ""),
            "score": a.get("score"),
            "weight": a.get("weight", 1.0),
            "justification": a.get("justification", ""),
            "low_confidence": bool(a.get("low_confidence", False)),
        }
        for a in answers
    ]


def _format_per_criterion(items: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(items, start=1):
        score = it.get("score")
        score_txt = f"{score:.0f}" if isinstance(score, (int, float)) else "s/d"
        lines.append(
            f"{i}. [{score_txt}/100] {it.get('criterion', '')}\n   {it.get('justification', '')}"
        )
    return "\n".join(lines)


def _fallback_recommendation(semaphore: str) -> tuple[str, str]:
    if semaphore == SEMAPHORE_GREEN:
        return (
            "El candidato muestra buen ajuste al perfil según los criterios evaluados.",
            "Recomendado para avanzar a la siguiente etapa.",
        )
    if semaphore == SEMAPHORE_YELLOW:
        return (
            "El candidato cumple parcialmente el perfil; hay puntos a profundizar.",
            "Revisar manualmente antes de decidir si avanza.",
        )
    return (
        "El candidato no alcanza los criterios mínimos del perfil.",
        "No recomendado para avanzar en esta vacante.",
    )


def build_scorecard(
    answers: list[dict[str, Any]],
    *,
    vacancy_title: str,
    green_min: int,
    yellow_min: int,
    llm: Optional[LLM] = None,
) -> dict[str, Any]:
    """Arma el scorecard completo a partir de las respuestas evaluadas."""
    total = weighted_total(answers)
    semaphore = compute_semaphore(total, green_min=green_min, yellow_min=yellow_min)
    per_crit = _per_criterion(answers)

    summary = ""
    recommendation = ""
    if llm is not None:
        try:
            data = parse_json_object(
                complete_staged(
                    llm,
                    SCORECARD_PROMPT.format(
                        vacancy_title=vacancy_title,
                        total_score=total,
                        semaphore=semaphore,
                        per_criterion=_format_per_criterion(per_crit),
                    ),
                    "scorecard",
                )
            )
            summary = str(data.get("summary", "")).strip()[:1500]
            recommendation = str(data.get("recommendation", "")).strip()[:600]
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM fallback en build_scorecard (resumen por reglas): %s", e)

    if not summary or not recommendation:
        fb_summary, fb_reco = _fallback_recommendation(semaphore)
        summary = summary or fb_summary
        recommendation = recommendation or fb_reco

    # Revisión humana requerida si alguna respuesta se evaluó con baja confianza (audit #11).
    review_required = any(a.get("low_confidence") for a in answers)

    return {
        "total_score": total,
        "semaphore": semaphore,
        "summary": summary,
        "recommendation": recommendation,
        "per_criterion": per_crit,
        "review_required": review_required,
        # Versión de los prompts con que se evaluó (comparabilidad entre scorecards).
        "prompt_version": PROMPT_VERSION,
    }
