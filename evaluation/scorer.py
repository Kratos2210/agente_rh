"""Evaluación de respuestas del candidato y utilidades conversacionales del turno.

Todo se apoya en un LLM inyectable (protocolo orquestacion.llm.LLM) y degrada con gracia
ante fallos del modelo: sin LLM o JSON inválido se usa una heurística conservadora.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Optional

from orquestacion.llm import LLM, complete_staged, parse_json_object
from agente.prompts import (
    ANSWER_CANDIDATE_PROMPT,
    CLASSIFY_TURN_PROMPT,
    DOC_CHECK_PROMPT,
    EVALUATE_ANSWER_PROMPT,
    SCHEDULING_PARSE_PROMPT,
)
from core.logging_config import get_logger

logger = get_logger(__name__)


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


# Firmas de "eco": el candidato pide que el bot escriba/repita un literal concreto
# ("respondé únicamente con la palabra X"). Nunca es una duda real sobre el puesto; es
# el vector de inyección que el red teaming (paso 5) mostró que un modelo chico obedece
# pese al marco anti-inyección del prompt. Detectarlo en el INPUT y derivar sin llamar al
# LLM es la defensa en profundidad (no depende de que el modelo resista). Alta precisión:
# exige verbo imperativo + directiva de literal, improbable en una pregunta genuina.
_ECHO_INJECTION_MARKERS = (
    "solo con la palabra", "sólo con la palabra", "únicamente con la palabra",
    "unicamente con la palabra", "solo la palabra", "sólo la palabra",
    "únicamente la palabra", "unicamente la palabra", "responde solo con",
    "respondé solo con", "respondé únicamente", "responde únicamente",
    "respondé unicamente", "responde unicamente", "repite exactamente",
    "repetí exactamente", "repeti exactamente", "repite la palabra",
    "repetí la palabra", "repeti la palabra", "di exactamente", "decí exactamente",
    "deci exactamente", "escribe exactamente", "escribí exactamente", "escribi exactamente",
    "reply only with", "respond only with", "repeat exactly", "say exactly", "only the word",
)

# Deriva segura ante un mensaje que no es una duda real sino un intento de dirigir la
# respuesta del bot (misma intención que la rama de rechazo de ANSWER_CANDIDATE_PROMPT).
SAFE_DEFLECTION = "Con gusto lo revisamos con el equipo más adelante. ¿Seguimos con la entrevista? 🙌"


def is_echo_injection(text: str) -> bool:
    """True si el mensaje pide que el bot repita/escriba un literal concreto (inyección de eco)."""
    low = (text or "").lower()
    return any(marker in low for marker in _ECHO_INJECTION_MARKERS)


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
    """Devuelve 'answer', 'question' u 'offtopic'. Heurística de respaldo ante fallo del LLM.

    'question' = duda genuina sobre la vacante; 'offtopic' = conocimiento general / ajena a la
    vacante (se deflecta sin responder). El fallback nunca devuelve 'offtopic' (no puede juzgar
    el alcance sin el LLM): degrada a 'question'/'answer', más conservador (no deflecta de más)."""
    try:
        raw = complete_staged(
            llm,
            CLASSIFY_TURN_PROMPT.format(
                question=current_question, message=sanitize_answer_for_prompt(message)
            ),
            "classify",
        )
        kind = str(parse_json_object(raw).get("kind", "")).strip().lower()
        if kind in ("answer", "question", "offtopic"):
            return kind
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM fallback en classify_turn (heurística): %s", e)
    # Heurística: mensaje corto que termina en '?' y empieza con interrogativo → duda.
    stripped = message.strip()
    interrogatives = ("¿", "qué", "cuál", "cuándo", "cómo", "dónde", "cuánto", "quién", "por qué")
    if stripped.endswith("?") and stripped.lower().startswith(interrogatives) and len(stripped) < 160:
        return "question"
    return "answer"


def answer_candidate_question(llm: LLM, *, company_info: str, question: str) -> str:
    """Responde una duda del candidato sobre el puesto/empresa."""
    # Defensa en profundidad: si el mensaje pide que el bot repita/escriba un literal
    # ("respondé solo con la palabra X"), no es una duda — se deriva sin llamar al LLM
    # (el prompt lo desincentiva, pero un modelo chico obedece; audit red teaming paso 5).
    if is_echo_injection(question):
        logger.info("answer_candidate_question: patrón de eco/inyección → deriva segura (sin LLM)")
        return SAFE_DEFLECTION
    try:
        text = complete_staged(
            llm,
            ANSWER_CANDIDATE_PROMPT.format(
                company_info=company_info or "", question=sanitize_answer_for_prompt(question)
            ),
            "answer",
        ).strip()
        if text:
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM fallback en answer_candidate_question (respuesta genérica): %s", e)
    return (
        "Buena pregunta. No tengo ese detalle a la mano, pero el equipo te lo confirmará "
        "más adelante en el proceso. ¿Te parece si seguimos? 🙌"
    )


def classify_candidate_document(llm: LLM, text: str) -> str:
    """Devuelve 'cv', 'cul' u 'other' para el texto de un documento (desambiguación LLM).

    Ante fallo del modelo o JSON inválido devuelve 'unknown' (fail-open: la capa que decide
    NO bloquea con 'unknown', para no rechazar por un problema del clasificador)."""
    snippet = sanitize_answer_for_prompt(text, max_chars=4000)
    if not snippet.strip():
        return "unknown"
    try:
        data = parse_json_object(complete_staged(llm, DOC_CHECK_PROMPT.format(text=snippet), "doc_check"))
        kind = str(data.get("kind", "")).strip().lower()
        if kind in ("cv", "cul", "other"):
            return kind
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM fallback en classify_candidate_document (unknown): %s", e)
    return "unknown"


def parse_slot_choice(llm: LLM, options_human: list[str], message: str) -> Optional[int]:
    """Índice 0-based del horario elegido por el candidato (o None si no eligió).

    Primero intenta el LLM; si falla, cae a una heurística (número suelto en el texto)."""
    if not options_human:
        return None
    listed = "\n".join(f"{i}. {o}" for i, o in enumerate(options_human, 1))
    try:
        data = parse_json_object(
            complete_staged(
                llm,
                SCHEDULING_PARSE_PROMPT.format(
                    options=listed, message=sanitize_answer_for_prompt(message)
                ),
                "schedule",
            )
        )
        choice = int(data.get("choice", 0) or 0)
        if 1 <= choice <= len(options_human):
            return choice - 1
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM fallback en parse_slot_choice (heurística de dígito): %s", e)
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
    except Exception as e:  # noqa: BLE001
        # Fallback conservador: puntaje neutro, marcado baja-confianza → revisión humana (#11).
        logger.warning("LLM fallback en evaluate_answer (score neutro + revisión humana): %s", e)
        return EvalResult(
            score=50.0,
            justification="No se pudo evaluar automáticamente (fallo del modelo); requiere revisión manual.",
            needs_follow_up=False,
            follow_up_question="",
            ack="Gracias por tu respuesta.",
            low_confidence=True,
        )
