"""Estado de la entrevista (durable entre mensajes vía el checkpointer de LangGraph).

El estado es un dict plano (TypedDict) para que LangGraph lo serialice sin fricción.
Las claves usan semántica de sobreescritura: cada turno devuelve los valores nuevos.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


# Fases del flujo de la entrevista.
PHASE_GREETING = "greeting"            # esperando Acepto / No interesado
PHASE_INTERVIEWING = "interviewing"    # haciendo preguntas
PHASE_AWAITING_DOCS = "awaiting_docs"  # entrevista ok (verde): recolectando CV + CUL
PHASE_FINISHED = "finished"            # entrevista completa (a la espera de decisión de RR.HH.)
PHASE_SCHEDULING = "scheduling"        # RR.HH. continuó: coordinando horario de entrevista
PHASE_SCHEDULED = "scheduled"          # horario elegido / reunión agendada
PHASE_CLOSED = "closed"                # candidato declinó o abandonó


class QuestionSpec(TypedDict, total=False):
    question_id: str
    position: int
    text: str
    criterion: str
    weight: float
    max_follow_ups: int
    cv_field: Optional[str]   # campo del CV que la pregunta revalida (None = pregunta en frío)
    label: str               # etiqueta corta (vértice del radar / leyenda)


class AnswerRecord(TypedDict, total=False):
    question_id: str
    position: int
    text: str            # enunciado de la pregunta (para el scorecard)
    label: str           # etiqueta corta de la pregunta
    criterion: str
    weight: float
    raw_answer: str      # respuesta del candidato (acumulada con follow-ups)
    score: Optional[float]
    justification: str
    follow_up_count: int
    low_confidence: bool   # evaluación no confiable (fallo del LLM) → revisión humana


class InterviewState(TypedDict, total=False):
    # Configuración de la vacante (subset necesario para conducir la entrevista).
    vacancy: dict[str, Any]
    questions: list[QuestionSpec]
    # Perfil del CV del candidato (si vino por sourcing) — para revalidar sin repetir.
    cv_profile: dict[str, Any]

    # Progreso.
    phase: str
    consented: Optional[bool]
    current_idx: int             # índice de la pregunta actual (0-based)
    follow_ups_used: int         # follow-ups gastados en la pregunta actual
    questions_asked: int         # dudas del candidato respondidas en la pregunta actual
    current_answer_parts: list[str]  # partes de la respuesta actual (con follow-ups)
    answers: list[AnswerRecord]  # respuestas ya cerradas

    # Resultado.
    scorecard: Optional[dict[str, Any]]
    closed_reason: str           # motivo del cierre ("declined" | "no_response" | "")

    # Recolección de documentos (tras calificar).
    doc_idx: int                          # índice del documento que se está pidiendo
    save_document: Optional[dict[str, Any]]  # documento a persistir este turno (lo lee el servicio)

    # Agendamiento de entrevista (tras "Continuar" de RR.HH.). Multi-etapa:
    #   scheduling_stage = "hr" (Fase 1) | "lead" (Fase 2) | "manager" (Fase 3).
    proposed_slots: list[str]             # horarios propuestos (ISO 8601) al candidato
    meeting_slot: Optional[str]           # horario elegido (ISO) — lo lee el servicio para agendar
    slot_retries: int                     # intentos fallidos de elegir horario (corte → RR.HH.)
    recruiter: dict[str, Any]             # contacto RR.HH. que coordina/firma los mensajes
    scheduling_stage: str                 # etapa que se está coordinando ("hr" | "lead" | "manager")
    modality: str                         # "virtual" (Meet) | "onsite" (presencial)
    interviewer: dict[str, Any]           # persona entrevistada en esta etapa (líder / gerencia / RR.HH.)

    # Por turno (no durable a efectos prácticos; se reescribe cada vez):
    mode: str                    # "start" (primer contacto) | "turn" (procesar mensaje)
    outbound: list[str]          # mensajes a enviar al candidato este turno
    show_consent_buttons: bool   # el canal debe mostrar Acepto / No interesado
    pending_input: Optional[str]    # texto entrante a procesar
    pending_button: Optional[str]   # "accept" | "decline" (botón pulsado)
    pending_document: Optional[dict[str, Any]]  # documento entrante {file_id, filename, local_path}
    pending_timeout: bool           # cierre por inactividad (sin respuesta del candidato)


def new_state(
    vacancy: dict[str, Any],
    questions: list[QuestionSpec],
    cv_profile: Optional[dict[str, Any]] = None,
) -> InterviewState:
    """Estado inicial para una entrevista nueva."""
    return InterviewState(
        vacancy=vacancy,
        questions=questions,
        cv_profile=cv_profile or {},
        phase=PHASE_GREETING,
        consented=None,
        current_idx=0,
        follow_ups_used=0,
        questions_asked=0,
        current_answer_parts=[],
        answers=[],
        scorecard=None,
        closed_reason="",
        doc_idx=0,
        save_document=None,
        proposed_slots=[],
        meeting_slot=None,
        slot_retries=0,
        recruiter={},
        scheduling_stage="hr",
        modality="virtual",
        interviewer={},
        outbound=[],
        show_consent_buttons=False,
        pending_input=None,
        pending_button=None,
        pending_document=None,
        pending_timeout=False,
    )


def current_question(state: InterviewState) -> Optional[QuestionSpec]:
    qs = state.get("questions") or []
    idx = state.get("current_idx", 0)
    return qs[idx] if 0 <= idx < len(qs) else None
