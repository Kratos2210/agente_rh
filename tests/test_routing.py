"""Routing multi-tenant del bot (auditoría A1): deep-links t.me/<bot>?start=<vacancy_id>.

Service-level con runner fake y `db.repositories` monkeypatcheado (sin DB ni LLM).
Orden de resolución probado: conversación existente (sticky) → payload del deep-link
(válido/abierta, si no aviso sin crear candidato) → vacante default (retrocompat).
"""

from __future__ import annotations

from agente import service as svc
from agente.prompts import NO_OPEN_VACANCY, VACANCY_UNAVAILABLE
from agente.service import InterviewService
from channels.base import InboundMessage

UUID_A = "11111111-1111-1111-1111-111111111111"  # vacante default (tenant 1)
UUID_B = "22222222-2222-2222-2222-222222222222"  # vacante deep-link (tenant 2)
UUID_X = "99999999-9999-9999-9999-999999999999"  # no existe


class _FakeRunner:
    """Runner mínimo: sin estado previo (primer contacto) salvo que se inyecte."""

    llm = None

    def __init__(self, state: dict | None = None):
        self._state = state or {}
        self.started_with: dict | None = None

    def get_state(self, thread_id):
        return dict(self._state)

    def start(self, thread_id, vacancy, questions, cv_profile=None):
        self.started_with = {"vacancy": vacancy, "questions": questions}
        return {"phase": "greeting", "outbound": ["¡Hola!"], "show_consent_buttons": True}

    def send(self, thread_id, **kw):
        return {"phase": "interviewing", "outbound": ["Siguiente pregunta"]}


def _patch_repo(monkeypatch, *, vacancies: dict[str, dict], conversation: dict | None = None,
                candidate: dict | None = None):
    """Fakea el repo; devuelve un dict `calls` que registra las escrituras."""
    calls: dict = {"created_candidates": [], "created_convs": []}
    repo = svc.repositories
    open_ones = [v for v in vacancies.values() if v.get("status") == "open"]
    monkeypatch.setattr(repo, "get_conversation_by_thread", lambda t: conversation)
    monkeypatch.setattr(repo, "get_candidate", lambda cid: candidate)
    monkeypatch.setattr(repo, "get_vacancy", lambda vid: vacancies.get(vid))
    monkeypatch.setattr(repo, "get_default_open_vacancy", lambda: (open_ones[0] if open_ones else None))
    monkeypatch.setattr(repo, "get_vacancy_questions", lambda vid: [])

    def get_or_create_candidate(vacancy_id, channel, chat_id, name="", **kw):
        cand = {"id": "cand-new", "vacancy_id": vacancy_id, "channel": channel,
                "channel_user_id": chat_id, "cv_profile": {}}
        calls["created_candidates"].append(cand)
        return cand

    def get_or_create_conversation(candidate_id, vacancy_id, thread_id):
        conv = {"id": "conv-new", "candidate_id": candidate_id, "vacancy_id": vacancy_id}
        calls["created_convs"].append(conv)
        return conv

    monkeypatch.setattr(repo, "get_or_create_candidate", get_or_create_candidate)
    monkeypatch.setattr(repo, "get_or_create_conversation", get_or_create_conversation)
    monkeypatch.setattr(repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(repo, "update_conversation", lambda cid, p: p)
    monkeypatch.setattr(repo, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(repo, "upsert_answer", lambda *a, **k: None)
    monkeypatch.setattr(repo, "get_scorecard", lambda cid: None)
    return calls


def _vacancy(vid: str, *, status: str = "open", tenant: str = "t1") -> dict:
    return {"id": vid, "status": status, "tenant_id": tenant, "title": f"Vacante {vid[:2]}"}


def _inbound(payload: str | None = None) -> InboundMessage:
    return InboundMessage(channel="telegram", chat_id="777", text=None, start_payload=payload)


def test_deep_link_routes_to_that_vacancy(monkeypatch):
    """El payload gana sobre la vacante default: el candidato queda en LA vacante del aviso."""
    vacs = {UUID_A: _vacancy(UUID_A), UUID_B: _vacancy(UUID_B, tenant="t2")}
    calls = _patch_repo(monkeypatch, vacancies=vacs)
    runner = _FakeRunner()
    result = InterviewService(runner).process(_inbound(UUID_B))
    assert result.messages == ["¡Hola!"]
    assert calls["created_candidates"][0]["vacancy_id"] == UUID_B
    assert calls["created_convs"][0]["vacancy_id"] == UUID_B


def test_deep_link_unknown_vacancy_says_unavailable(monkeypatch):
    calls = _patch_repo(monkeypatch, vacancies={UUID_A: _vacancy(UUID_A)})
    result = InterviewService(_FakeRunner()).process(_inbound(UUID_X))
    assert result.messages == [VACANCY_UNAVAILABLE]
    assert not calls["created_candidates"]  # no engancha al candidato a otra vacante


def test_deep_link_closed_vacancy_says_unavailable(monkeypatch):
    vacs = {UUID_A: _vacancy(UUID_A), UUID_B: _vacancy(UUID_B, status="closed")}
    calls = _patch_repo(monkeypatch, vacancies=vacs)
    result = InterviewService(_FakeRunner()).process(_inbound(UUID_B))
    assert result.messages == [VACANCY_UNAVAILABLE]
    assert not calls["created_candidates"]


def test_malformed_payload_never_hits_db(monkeypatch):
    """Payload no-UUID (basura/inyección) se corta antes de consultar la DB."""
    calls = _patch_repo(monkeypatch, vacancies={UUID_A: _vacancy(UUID_A)})
    monkeypatch.setattr(
        svc.repositories, "get_vacancy",
        lambda vid: (_ for _ in ()).throw(AssertionError("no debe consultar la DB")),
    )
    result = InterviewService(_FakeRunner()).process(_inbound("'; drop table--"))
    assert result.messages == [VACANCY_UNAVAILABLE]
    assert not calls["created_candidates"]


def test_no_payload_falls_back_to_default(monkeypatch):
    calls = _patch_repo(monkeypatch, vacancies={UUID_A: _vacancy(UUID_A)})
    result = InterviewService(_FakeRunner()).process(_inbound(None))
    assert result.messages == ["¡Hola!"]
    assert calls["created_candidates"][0]["vacancy_id"] == UUID_A


def test_no_open_vacancy_message(monkeypatch):
    _patch_repo(monkeypatch, vacancies={UUID_A: _vacancy(UUID_A, status="closed")})
    result = InterviewService(_FakeRunner()).process(_inbound(None))
    assert result.messages == [NO_OPEN_VACANCY]


def test_existing_conversation_is_sticky(monkeypatch):
    """Mensaje a mitad de proceso: usa SU vacante aunque el deep-link apunte a otra."""
    conv = {"id": "conv-1", "candidate_id": "cand-1", "vacancy_id": UUID_B}
    cand = {"id": "cand-1", "vacancy_id": UUID_B, "cv_profile": {}}
    vacs = {UUID_A: _vacancy(UUID_A), UUID_B: _vacancy(UUID_B, tenant="t2")}
    calls = _patch_repo(monkeypatch, vacancies=vacs, conversation=conv, candidate=cand)
    runner = _FakeRunner(state={"phase": "interviewing"})
    result = InterviewService(runner).process(_inbound(UUID_A))
    assert result.messages == ["Siguiente pregunta"]
    assert not calls["created_candidates"]  # no crea un candidato duplicado en otra vacante
    assert not calls["created_convs"]


# ── Decisión terminal ya tomada: un mensaje del candidato no reabre el proceso ─────

class _NoSendRunner(_FakeRunner):
    def send(self, *a, **k):
        raise AssertionError("no debe reprocesar un candidato con decisión terminal")


def _terminal_conv(monkeypatch, status: str):
    conv = {"id": "conv1", "candidate_id": "cand1", "vacancy_id": UUID_A}
    cand = {"id": "cand1", "vacancy_id": UUID_A, "status": status, "cv_profile": {}}
    _patch_repo(monkeypatch, vacancies={UUID_A: _vacancy(UUID_A)}, conversation=conv, candidate=cand)
    recorded: list = []
    monkeypatch.setattr(svc.repositories, "add_message",
                        lambda cid, role, content: recorded.append((role, content)))
    return recorded


def test_rejected_candidate_message_does_not_reopen(monkeypatch):
    from agente.prompts import PROCESS_CLOSED_ACK

    recorded = _terminal_conv(monkeypatch, "rejected")
    result = InterviewService(_NoSendRunner()).process(
        InboundMessage(channel="telegram", chat_id="777", text="como va el proceso?")
    )
    assert result.messages == [PROCESS_CLOSED_ACK]
    # La transcripción sí registra el mensaje del candidato + el acuse (no se pierde).
    assert ("user", "como va el proceso?") in recorded
    assert ("assistant", PROCESS_CLOSED_ACK) in recorded


def test_hired_candidate_message_gets_warm_ack(monkeypatch):
    from agente.prompts import PROCESS_HIRED_ACK

    _terminal_conv(monkeypatch, "hired")
    result = InterviewService(_NoSendRunner()).process(
        InboundMessage(channel="telegram", chat_id="777", text="gracias!")
    )
    assert result.messages == [PROCESS_HIRED_ACK]


def test_medical_phase_message_does_not_revert_status(monkeypatch):
    """BUG demo: el checkpoint sigue en gerencia; reprocesar un mensaje durante la fase
    médica re-sincronizaba status='mgr_scheduled' y rompía los endpoints médicos (409)."""
    from agente.prompts import PROCESS_MEDICAL_ACK

    for status in ("medical_pending", "medical_scheduled"):
        recorded = _terminal_conv(monkeypatch, status)
        updates: list = []
        monkeypatch.setattr(svc.repositories, "update_candidate",
                            lambda cid, data: updates.append(data))
        result = InterviewService(_NoSendRunner()).process(
            InboundMessage(channel="telegram", chat_id="777", text="gracias, ahi estare")
        )
        assert result.messages == [PROCESS_MEDICAL_ACK]
        assert not updates  # el status médico queda intacto
        assert ("user", "gracias, ahi estare") in recorded
        assert ("assistant", PROCESS_MEDICAL_ACK) in recorded
