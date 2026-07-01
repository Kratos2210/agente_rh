"""Lógica conversacional del agente de selección (pura, sin I/O ni persistencia).

`start` produce el primer contacto; `handle_turn` procesa un mensaje entrante y
avanza la entrevista. Ambas devuelven un estado nuevo con `outbound` (mensajes a
enviar este turno). La persistencia y el envío los hace el driver (LangGraph + canal).
"""

from __future__ import annotations

from datetime import datetime

from agent.llm import LLM
from agent.prompts import (
    CLOSING_DECLINED,
    CLOSING_DOCS_PENDING,
    CLOSING_GREETING_NO_RESPONSE,
    CLOSING_INACTIVITY,
    CLOSING_THANKS,
    DOC_RECEIVED,
    DOC_RETRY,
    DOC_SKIPPED,
    EMPTY_ANSWER_NUDGE,
    QUALIFIED_NEXT_STEPS,
    QUESTIONS_EXHAUSTED,
    REQUEST_DOC,
    SCHEDULING_BOOKING,
    SCHEDULING_ESCALATE,
    SCHEDULING_PICK_AGAIN,
    SCHEDULING_PROPOSAL,
    SCHEDULING_SESSION_LINES,
    progress_prefix,
    revalidation_question,
)
from agent.state import (
    PHASE_AWAITING_DOCS,
    PHASE_CLOSED,
    PHASE_FINISHED,
    PHASE_GREETING,
    PHASE_INTERVIEWING,
    PHASE_SCHEDULED,
    PHASE_SCHEDULING,
    AnswerRecord,
    InterviewState,
    current_question,
)
from evaluation.scorecard import build_scorecard
from evaluation.scorer import (
    answer_candidate_question,
    classify_turn,
    evaluate_answer,
    is_meaningful_answer,
    parse_slot_choice,
)
from integrations.scheduling import human_slot

_DECLINE_SIGNALS = ("no interesad", "no me interesa", "no deseo", "no quiero continuar", "cancelar")
_ACCEPT_SIGNALS = ("acepto", "si", "sí", "ok", "okay", "dale", "comenzar", "empezar", "claro", "listo")
_SKIP_SIGNALS = ("omitir", "saltar", "skip", "no tengo", "luego", "después", "despues", "más tarde", "mas tarde")

# Documentos que se piden (en orden) tras calificar: (tipo, etiqueta legible).
DOC_SEQUENCE = [("cv", "tu hoja de vida (CV)"), ("cul", "tu Certificado Único Laboral (CUL)")]

# Criterios de parada de los ciclos deliberativos (auditoría I1/I2): sin tope, cada vuelta
# cuesta llamadas LLM y el reloj de inactividad se reinicia con cada mensaje.
MAX_CANDIDATE_QUESTIONS = 3   # dudas respondidas por pregunta de la entrevista
MAX_SLOT_RETRIES = 3          # intentos fallidos de elegir horario antes de escalar a RR.HH.


def start(state: InterviewState) -> InterviewState:
    """Primer contacto: presenta el puesto y pide consentimiento (con botones)."""
    state = dict(state)
    intro = (state.get("vacancy") or {}).get("intro_message") or (
        "¡Hola! 👋 Nos gustaría hacerte unas preguntas para una vacante. ¿Deseas continuar?"
    )
    state["outbound"] = [intro]
    state["show_consent_buttons"] = True
    state["phase"] = PHASE_GREETING
    return state


def handle_turn(state: InterviewState, llm: LLM) -> InterviewState:
    """Procesa el mensaje entrante (`pending_input` / `pending_button`) y avanza."""
    state = dict(state)
    state["outbound"] = []
    state["show_consent_buttons"] = False
    state["save_document"] = None

    text = (state.get("pending_input") or "").strip()
    button = state.get("pending_button")
    document = state.get("pending_document")
    phase = state.get("phase")

    # Cierre por inactividad (sin respuesta del candidato): lo dispara el scheduler.
    if state.get("pending_timeout"):
        _handle_timeout(state, phase)
        state["pending_input"] = None
        state["pending_button"] = None
        state["pending_document"] = None
        state["pending_timeout"] = False
        return state

    # Documento entrante fuera de la etapa de documentos: se ignora con cortesía.
    if document and phase != PHASE_AWAITING_DOCS:
        if phase == PHASE_INTERVIEWING:
            state["outbound"].append("Gracias 🙌 Por ahora continuemos con la entrevista.")
        state["pending_input"] = None
        state["pending_button"] = None
        state["pending_document"] = None
        return state

    if phase == PHASE_GREETING:
        _handle_consent(state, text=text, button=button)
    elif phase == PHASE_AWAITING_DOCS:
        _handle_docs(state, text=text, button=button, document=document)
    elif phase == PHASE_INTERVIEWING:
        # Abandono explícito en cualquier momento.
        if _is_decline(text, button):
            state["phase"] = PHASE_CLOSED
            state["consented"] = False
            state["closed_reason"] = "declined"
            state["outbound"].append(CLOSING_DECLINED)
        else:
            _handle_interview(state, llm, text=text)
    elif phase == PHASE_SCHEDULING:
        if _is_decline(text, button):
            state["phase"] = PHASE_CLOSED
            state["consented"] = False
            state["closed_reason"] = "declined"
            state["outbound"].append(CLOSING_DECLINED)
        else:
            _handle_scheduling(state, llm, text=text)
    # PHASE_FINISHED / PHASE_SCHEDULED / PHASE_CLOSED: no se procesa nada más.

    state["pending_input"] = None
    state["pending_button"] = None
    state["pending_document"] = None
    return state


# ── Inactividad ───────────────────────────────────────────────────────────────

def _handle_timeout(state: InterviewState, phase: str | None) -> None:
    """Cierra la conversación por silencio del candidato (según la fase)."""
    if phase == PHASE_GREETING:
        # Nunca pulsó Acepto / No interesado: se cierra como "no respondió".
        state["phase"] = PHASE_CLOSED
        state["consented"] = False
        state["closed_reason"] = "no_response"
        state["outbound"].append(CLOSING_GREETING_NO_RESPONSE)
    elif phase == PHASE_INTERVIEWING:
        state["phase"] = PHASE_CLOSED
        state["closed_reason"] = "no_response"
        state["outbound"].append(CLOSING_INACTIVITY)
    elif phase == PHASE_AWAITING_DOCS:
        # La entrevista ya terminó y el scorecard ya está guardado: el negocio queda
        # "finished" y los documentos quedan pendientes (no se penaliza al candidato).
        state["phase"] = PHASE_FINISHED
        state["outbound"].append(CLOSING_DOCS_PENDING)
    elif phase == PHASE_SCHEDULING:
        # Inactividad coordinando horario: la maneja el servicio (recordatorio, sin cerrar).
        pass
    # Otras fases (greeting/finished/scheduled/closed): no aplica inactividad.


# ── Agendamiento de entrevista (coordinación de horario tras "Continuar") ─────────

def _slot_label(iso: str) -> str:
    try:
        return human_slot(datetime.fromisoformat(iso))
    except Exception:  # noqa: BLE001
        return iso


def _format_slot_options(slots: list[str]) -> str:
    return "\n".join(f"{i}. {_slot_label(s)}" for i, s in enumerate(slots, 1))


def start_scheduling(state: InterviewState) -> InterviewState:
    """Abre la coordinación: presenta al reclutador y propone los horarios (con número).

    Multi-etapa: la línea que describe la sesión depende de `scheduling_stage`
    ("hr" | "lead" | "manager") y de `modality` ("virtual" | "onsite")."""
    state = dict(state)
    state["outbound"] = []
    state["show_consent_buttons"] = False
    state["save_document"] = None
    slots = list(state.get("proposed_slots") or [])
    state["proposed_slots"] = slots
    state["meeting_slot"] = None
    state["slot_retries"] = 0
    state["phase"] = PHASE_SCHEDULING
    if not slots:
        state["outbound"].append(
            "Estamos coordinando la agenda para tu entrevista; te confirmo los horarios muy pronto. 🙌"
        )
        return state
    rec = state.get("recruiter") or {}
    interviewer = state.get("interviewer") or {}
    vac = state.get("vacancy") or {}
    stage = state.get("scheduling_stage") or "hr"
    modality = state.get("modality") or "virtual"
    name = str((state.get("cv_profile") or {}).get("name", "")).split(" ")[0]
    session_line = SCHEDULING_SESSION_LINES.get(
        (stage, modality), SCHEDULING_SESSION_LINES[("hr", "virtual")]
    ).format(
        vacancy_title=vac.get("title") or "la vacante",
        interviewer=interviewer.get("name") or "nuestro equipo",
    )
    state["outbound"].append(
        SCHEDULING_PROPOSAL.format(
            name=name or "",
            recruiter_name=rec.get("name") or "el equipo de Talento",
            company=rec.get("company") or "nuestra empresa",
            session_line=session_line,
            options=_format_slot_options(slots),
        )
    )
    return state


def _handle_scheduling(state: InterviewState, llm: LLM, *, text: str) -> None:
    slots = list(state.get("proposed_slots") or [])
    idx = parse_slot_choice(llm, [_slot_label(s) for s in slots], text)
    if idx is None or not (0 <= idx < len(slots)):
        # Criterio de parada: tras el tope de intentos, escala a RR.HH. en vez de
        # re-proponer indefinidamente (cada vuelta cuesta una llamada LLM). La fase
        # queda en `scheduling`: la reconciliación de "scheduling estancado" la vigila
        # y RR.HH. la retoma (reabrir horarios o coordinar directo).
        retries = state.get("slot_retries", 0) + 1
        state["slot_retries"] = retries
        if retries > MAX_SLOT_RETRIES:
            # Avisa el escalamiento una sola vez; después guarda silencio (una elección
            # válida tardía sigue agendando, porque el parseo corre antes de este corte).
            if retries == MAX_SLOT_RETRIES + 1:
                state["outbound"].append(SCHEDULING_ESCALATE)
            return
        state["outbound"].append(SCHEDULING_PICK_AGAIN.format(options=_format_slot_options(slots)))
        return
    # Elige el horario; el servicio crea la reunión y envía la confirmación con el enlace.
    state["meeting_slot"] = slots[idx]
    state["phase"] = PHASE_SCHEDULED
    state["outbound"].append(SCHEDULING_BOOKING)


# ── Consentimiento ─────────────────────────────────────────────────────────────

def _is_decline(text: str, button: str | None) -> bool:
    if button == "decline":
        return True
    low = text.lower()
    return any(sig in low for sig in _DECLINE_SIGNALS)


def _is_accept(text: str, button: str | None) -> bool:
    if button == "accept":
        return True
    return text.strip().lower() in _ACCEPT_SIGNALS


def _handle_consent(state: InterviewState, *, text: str, button: str | None) -> None:
    if _is_decline(text, button):
        state["consented"] = False
        state["phase"] = PHASE_CLOSED
        state["closed_reason"] = "declined"
        state["outbound"].append(CLOSING_DECLINED)
        return
    if _is_accept(text, button):
        state["consented"] = True
        state["phase"] = PHASE_INTERVIEWING
        state["current_idx"] = 0
        # Comparte el detalle del puesto y arranca con la primera pregunta (revalidación).
        details = (state.get("vacancy") or {}).get("details_message")
        if details:
            state["outbound"].append(details)
        _emit_question(state)
        return
    # Entrada ambigua en el saludo: no consentir; re-mostrar intro + botones.
    intro = (state.get("vacancy") or {}).get("intro_message") or (
        "¿Deseas iniciar la entrevista?"
    )
    state["outbound"].append(intro)
    state["show_consent_buttons"] = True


# ── Entrevista ──────────────────────────────────────────────────────────────────

def _cv_value(cv_profile: dict, cv_field: str | None) -> str:
    """Representación legible del dato del CV que revalida una pregunta."""
    if not cv_field or not cv_profile:
        return ""
    if cv_field == "education":
        edu = cv_profile.get("education") or {}
        parts = [str(edu.get("level", "")).strip(), str(edu.get("career", "")).strip()]
        return " en ".join(p for p in parts if p)
    if cv_field == "years_experience":
        yrs = cv_profile.get("years_experience")
        return f"{yrs} años de experiencia" if yrs not in (None, "") else ""
    if cv_field == "skills":
        skills = cv_profile.get("skills") or []
        return ", ".join(str(s) for s in skills)
    value = cv_profile.get(cv_field)
    return str(value).strip() if value not in (None, "") else ""


def _emit_question(state: InterviewState, *, ack: str = "") -> None:
    """Formula la pregunta actual con línea de progreso; reinicia acumuladores.

    Si la pregunta mapea a un campo del CV que el candidato ya declaró, la reformula
    como revalidación ("Según tu CV: «…»") en lugar de preguntarla en frío.
    """
    q = current_question(state)
    if q is None:
        return
    total = len(state.get("questions") or [])
    prefix = progress_prefix(q["position"], total)
    cv_value = _cv_value(state.get("cv_profile") or {}, q.get("cv_field"))
    qtext = revalidation_question(q["text"], cv_value) if cv_value else q["text"]
    body = f"{prefix}\n\n{qtext}"
    state["outbound"].append(f"{ack}\n\n{body}".strip() if ack else body)
    state["follow_ups_used"] = 0
    state["questions_asked"] = 0
    state["current_answer_parts"] = []


def _handle_interview(state: InterviewState, llm: LLM, *, text: str) -> None:
    q = current_question(state)
    if q is None:
        _finalize(state, llm)
        return

    # Respuesta vacía/trivial (espacios, símbolos o emoji): repreguntamos sin gastar un
    # follow-up ni llamar al LLM. Evita que un mensaje vacío avance con un puntaje al azar (#10).
    if not is_meaningful_answer(text):
        state["outbound"].append(EMPTY_ANSWER_NUDGE.format(question=q["text"]))
        return

    # ¿Responde o pregunta algo sobre el puesto?
    if classify_turn(llm, current_question=q["text"], message=text) == "question":
        # Criterio de parada: tras el tope de dudas por pregunta, se difiere al equipo
        # sin llamar al LLM (evita un ciclo de costo indefinido que además reinicia
        # el reloj de inactividad en cada vuelta).
        if state.get("questions_asked", 0) >= MAX_CANDIDATE_QUESTIONS:
            state["outbound"].append(QUESTIONS_EXHAUSTED.format(question=q["text"]))
            return
        state["questions_asked"] = state.get("questions_asked", 0) + 1
        company_info = (state.get("vacancy") or {}).get("company_info", "")
        reply = answer_candidate_question(llm, company_info=company_info, question=text)
        state["outbound"].append(reply)
        state["outbound"].append(f"Volviendo a lo nuestro:\n\n{q['text']}")
        return

    # Es una respuesta: la acumulamos (puede venir en varias partes con follow-ups).
    parts = list(state.get("current_answer_parts") or []) + [text]
    state["current_answer_parts"] = parts
    combined = "\n".join(parts)

    follow_ups_used = state.get("follow_ups_used", 0)
    can_follow = follow_ups_used < int(q.get("max_follow_ups", 0))
    result = evaluate_answer(
        llm,
        question=q["text"],
        criterion=q["criterion"],
        answer=combined,
        can_follow_up=can_follow,
        cv_context=_cv_value(state.get("cv_profile") or {}, q.get("cv_field")),
    )

    if result.needs_follow_up:
        state["follow_ups_used"] = follow_ups_used + 1
        state["outbound"].append(result.follow_up_question)
        return

    # Cierra la respuesta de esta pregunta.
    record: AnswerRecord = {
        "question_id": q["question_id"],
        "position": q["position"],
        "text": q["text"],
        "label": q.get("label", ""),
        "criterion": q["criterion"],
        "weight": float(q.get("weight", 1.0)),
        "raw_answer": combined,
        "score": result.score,
        "justification": result.justification,
        "follow_up_count": follow_ups_used,
        "low_confidence": result.low_confidence,
    }
    state["answers"] = list(state.get("answers") or []) + [record]

    next_idx = state.get("current_idx", 0) + 1
    state["current_idx"] = next_idx
    if next_idx < len(state.get("questions") or []):
        _emit_question(state, ack=result.ack)
    else:
        if result.ack:
            state["outbound"].append(result.ack)
        _finalize(state, llm)


def _finalize(state: InterviewState, llm: LLM) -> None:
    vac = state.get("vacancy") or {}
    thresholds = vac.get("semaphore_thresholds") or {}
    green_min = int(thresholds.get("green_min", 75))
    yellow_min = int(thresholds.get("yellow_min", 50))
    scorecard = build_scorecard(
        list(state.get("answers") or []),
        vacancy_title=vac.get("title", ""),
        green_min=green_min,
        yellow_min=yellow_min,
        llm=llm,
    )
    state["scorecard"] = scorecard
    # Si cumple con el perfil (semáforo verde), felicita y pide los documentos (CV + CUL).
    if scorecard.get("semaphore") == "green":
        name = str((state.get("cv_profile") or {}).get("name", "")).split(" ")[0]
        state["outbound"].append(QUALIFIED_NEXT_STEPS.format(name=name or "").replace("  ", " ").strip())
        state["phase"] = PHASE_AWAITING_DOCS
        state["doc_idx"] = 0
        state["outbound"].append(REQUEST_DOC.format(label=DOC_SEQUENCE[0][1]))
    else:
        state["phase"] = PHASE_FINISHED
        state["outbound"].append(CLOSING_THANKS)


# ── Recolección de documentos (CV + Certificado Único Laboral) ────────────────────

def _handle_docs(state: InterviewState, *, text: str, button: str | None, document: dict | None) -> None:
    """Recolecta los documentos en orden (DOC_SEQUENCE). Acepta el PDF o 'omitir'."""
    if _is_decline(text, button):
        state["phase"] = PHASE_CLOSED
        state["consented"] = False
        state["closed_reason"] = "declined"
        state["outbound"].append(CLOSING_DECLINED)
        return
    idx = state.get("doc_idx", 0)
    if idx >= len(DOC_SEQUENCE):
        _finish_docs(state)
        return
    doc_type, label = DOC_SEQUENCE[idx]
    if document:
        # El servicio persistirá este documento (con su tipo) tras el turno.
        state["save_document"] = {**document, "type": doc_type}
        state["outbound"].append(DOC_RECEIVED.format(label=label))
        _advance_doc(state)
    elif text.strip().lower() in _SKIP_SIGNALS:
        state["outbound"].append(DOC_SKIPPED.format(label=label))
        _advance_doc(state)
    else:
        state["outbound"].append(DOC_RETRY)


def _advance_doc(state: InterviewState) -> None:
    idx = state.get("doc_idx", 0) + 1
    state["doc_idx"] = idx
    if idx < len(DOC_SEQUENCE):
        state["outbound"].append(REQUEST_DOC.format(label=DOC_SEQUENCE[idx][1]))
    else:
        _finish_docs(state)


def _finish_docs(state: InterviewState) -> None:
    state["phase"] = PHASE_FINISHED
    state["outbound"].append(CLOSING_THANKS)
