"""Examen médico pre-contratación (auditoría v3, Parte B).

Endpoints con TestClient + monkeypatch de repos (sin DB): gate por-tenant en
advance-stage, guards de estado e idempotencia, resultado inmutable, transiciones
apto→hired / no_apto→rejected, enmascarado del resultado para viewer (dato de salud)
y settings por-tenant.
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
from api.routes.candidates import _medical_exam_for_role
from fastapi.testclient import TestClient
from core.config import get_settings

client = TestClient(main.app)

_CITA = {"clinic": "Clínica Internacional", "scheduled_at": "lunes 13/07, 9:00 am",
         "address": "Av. Garcilaso 100, Lima", "instructions": "Ayuno de 8 horas"}


def _auth(role: str = "recruiter", tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


def _patch_candidate(monkeypatch, *, tenant_id="t1", status="medical_pending", medical_exam=None):
    cand = {"id": "cand1", "vacancy_id": "v1", "name": "Luis", "channel": "telegram",
            "channel_user_id": "123456", "status": status, "medical_exam": medical_exam,
            "cv_profile": {"email": "luis@x.com"}}
    vac = {"id": "v1", "title": "Analista", "tenant_id": tenant_id}
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: cand)
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: vac)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: {"id": "conv1"})
    return cand, vac


# ── Rechazo cierra la conversación (no más recordatorios de documentos) ────────────

def test_reject_closes_conversation(monkeypatch):
    # Bug: tras rechazar, el barrido de inactividad seguía pidiendo documentos porque la
    # conversación quedaba en awaiting_docs. Ahora el rechazo la cierra (estado terminal).
    _patch_candidate(monkeypatch, status="awaiting_docs")
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify", lambda *a, **k: True)
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate",
                        lambda cid: {"id": "conv1", "state": "awaiting_docs"})
    captured: dict = {}
    monkeypatch.setattr(main.repo, "update_conversation",
                        lambda cid, p: captured.update({"id": cid, **p}) or p)

    r = client.post("/api/candidates/cand1/decision", json={"decision": "reject"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert captured["id"] == "conv1" and captured["state"] == "closed"


def test_reject_no_conversation_does_not_fail(monkeypatch):
    # Sin conversación: el cierre es best-effort y no rompe el rechazo.
    _patch_candidate(monkeypatch, status="finished")
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify", lambda *a, **k: True)
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: None)
    r = client.post("/api/candidates/cand1/decision", json={"decision": "reject"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "rejected"


# ── advance-stage: gate config-gated por tenant ────────────────────────────────────

def test_manager_approve_with_medical_enabled_goes_to_medical_pending(monkeypatch):
    _patch_candidate(monkeypatch, status="mgr_scheduled")
    monkeypatch.setattr(main.repo, "save_stage_feedback", lambda row: row)
    monkeypatch.setattr(main.repo, "get_app_setting", lambda *a, **k: {"enabled": True})
    updated, sent = {}, {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_text",
                        lambda s, c, text, **k: sent.setdefault("text", text) or True)

    r = client.post("/api/candidates/cand1/advance-stage",
                    json={"stage": "manager", "decision": "approved"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "medical_pending"
    assert updated["status"] == "medical_pending"
    assert "examen médico" in sent["text"].lower()


# ── POST /medical-exam: guards + idempotencia + notificación dual ─────────────────

def test_medical_exam_requires_medical_phase(monkeypatch):
    _patch_candidate(monkeypatch, status="mgr_scheduled")  # aún no aprobó gerencia
    r = client.post("/api/candidates/cand1/medical-exam", json=_CITA, headers=_auth())
    assert r.status_code == 409


def test_medical_exam_schedules_and_notifies_both_channels(monkeypatch):
    _patch_candidate(monkeypatch, status="medical_pending")
    updated, mail, tg = {}, {}, {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_medical_exam",
                        lambda s, v, c, exam, **k: (mail.setdefault("exam", exam), True)[1])
    monkeypatch.setattr(main.outbox, "deliver_candidate_text",
                        lambda s, c, text, **k: (tg.setdefault("text", text), True)[1])

    r = client.post("/api/candidates/cand1/medical-exam", json=_CITA, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "medical_scheduled"
    assert body["email_sent"] is True and body["telegram_sent"] is True
    assert updated["status"] == "medical_scheduled"
    assert updated["medical_exam"]["clinic"] == _CITA["clinic"]
    assert updated["medical_exam"]["sent_by"] == "a@b.com"
    # El Telegram lleva la cita completa (clínica + fecha + indicaciones).
    assert _CITA["clinic"] in tg["text"] and _CITA["scheduled_at"] in tg["text"]


def test_medical_exam_same_payload_is_409_but_new_date_reprograms(monkeypatch):
    prev = {**_CITA, "sent_at": "2026-07-05T00:00:00", "sent_by": "a@b.com"}
    _patch_candidate(monkeypatch, status="medical_scheduled", medical_exam=prev)
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(main.outbox, "deliver_medical_exam", lambda *a, **k: True)
    monkeypatch.setattr(main.outbox, "deliver_candidate_text", lambda *a, **k: True)

    # Doble click con la MISMA cita → 409.
    assert client.post("/api/candidates/cand1/medical-exam", json=_CITA, headers=_auth()).status_code == 409
    # Cita distinta (reprogramar) → 200.
    r = client.post("/api/candidates/cand1/medical-exam",
                    json={**_CITA, "scheduled_at": "martes 14/07, 10:00 am"}, headers=_auth())
    assert r.status_code == 200


# ── POST /medical-result: inmutable + transiciones ─────────────────────────────────

def _scheduled_exam():
    return {**_CITA, "sent_at": "2026-07-05T00:00:00", "sent_by": "a@b.com"}


def test_medical_result_apto_hires_and_notifies(monkeypatch):
    _patch_candidate(monkeypatch, status="medical_scheduled", medical_exam=_scheduled_exam())
    updated, notified = {}, {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify",
                        lambda s, c, decision, **k: notified.setdefault("decision", decision) or True)

    r = client.post("/api/candidates/cand1/medical-result",
                    json={"result": "apto"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "hired"
    assert updated["status"] == "hired"
    assert updated["medical_exam"]["result"] == "apto"
    assert updated["medical_exam"]["result_by"] == "a@b.com"
    assert notified["decision"] == "hired"


def test_medical_result_no_apto_rejects(monkeypatch):
    _patch_candidate(monkeypatch, status="medical_scheduled", medical_exam=_scheduled_exam())
    updated = {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_candidate_notify", lambda *a, **k: True)

    r = client.post("/api/candidates/cand1/medical-result",
                    json={"result": "no_apto", "notes": "observado"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert updated["status"] == "rejected"
    assert updated["medical_exam"]["result_notes"] == "observado"


def test_medical_result_is_immutable(monkeypatch):
    exam = {**_scheduled_exam(), "result": "apto", "result_at": "2026-07-06T00:00:00"}
    _patch_candidate(monkeypatch, status="medical_scheduled", medical_exam=exam)
    r = client.post("/api/candidates/cand1/medical-result",
                    json={"result": "no_apto"}, headers=_auth())
    assert r.status_code == 409


def test_medical_result_requires_scheduled_exam(monkeypatch):
    _patch_candidate(monkeypatch, status="medical_pending", medical_exam=None)
    r = client.post("/api/candidates/cand1/medical-result",
                    json={"result": "apto"}, headers=_auth())
    assert r.status_code == 409


def test_medical_result_invalid_value_is_422(monkeypatch):
    _patch_candidate(monkeypatch, status="medical_scheduled", medical_exam=_scheduled_exam())
    r = client.post("/api/candidates/cand1/medical-result",
                    json={"result": "quizas"}, headers=_auth())
    assert r.status_code == 422


# ── RBAC + tenant ──────────────────────────────────────────────────────────────────

def test_medical_endpoints_require_recruiter(monkeypatch):
    _patch_candidate(monkeypatch)
    for path, body in [
        ("/api/candidates/cand1/medical-exam", _CITA),
        ("/api/candidates/cand1/medical-result", {"result": "apto"}),
        ("/api/candidates/cand1/start-date", {"start_date": "2026-07-13"}),
        ("/api/candidates/cand1/onboarding", None),
    ]:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=_auth("viewer")).status_code == 403


def test_medical_exam_cross_tenant_is_404(monkeypatch):
    _patch_candidate(monkeypatch, tenant_id="OWNER")
    r = client.post("/api/candidates/cand1/medical-exam", json=_CITA,
                    headers=_auth("recruiter", "INTRUSO"))
    assert r.status_code == 404


# ── Enmascarado del resultado para viewer (dato de salud, patrón S3) ──────────────

def test_medical_masking_hides_result_for_viewer():
    exam = {**_scheduled_exam(), "result": "no_apto", "result_notes": "detalle clínico"}
    masked = _medical_exam_for_role(exam, "viewer")
    assert masked["result"] == "•••" and masked["result_notes"] == "•••"
    # La cita (logística, no clínica) sigue visible para coordinar.
    assert masked["clinic"] == _CITA["clinic"]
    # recruiter/admin ven todo.
    assert _medical_exam_for_role(exam, "recruiter")["result"] == "no_apto"
    assert _medical_exam_for_role(None, "viewer") is None


# ── Settings por-tenant ────────────────────────────────────────────────────────────

def test_medical_settings_get_put(monkeypatch):
    stored = {}
    monkeypatch.setattr(main.repo, "get_app_setting",
                        lambda key, default, tid: stored.get((tid, key), default))
    monkeypatch.setattr(main.repo, "set_app_setting",
                        lambda key, value, tid: stored.update({(tid, key): value}) or value)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    # Default: apagado.
    r = client.get("/api/settings/medical-exam", headers=_auth("viewer"))
    assert r.status_code == 200 and r.json() == {"enabled": False}
    # PUT es admin-only.
    assert client.put("/api/settings/medical-exam", json={"enabled": True},
                      headers=_auth("recruiter")).status_code == 403
    r = client.put("/api/settings/medical-exam", json={"enabled": True}, headers=_auth("admin"))
    assert r.status_code == 200 and r.json() == {"enabled": True}
    # Aislado por tenant: otro tenant sigue en default.
    r = client.get("/api/settings/medical-exam", headers=_auth("viewer", "OTRO"))
    assert r.json() == {"enabled": False}
