"""Proceso de selección multi-etapa (Fases 2 y 3): motor stage-aware, backend
presencial/virtual y endpoints de asistencia + decisión de etapa.

Motor/scheduler: helpers puros + grafo con checkpointer en memoria (sin Supabase).
Endpoints: TestClient (sin lifespan → sin DB), con monkeypatch de repos/servicio.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import api.auth as auth
import api.main as main
from agent.graph import make_memory_runner
from agent.state import PHASE_SCHEDULED, PHASE_SCHEDULING
from fastapi.testclient import TestClient
from integrations.scheduling import SimulatedScheduler, _tz, compute_free_slots
from notifications.email import build_psych_exam_email
from src.config import get_settings

client = TestClient(main.app)

_CFG = {
    "slot_minutes": 60, "work_days": [1, 2, 3, 4, 5],
    "work_start": "09:00", "work_end": "11:00",
    "timezone": "America/Lima", "horizon_days": 7, "options": 3,
}


class FakeLLM:
    def complete(self, prompt: str) -> str:
        if '"choice"' in prompt:
            # La respuesta va entre delimitadores anti-inyección (audit S1).
            m = re.search(r"<<<respuesta>>>\n(.*?)\n<<<fin>>>", prompt, re.S)
            digits = [c for c in (m.group(1) if m else "") if c.isdigit()]
            return json.dumps({"choice": int(digits[0]) if digits else 0})
        return "{}"


def _monday_8am():
    return datetime(2026, 6, 22, 8, 0, tzinfo=_tz("America/Lima"))


def _slots():
    return [s.isoformat() for s in compute_free_slots([], _CFG, now=_monday_8am())]


# ── Motor: agendamiento stage-aware ───────────────────────────────────────────────

def test_engine_lead_onsite_proposal_and_state():
    """La etapa 'lead' presencial menciona al líder y arrastra stage/modality al estado."""
    runner = make_memory_runner(FakeLLM())
    tid = "test:lead"
    s0 = runner.send(
        tid, start_scheduling=_slots(), recruiter={"name": "Grace", "company": "SIFRAH"},
        stage="lead", modality="onsite", interviewer={"name": "Christian Benites"},
    )
    assert s0["phase"] == PHASE_SCHEDULING
    assert s0["scheduling_stage"] == "lead" and s0["modality"] == "onsite"
    joined = " ".join(s0["outbound"]).lower()
    assert "christian benites" in joined and "presencial" in joined

    s1 = runner.send(tid, text="la 2 por favor")
    assert s1["phase"] == PHASE_SCHEDULED and s1["meeting_slot"] == _slots()[1]
    # La etapa/modalidad persisten en el checkpoint tras la elección.
    assert s1["scheduling_stage"] == "lead" and s1["modality"] == "onsite"


def test_engine_manager_proposal_mentions_gerencia():
    runner = make_memory_runner(FakeLLM())
    s0 = runner.send(
        "test:mgr", start_scheduling=_slots(), recruiter={"name": "Grace", "company": "SIFRAH"},
        stage="manager", modality="onsite", interviewer={"name": "Gerencia"},
    )
    assert "gerencia" in " ".join(s0["outbound"]).lower()


# ── Scheduler: presencial vs virtual ───────────────────────────────────────────────

def test_simulated_onsite_has_no_meet_link(tmp_path):
    backend = SimulatedScheduler(sheet_path=tmp_path / "m.csv")
    start = _monday_8am() + timedelta(hours=2)
    onsite = backend.create_meeting(
        calendar_id="primary", summary="x", start=start, end=start + timedelta(minutes=45),
        attendees=[], modality="onsite", location="Av. El Derby 254",
    )
    assert onsite.meet_link == ""
    virtual = backend.create_meeting(
        calendar_id="primary", summary="x", start=start, end=start + timedelta(minutes=45),
        attendees=[], modality="virtual",
    )
    assert virtual.meet_link.startswith("https://meet.google.com/sim-")


# ── Correo de exámenes psicológicos ────────────────────────────────────────────────

class _MailSettings:
    smtp_host = "smtp.test"; smtp_from = "rrhh@x.com"; smtp_port = 587
    smtp_user = ""; smtp_password = ""; recruiter_email = "rec@x.com"


def test_psych_exam_email_includes_credentials():
    cand = {"name": "Luis Alberto", "cv_profile": {"email": "luis@x.com"}}
    built = build_psych_exam_email(_MailSettings(), {"title": "Analista"}, cand,
                                   {"link": "http://ev.co/x", "code": "465", "key": "642"})
    assert built is not None
    recipients, subject, text, html = built
    assert recipients == ["luis@x.com"]
    assert "465" in text and "642" in text and "http://ev.co/x" in text
    assert "465" in html


def test_psych_exam_email_none_without_candidate_email():
    cand = {"name": "X", "cv_profile": {}}  # sin email
    assert build_psych_exam_email(_MailSettings(), {"title": "A"}, cand, {"link": "x"}) is None


# ── Endpoints (TestClient + monkeypatch de repos) ──────────────────────────────────

def _auth(role: str = "recruiter", tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


class _FakeService:
    """Registra la última llamada a initiate_scheduling y devuelve mensajes."""
    def __init__(self):
        self.calls = []

    def initiate_scheduling(self, candidate, vacancy, *, stage="hr", modality="virtual"):
        self.calls.append({"stage": stage, "modality": modality})
        return type("R", (), {"messages": ["propuesta de horarios"]})()


def _patch_candidate(monkeypatch, *, tenant_id="t1", status="scheduled"):
    cand = {"id": "cand1", "vacancy_id": "v1", "name": "Luis", "channel": "telegram",
            "channel_user_id": "sim-x", "status": status}
    vac = {"id": "v1", "title": "Analista", "tenant_id": tenant_id}
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: cand)
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: vac)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: {"id": "conv1"})
    return cand, vac


def test_endpoints_require_recruiter(monkeypatch):
    _patch_candidate(monkeypatch)
    for path, body in [
        ("/api/candidates/cand1/psych-exam", {"link": "x"}),
        ("/api/candidates/cand1/attendance", {"stage": "lead", "attended": "attended"}),
        ("/api/candidates/cand1/advance-stage", {"stage": "hr", "decision": "approved"}),
    ]:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=_auth("viewer")).status_code == 403


def test_advance_stage_reject_notifies_and_closes(monkeypatch):
    _patch_candidate(monkeypatch)
    saved, updated = {}, {}
    monkeypatch.setattr(main.repo, "save_stage_feedback", lambda row: saved.update(row) or row)
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify", lambda *a, **k: True)

    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "lead", "decision": "rejected", "feedback": "no encaja"},
                    headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert updated["status"] == "rejected"
    assert saved["decision"] == "rejected" and saved["feedback"] == "no encaja"


def test_advance_stage_hr_approve_schedules_lead(monkeypatch):
    _patch_candidate(monkeypatch, status="scheduled")
    monkeypatch.setattr(main.repo, "save_stage_feedback", lambda row: row)
    monkeypatch.setattr(main.repo, "get_app_setting", lambda *a, **k: {"enabled": True})
    fake = _FakeService()
    monkeypatch.setitem(main._state, "service", fake)

    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "hr", "decision": "approved", "modality": "onsite"},
                    headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "lead_scheduling" and body["scheduling_started"] is True
    assert fake.calls == [{"stage": "lead", "modality": "onsite"}]


def test_advance_stage_lead_approve_forces_manager_onsite(monkeypatch):
    _patch_candidate(monkeypatch, status="lead_scheduled")
    monkeypatch.setattr(main.repo, "save_stage_feedback", lambda row: row)
    monkeypatch.setattr(main.repo, "get_app_setting", lambda *a, **k: {"enabled": True})
    fake = _FakeService()
    monkeypatch.setitem(main._state, "service", fake)

    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "lead", "decision": "approved", "modality": "virtual"},
                    headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "mgr_scheduling"
    # Gerencia siempre presencial, aunque se envíe "virtual".
    assert fake.calls == [{"stage": "manager", "modality": "onsite"}]


def test_advance_stage_manager_approve_hires(monkeypatch):
    _patch_candidate(monkeypatch, status="mgr_scheduled")
    monkeypatch.setattr(main.repo, "save_stage_feedback", lambda row: row)
    updated = {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    notified = {}
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify",
                        lambda s, c, decision, **k: notified.setdefault("decision", decision) or True)

    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "manager", "decision": "approved"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "hired"
    assert updated["status"] == "hired"
    assert notified["decision"] == "hired"


def test_attendance_no_show_reschedule_reopens(monkeypatch):
    _patch_candidate(monkeypatch, status="lead_scheduled")
    monkeypatch.setattr(main.repo, "get_meeting_by_conversation_stage",
                        lambda cid, stage: {"id": "m1", "modality": "onsite"})
    marked = {}
    monkeypatch.setattr(main.repo, "set_meeting_attendance", lambda mid, a: marked.setdefault("a", a))
    fake = _FakeService()
    monkeypatch.setitem(main._state, "service", fake)

    r = client.post("/api/candidates/cand1/attendance",
                    json={"stage": "lead", "attended": "no_show", "reschedule": True}, headers=_auth())
    assert r.status_code == 200 and r.json()["rescheduled"] is True
    assert marked["a"] == "no_show"
    assert fake.calls == [{"stage": "lead", "modality": "onsite"}]  # reabre la misma etapa/modalidad


def test_attendance_no_show_close_marks_no_show(monkeypatch):
    _patch_candidate(monkeypatch, status="lead_scheduled")
    monkeypatch.setattr(main.repo, "get_meeting_by_conversation_stage",
                        lambda cid, stage: {"id": "m1", "modality": "onsite"})
    monkeypatch.setattr(main.repo, "set_meeting_attendance", lambda mid, a: a)
    updated = {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify", lambda *a, **k: True)

    r = client.post("/api/candidates/cand1/attendance",
                    json={"stage": "lead", "attended": "no_show", "reschedule": False}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "no_show"
    assert updated["status"] == "no_show"


def test_candidate_in_other_tenant_is_404(monkeypatch):
    _patch_candidate(monkeypatch, tenant_id="OWNER")
    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "hr", "decision": "rejected"}, headers=_auth("recruiter", "INTRUSO"))
    assert r.status_code == 404
