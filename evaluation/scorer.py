"""Evaluación de respuestas del candidato y utilidades conversacionales del turno.

Todo se apoya en un LLM inyectable (protocolo agent.llm.LLM) y degrada con gracia
ante fallos del modelo: sin LLM o JSON inválido se usa una heurística conservadora.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Optional

from agent.llm import LLM, complete_staged, parse_json_object
from agent.prompts import (
    ANSWER_CANDIDATE_PROMPT,
    CLASSIFY_TURN_PROMPT,
    EVALUATE_ANSWER_PROMPT,
    SCHEDULING_PARSE_PROMPT,
)


@dataclass
class EvalResult:
    score: float
    justification: str
    needs_follow_up: bool
    follow_up_question: str
    ack: str
    # True cuando la evaluación no es confiable (fallo del modelo → puntaje neutro).
    # Enruta el scorecard a revisión humana en vez de decidir a ciegas (audit #11).
    low_confidence: bool = False


# Anti-gaming: la respuesta del candidato entra al prompt entre delimitadores y acotada,
# para que no pueda inyectar instrucciones ni desbordar el contexto (audit #10).
MAX_ANSWER_CHARS = 4000
_ANSWER_DELIMS = ("<<<respuesta>>>", "<<<fin>>>")


def is_meaningful_answer(text: str) -> bool:
    """False si la respuesta está vacía o es solo símbolos/emoji (nada que evaluar)."""
    stripped = (text or "").strip()
    return bool(stripped) and any(ch.isalnum() for ch in stripped)


def sanitize_answer_for_prompt(answer: str, max_chars: int = MAX_ANSWER_CHARS) -> str:
    """Prepara la respuesta del candidato para el prompt: quita los delimitadores (evita
    breakout de una inyección) y acota la longitud."""
    cleaned = answer or ""
    for d in _ANSWER_DELIMS:
        cleaned = cleaned.replace(d, "")
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + " […truncado…]"
    return cleaned


def _clamp_score(value: object) -> float:
    try:
        s = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, s))


def classify_turn(llm: LLM, *, current_question: str, message: str) -> str:
    """Devuelve 'answer' o 'question'. Heurística de respaldo ante fallo del LLM."""
    try:
        raw = complete_staged(
            llm,
            CLASSIFY_TURN_PROMPT.format(question=current_question, message=message),
            "classify",
        )
        kind = str(parse_json_object(raw).get("kind", "")).strip().lower()
        if kind in ("answer", "question"):
            return kind
    except Exception:
        pass
    # Heurística: mensaje corto que termina en '?' y empieza con interrogativo → duda.
    stripped = message.strip()
    interrogatives = ("¿", "qué", "cuál", "cuándo", "cómo", "dónde", "cuánto", "quién", "por qué")
    if stripped.endswith("?") and stripped.lower().startswith(interrogatives) and len(stripped) < 160:
        return "question"
    return "answer"


def answer_candidate_question(llm: LLM, *, company_info: str, question: str) -> str:
    """Responde una duda del candidato sobre el puesto/empresa."""
    try:
        text = complete_staged(
            llm,
            ANSWER_CANDIDATE_PROMPT.format(company_info=company_info or "", question=question),
            "answer",
        ).strip()
        if text:
            return text
    except Exception:
        pass
    return (
        "Buena pregunta. No tengo ese detalle a la mano, pero el equipo te lo confirmará "
        "más adelante en el proceso. ¿Te parece si seguimos? 🙌"
    )


def parse_slot_choice(llm: LLM, options_human: list[str], message: str) -> Optional[int]:
    """Índice 0-based del horario elegido por el candidato (o None si no eligió).

    Primero intenta el LLM; si falla, cae a una heurística (número suelto en el texto)."""
    if not options_human:
        return None
    listed = "\n".join(f"{i}. {o}" for i, o in enumerate(options_human, 1))
    try:
        data = parse_json_object(
            complete_staged(
                llm, SCHEDULING_PARSE_PROMPT.format(options=listed, message=message), "schedule"
            )
        )
        choice = int(data.get("choice", 0) or 0)
        if 1 <= choice <= len(options_human):
            return choice - 1
    except Exception:
        pass
    # Heurística: un único dígito 1..N presente en la respuesta.
    digits = {c for c in message if c.isdigit()}
    if len(digits) == 1:
        n = int(next(iter(digits)))
        if 1 <= n <= len(options_human):
            return n - 1
    return None


def evaluate_answer(
    llm: LLM,
    *,
    question: str,
    criterion: str,
    answer: str,
    can_follow_up: bool,
    cv_context: str = "",
) -> EvalResult:
    """Evalúa la respuesta contra el criterio. `can_follow_up` desactiva la repregunta
    cuando ya se agotó el presupuesto de follow-ups de la pregunta. `cv_context` (opcional)
    aporta el dato del CV para que el score refleje la consistencia CV↔respuesta."""
    context_block = (
        f'Dato declarado en el CV (para contrastar consistencia): "{cv_context}"' if cv_context else ""
    )
    safe_answer = sanitize_answer_for_prompt(answer)
    try:
        data = parse_json_object(
            complete_staged(
                llm,
                EVALUATE_ANSWER_PROMPT.format(
                    question=question, criterion=criterion, answer=safe_answer, cv_context=context_block
                ),
                "evaluate",
            )
        )
        score = _clamp_score(data.get("score"))
        justification = str(data.get("justification", "")).strip()[:500]
        needs = bool(data.get("needs_follow_up", False)) and can_follow_up
        follow_up = str(data.get("follow_up_question", "")).strip()[:400]
        ack = str(data.get("ack", "")).strip()[:280]
        if needs and not follow_up:
            follow_up = "¿Podrías ampliar un poco más, con ejemplos o herramientas concretas? 🙌"
        return EvalResult(
            score=score,
            justification=justification or "Sin justificación generada.",
            needs_follow_up=needs,
            follow_up_question=follow_up if needs else "",
            ack=ack or "Gracias por tu respuesta.",
        )
    except Exception:
        # Fallback conservador: puntaje neutro, marcado baja-confianza → revisión humana (#11).
        return EvalResult(
            score=50.0,
            justification="No se pudo evaluar automáticamente (fallo del modelo); requiere revisión manual.",
            needs_follow_up=False,
            follow_up_question="",
            ack="Gracias por tu respuesta.",
            low_confidence=True,
        )
