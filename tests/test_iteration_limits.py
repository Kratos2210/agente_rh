"""Criterios de parada de los ciclos deliberativos + consistencia motor↔negocio.

Cubre los hallazgos de la auditoría e2e (2026-07-01):
  - I1: tope de dudas del candidato por pregunta (corte sin llamar al LLM).
  - I2: tope de reintentos al elegir horario (escalamiento a RR.HH., una sola vez).
  - M1: la retención purga también el checkpoint de LangGraph (PII en el estado).
  - G1: la reconciliación alerta divergencia entre la fase del motor y el negocio.
  - G2: la reunión se registra en la DB ANTES de crear el evento externo (no duplica).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from agent.graph import make_memory_runner
from agent.nodes import MAX_CANDIDATE_QUESTIONS, MAX_SLOT_RETRIES
from agent.state import PHASE_SCHEDULED, PHASE_SCHEDULING


class FakeLLM:
    """Clasifica todo como 'question', responde dudas y evalúa con score fijo."""

    def __init__(self, classify: str = "question"):
        self.kind = classify
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if '"kind"' in prompt:
            return json.dumps({"kind": self.kind})
        if '"choice"' in prompt:
            m = re.search(r"<<<respuesta>>>\n(.*?)\n<<<fin>>>", prompt, re.S)
            digits = [c for c in (m.group(1) if m else "") if c.isdigit()]
            return json.dumps({"choice": int(digits[0]) if digits else 0})
        if "needs_follow_up" in prompt:
            return json.dumps({"score": 80, "justification": "ok", "needs_follow_up": False,
                               "follow_up_question": "", "ack": "Gracias."})
        if "recommendation" in prompt:
            return json.dumps({"summary": "Resumen.", "recommendation": "Ok."})
        if "Información disponible sobre el puesto" in prompt:
            return "Es presencial en Surco."
        return "{}"

    def answer_calls(self) -> int:
        return sum(1 for p in self.calls if "Información disponible sobre el puesto" in p)


def _vacancy():
    return {
        "title": "Vacante demo",
        "intro_message": "Hola, ¿deseas continuar?",
        "company_info": "Empresa retail.",
        "semaphore_thresholds": {"green_min": 75, "yellow_min": 50},
    }


def _questions(n=2):
    return [
        {"question_id": f"q{i}", "position": i, "text": f"Pregunta {i}?",
         "criterion": f"c{i}", "weight": 1.0, "max_follow_ups": 0}
        for i in range(1, n + 1)
    ]


# ── I1: tope de dudas del candidato por pregunta ──────────────────────────────────

def test_candidate_questions_capped_per_question():
    llm = FakeLLM(classify="question")
    runner = make_memory_runner(llm)
    tid = "test:qcap"
    runner.start(tid, _vacancy(), _questions())
    runner.send(tid, button="accept")

    for i in range(MAX_CANDIDATE_QUESTIONS):
        s = runner.send(tid, text=f"¿duda {i}?")
        assert "Es presencial" in " ".join(s["outbound"])  # respondida
    assert llm.answer_calls() == MAX_CANDIDATE_QUESTIONS

    # La duda N+1 se corta: se difiere al equipo SIN llamar al LLM de respuesta.
    s = runner.send(tid, text="¿otra duda más?")
    joined = " ".join(s["outbound"])
    assert "siguiente etapa" in joined and "Pregunta 1?" in joined
    assert llm.answer_calls() == MAX_CANDIDATE_QUESTIONS  # no gastó otra llamada


def test_question_counter_resets_on_next_question():
    llm = FakeLLM(classify="question")
    runner = make_memory_runner(llm)
    tid = "test:qreset"
    runner.start(tid, _vacancy(), _questions())
    runner.send(tid, button="accept")

    for i in range(MAX_CANDIDATE_QUESTIONS):
        runner.send(tid, text=f"¿duda {i}?")
    # Responde la pregunta 1 → avanza a la 2 y el contador se reinicia.
    llm.kind = "answer"
    s = runner.send(tid, text="Mi respuesta con experiencia real.")
    assert s["questions_asked"] == 0
    llm.kind = "question"
    before = llm.answer_calls()
    s = runner.send(tid, text="¿una duda en la pregunta 2?")
    assert llm.answer_calls() == before + 1  # vuelve a responder dudas


# ── I2: tope de reintentos al elegir horario ──────────────────────────────────────

def test_slot_choice_escalates_after_retries():
    llm = FakeLLM()
    runner = make_memory_runner(llm)
    tid = "test:slotcap"
    slots = ["2026-07-06T10:00:00-05:00", "2026-07-06T11:00:00-05:00"]
    runner.send(tid, start_scheduling=slots, recruiter={"name": "Grace", "company": "SIFRAH"})

    for _ in range(MAX_SLOT_RETRIES):
        s = runner.send(tid, text="no sé todavía")
        assert s["phase"] == PHASE_SCHEDULING
        assert "número" in " ".join(s["outbound"]).lower()  # re-propone

    # Intento N+1: escala a RR.HH. (una sola vez); el siguiente guarda silencio.
    s = runner.send(tid, text="mmm no sé")
    assert "equipo de Talento" in " ".join(s["outbound"])
    s = runner.send(tid, text="sigo sin saber")
    assert s["outbound"] == []

    # Una elección válida tardía sigue agendando.
    s = runner.send(tid, text="la 2")
    assert s["phase"] == PHASE_SCHEDULED and s["meeting_slot"] == slots[1]


def test_slot_retries_reset_on_new_proposal():
    llm = FakeLLM()
    runner = make_memory_runner(llm)
    tid = "test:slotreset"
    slots = ["2026-07-06T10:00:00-05:00"]
    runner.send(tid, start_scheduling=slots, recruiter={})
    for _ in range(MAX_SLOT_RETRIES + 1):
        runner.send(tid, text="no puedo")
    # RR.HH. reabre horarios (reagendar): el contador arranca de cero.
    s = runner.send(tid, start_scheduling=slots, recruiter={})
    assert s["slot_retries"] == 0


# ── M1: la retención purga el checkpoint de LangGraph ─────────────────────────────

def test_retention_sweep_deletes_checkpoint(monkeypatch):
    from api import main

    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    cand = {"id": "c1", "vacancy_id": "v1", "name": "Ana", "cv_profile": {"email": "a@x.com"},
            "created_at": old}
    conv = {"id": "conv1", "langgraph_thread_id": "telegram:123"}
    deleted: dict[str, list] = {"checkpoints": [], "messages": [], "docs": [], "anon": []}

    monkeypatch.setattr(main.repo, "list_candidates_by_statuses", lambda statuses: [cand])
    monkeypatch.setattr(main.repo, "list_vacancies", lambda **kw: [{"id": "v1", "tenant_id": "t1"}])
    monkeypatch.setattr(main.repo, "get_app_setting", lambda k, d, tid=None: {"enabled": True, "days": 180})
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: conv)
    monkeypatch.setattr(main.repo, "delete_messages", lambda cid: deleted["messages"].append(cid))
    monkeypatch.setattr(main.repo, "delete_langgraph_checkpoint", lambda t: deleted["checkpoints"].append(t))
    monkeypatch.setattr(main.repo, "delete_candidate_documents", lambda cid: deleted["docs"].append(cid))
    monkeypatch.setattr(main.repo, "anonymize_candidate", lambda cid: deleted["anon"].append(cid))

    report = main._retention_sweep(settings=None)
    assert report["anonymized"] == 1
    assert deleted["checkpoints"] == ["telegram:123"]  # la PII del estado también se purga


# ── G1: la reconciliación alerta divergencia motor↔negocio ────────────────────────

def test_reconciliation_flags_state_divergence(monkeypatch):
    from api import main

    conv = {"id": "conv1", "state": "greeting", "langgraph_thread_id": "telegram:9",
            "vacancy_id": "v1", "last_activity_at": datetime.now(timezone.utc).isoformat()}

    class _Runner:
        def get_state(self, thread_id):
            return {"phase": "interviewing"}  # el motor ya avanzó; el negocio quedó atrás

    class _Service:
        runner = _Runner()

    monkeypatch.setattr(main.repo, "count_outbox_by_status", lambda tid=None: {})
    monkeypatch.setattr(main.repo, "list_meetings_without_link", lambda: [])
    monkeypatch.setattr(main.repo, "list_conversations_by_states",
                        lambda states: [conv] if conv["state"] in states else [])
    monkeypatch.setattr(main.repo, "get_meeting_by_conversation", lambda cid: None)
    monkeypatch.setitem(main._state, "service", _Service())
    try:
        report = main._reconciliation_sweep(settings=None)
    finally:
        main._state.pop("service", None)
    assert report["state_divergence"] == 1 and report["alerts"] == 1


# ── G2: registro-primero de la reunión (no duplica el evento externo) ─────────────

class _RecordingScheduler:
    def __init__(self, events: list, fail: bool = False):
        self.events = events
        self.fail = fail

    def create_meeting(self, **kw):
        self.events.append("create_meeting")
        if self.fail:
            raise RuntimeError("Calendar caído")
        from integrations.scheduling import MeetingResult

        return MeetingResult(start=kw["start"], end=kw["end"], meet_link="https://meet/x", event_id="ev1")

    def append_sheet_row(self, *a, **kw):
        return ""


def _run_finalize(monkeypatch, *, fail_calendar: bool):
    from agent import service as svc
    from agent.service import InterviewService

    events: list = []
    saved: dict = {}

    monkeypatch.setattr(svc.repositories, "get_meeting_by_conversation_stage", lambda c, s: None)

    def save_meeting(payload):
        events.append("save_meeting")
        saved.update(payload)
        return {**payload, "id": "m1"}

    def update_meeting(mid, payload):
        events.append("update_meeting")
        saved.update(payload)
        return {**saved, "id": mid}

    monkeypatch.setattr(svc.repositories, "save_meeting", save_meeting)
    monkeypatch.setattr(svc.repositories, "update_meeting", update_meeting)
    monkeypatch.setattr(svc.repositories, "get_recruiter", lambda rid: {})

    service = InterviewService(
        runner=None, scheduler=_RecordingScheduler(events, fail=fail_calendar), settings=None
    )
    state = {"phase": PHASE_SCHEDULED, "meeting_slot": "2026-07-06T10:00:00-05:00",
             "scheduling_stage": "hr", "modality": "virtual", "outbound": []}
    service._finalize_scheduling(
        {"id": "v1", "title": "Vacante", "meeting_duration_minutes": 45},
        {"id": "c1", "name": "Ana Pérez", "cv_profile": {"email": "a@x.com"}},
        {"id": "conv1"},
        state,
    )
    return events, saved, state


def test_meeting_saved_before_external_event(monkeypatch):
    events, saved, state = _run_finalize(monkeypatch, fail_calendar=False)
    assert events.index("save_meeting") < events.index("create_meeting")
    assert saved["meet_link"] == "https://meet/x" and saved["event_id"] == "ev1"
    assert any("agendada" in m for m in state["outbound"])  # confirmación al candidato


def test_meeting_persisted_even_if_calendar_fails(monkeypatch):
    events, saved, state = _run_finalize(monkeypatch, fail_calendar=True)
    assert "save_meeting" in events and "update_meeting" not in events
    assert saved["meet_link"] == ""  # queda visible para la reconciliación (sin link)
    assert saved["status"] == "scheduled"
    assert state["outbound"]  # igual se confirma el horario al candidato
