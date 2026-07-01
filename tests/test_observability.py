"""Fase 3 — Observabilidad: salud del outbox + auditoría en el dashboard.

Endpoints admin (`GET /api/outbox`, `POST /api/outbox/{id}/retry`) con TestClient
(sin lifespan → sin DB): RBAC, aislamiento por tenant y reencolado (monkeypatch de repos).
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
from fastapi.testclient import TestClient
from src.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


# ── RBAC ──────────────────────────────────────────────────────────────────────

def test_outbox_requires_admin():
    assert client.get("/api/outbox").status_code == 401
    assert client.get("/api/outbox", headers=_auth("recruiter")).status_code == 403


def test_outbox_health_is_tenant_scoped(monkeypatch):
    seen: dict[str, str] = {}

    def _counts(tenant_id=None):
        seen["counts"] = tenant_id
        return {"pending": 1, "failed": 2, "sent": 5}

    def _list(tenant_id, statuses=None, limit=100):
        seen["list"] = tenant_id
        return [{"id": "o1", "kind": "scorecard_email", "status": "failed", "attempts": 6}]

    monkeypatch.setattr(main.repo, "count_outbox_by_status", _counts)
    monkeypatch.setattr(main.repo, "list_outbox", _list)

    r = client.get("/api/outbox", headers=_auth("admin", "TENANT_X"))
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["failed"] == 2
    assert body["items"][0]["id"] == "o1"
    # Ambas consultas se aislaron al tenant del token.
    assert seen == {"counts": "TENANT_X", "list": "TENANT_X"}


# ── Reintento (dead-letter → pending) ─────────────────────────────────────────

def test_retry_requeues_row(monkeypatch):
    updated: dict[str, dict] = {}
    monkeypatch.setattr(
        main.repo, "get_outbox",
        lambda oid: {"id": oid, "tenant_id": "t1", "status": "failed", "kind": "candidate_notify"},
    )
    monkeypatch.setattr(main.repo, "update_outbox", lambda oid, payload: updated.setdefault(oid, payload))
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    r = client.post("/api/outbox/o9/retry", headers=_auth("admin"))
    assert r.status_code == 200 and r.json()["requeued"] is True
    assert updated["o9"]["status"] == "pending"
    assert "next_attempt_at" in updated["o9"]


def test_retry_other_tenant_is_404(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_outbox",
        lambda oid: {"id": oid, "tenant_id": "OWNER", "status": "failed"},
    )
    r = client.post("/api/outbox/o9/retry", headers=_auth("admin", "INTRUSO"))
    assert r.status_code == 404


def test_retry_sent_is_409(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_outbox",
        lambda oid: {"id": oid, "tenant_id": "t1", "status": "sent"},
    )
    r = client.post("/api/outbox/o9/retry", headers=_auth("admin"))
    assert r.status_code == 409
