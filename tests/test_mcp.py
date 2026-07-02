"""Servidor MCP (api/mcp.py): gating por config, auth JWT, tenancy, RBAC y auditoría.

El mount `/mcp` es una sub-app ASGI (no APIRoute), así que el introspector de
`test_tenant_guards` no lo recorre — este archivo cubre explícitamente su perímetro:
  - default OFF: la app principal no expone /mcp;
  - sin Bearer (o inválido) → 401 antes de tocar el protocolo;
  - las tools corren con el tenant del token (el contextvar cruza al task del server);
  - RBAC: get_ops_alerts exige admin;
  - cada invocación queda en audit_log (action mcp.<tool>).

Los requests son JSON-RPC crudos contra el transporte streamable HTTP en modo
stateless+json (no requiere handshake initialize).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db.repositories as db_repo
from api import auth
from src.config import get_settings

_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _token(role: str = "viewer", user_id: str = "u1", tenant_id: str = "t1") -> str:
    return auth.create_access_token(
        user_id=user_id, email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )


def _rpc(client: TestClient, method: str, params: dict | None = None, *, token: str | None = None):
    headers = dict(_ACCEPT)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post("/mcp/", json=payload, headers=headers)


def _call_tool(client: TestClient, name: str, arguments: dict | None = None, *, token: str):
    r = _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


@pytest.fixture()
def mcp_client(monkeypatch):
    """App de prueba con el mount /mcp + session manager corriendo (via lifespan)."""
    auth._revocation_cache.clear()
    monkeypatch.setattr(db_repo, "get_user", lambda uid: {"id": uid, "active": True})
    monkeypatch.setattr(db_repo, "add_audit_log", lambda entry: None)

    from api.mcp import mount_mcp

    server = None

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    server = mount_mcp(app)
    with TestClient(app) as client:
        yield client
    auth._revocation_cache.clear()


# ── Gating por config ───────────────────────────────────────────────────────────


def test_mcp_disabled_by_default():
    import api.main as main

    assert get_settings().mcp_enabled is False
    client = TestClient(main.app)
    r = client.post("/mcp/", json={}, headers=_ACCEPT)
    assert r.status_code == 404  # sin MCP_ENABLED no existe el mount


# ── Perímetro de auth ───────────────────────────────────────────────────────────


def test_mcp_requires_bearer_token(mcp_client):
    r = _rpc(mcp_client, "tools/list")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_mcp_rejects_invalid_token(mcp_client):
    r = _rpc(mcp_client, "tools/list", token="no-es-un-jwt")
    assert r.status_code == 401


def test_mcp_rejects_revoked_user(mcp_client, monkeypatch):
    monkeypatch.setattr(db_repo, "get_user", lambda uid: {"id": uid, "active": False})
    r = _rpc(mcp_client, "tools/list", token=_token(user_id="revocado"))
    assert r.status_code == 401


def test_tools_list_with_valid_token(mcp_client):
    r = _rpc(mcp_client, "tools/list", token=_token())
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {
        "list_vacancies", "list_candidates", "get_candidate_detail", "get_metrics", "get_ops_alerts",
    }


# ── Tenancy: la tool corre con el tenant del token ─────────────────────────────


def test_list_vacancies_scoped_to_token_tenant(mcp_client, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_list_vacancies(status=None, tenant_id=None):
        seen["tenant_id"] = tenant_id
        return [{"id": "v1", "title": "Analista", "tenant_id": tenant_id, "recruiter_id": None}]

    monkeypatch.setattr(db_repo, "list_vacancies", fake_list_vacancies)
    monkeypatch.setattr(db_repo, "list_recruiters", lambda tenant_id=None: [])
    monkeypatch.setattr(db_repo, "count_candidates_by_status", lambda ids: {"v1": {"invited": 2}})

    result = _call_tool(mcp_client, "list_vacancies", token=_token(tenant_id="acme"))
    assert not result.get("isError")
    assert seen["tenant_id"] == "acme"  # el filtro viene del TOKEN, no de un parámetro
    rows = result["structuredContent"]["result"]
    assert rows[0]["candidate_count"] == 2


def test_candidate_detail_cross_tenant_is_error(mcp_client, monkeypatch):
    monkeypatch.setattr(db_repo, "get_candidate", lambda cid: {"id": cid, "vacancy_id": "v-b"})
    monkeypatch.setattr(db_repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "tenant-B"})

    result = _call_tool(mcp_client, "get_candidate_detail", {"candidate_id": "c1"},
                        token=_token(tenant_id="tenant-A"))
    assert result.get("isError") is True  # guard de tenant (404) → error de tool, sin datos


# ── RBAC ────────────────────────────────────────────────────────────────────────


def test_ops_alerts_requires_admin(mcp_client, monkeypatch):
    result = _call_tool(mcp_client, "get_ops_alerts", token=_token(role="recruiter"))
    assert result.get("isError") is True
    assert "admin" in result["content"][0]["text"]

    import api.routes.observability as observability

    monkeypatch.setattr(observability, "_collect_ops_alerts", lambda tid=None: [])
    result = _call_tool(mcp_client, "get_ops_alerts", token=_token(role="admin"))
    assert not result.get("isError")
    assert result["structuredContent"] == {"alerts": []}


# ── Auditoría ───────────────────────────────────────────────────────────────────


def test_tool_invocations_are_audited(mcp_client, monkeypatch):
    entries: list[dict] = []
    monkeypatch.setattr(db_repo, "add_audit_log", lambda entry: entries.append(entry))
    monkeypatch.setattr(db_repo, "list_vacancies", lambda status=None, tenant_id=None: [])
    monkeypatch.setattr(db_repo, "list_recruiters", lambda tenant_id=None: [])
    monkeypatch.setattr(db_repo, "count_candidates_by_status", lambda ids: {})

    _call_tool(mcp_client, "list_vacancies", token=_token(tenant_id="t1"))
    assert entries and entries[0]["action"] == "mcp.list_vacancies"
    assert entries[0]["tenant_id"] == "t1"
