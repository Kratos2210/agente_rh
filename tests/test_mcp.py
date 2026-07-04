"""Servidor MCP (api/mcp.py): gating por config, auth JWT, tenancy, RBAC y auditoría.

El mount `/mcp` es una sub-app ASGI (no APIRoute), así que el introspector de
`test_tenant_guards` no lo recorre — este archivo cubre explícitamente su perímetro:
  - default OFF: la app principal no expone /mcp;
  - sin Bearer (o inválido) → 401 antes de tocar el protocolo;
  - las tools corren con el tenant del token (el contextvar cruza al task del server);
  - RBAC: get_ops_alerts exige admin;
  - cada invocación queda en audit_log (action mcp.<tool>);
  - mutaciones (contact/decide): rol recruiter + confirmación en dos pasos — el preview
    no muta y emite un confirm_token HMAC ligado a la invocación; solo el token válido
    ejecuta (y un JWT de acceso jamás sirve de confirm_token).

Los requests son JSON-RPC crudos contra el transporte streamable HTTP en modo
stateless+json (no requiere handshake initialize).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db.repositories as db_repo
from api import auth
from core.config import get_settings

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

    from adaptadores_mcp.mcp import mount_mcp

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
        "contact_candidate", "decide_candidate",
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


# ── Mutaciones: confirmación en dos pasos ───────────────────────────────────────


def _mock_candidate(monkeypatch, status: str = "prescreen_passed", tenant_id: str = "t1"):
    monkeypatch.setattr(
        db_repo,
        "get_candidate",
        lambda cid: {"id": cid, "vacancy_id": "v1", "name": "Daniela", "status": status},
    )
    monkeypatch.setattr(db_repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": tenant_id})


def test_confirm_token_roundtrip_and_rejections():
    from adaptadores_mcp.mcp import CONFIRM_TTL_SECONDS, _issue_confirm_token, _verify_confirm_token

    user = {"id": "u1", "tenant_id": "t1"}
    tok = _issue_confirm_token(user, "decide_candidate", "c1", "reject", now=1000.0)
    ok = lambda **kw: _verify_confirm_token(tok, kw.pop("user", user), "decide_candidate", "c1", "reject", **kw)  # noqa: E731

    assert ok(now=1000.0 + CONFIRM_TTL_SECONDS)  # vigente hasta el TTL inclusive
    assert not ok(now=1000.0 + CONFIRM_TTL_SECONDS + 1)  # expirado
    assert not _verify_confirm_token(tok, user, "decide_candidate", "c1", "advance", now=1001.0)  # otra acción
    assert not _verify_confirm_token(tok, user, "decide_candidate", "c2", "reject", now=1001.0)  # otro candidato
    assert not _verify_confirm_token(tok, user, "contact_candidate", "c1", "reject", now=1001.0)  # otra tool
    assert not ok(user={"id": "u2", "tenant_id": "t1"}, now=1001.0)  # otro usuario
    assert not ok(user={"id": "u1", "tenant_id": "t2"}, now=1001.0)  # otro tenant
    assert not _verify_confirm_token(tok[:-4] + "AAAA", user, "decide_candidate", "c1", "reject", now=1001.0)  # adulterado
    assert not _verify_confirm_token("basura", user, "decide_candidate", "c1", "reject", now=1001.0)


def test_access_jwt_is_not_a_valid_confirm_token():
    """Un JWT de sesión robado no puede usarse como confirmación (clave derivada)."""
    from adaptadores_mcp.mcp import _verify_confirm_token

    user = {"id": "u1", "tenant_id": "t1"}
    jwt_token = _token(role="recruiter", user_id="u1", tenant_id="t1")
    assert not _verify_confirm_token(jwt_token, user, "contact_candidate", "c1")


def test_contact_preview_does_not_mutate_and_returns_token(mcp_client, monkeypatch):
    import api.routes.candidates as candidates

    _mock_candidate(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(candidates, "contact_candidate", lambda cid, user: calls.append(cid) or {"contacted": True})

    result = _call_tool(mcp_client, "contact_candidate", {"candidate_id": "c1"},
                        token=_token(role="recruiter"))
    assert not result.get("isError"), result
    preview = result["structuredContent"]
    assert preview["requires_confirmation"] is True
    # PII: el preview NO expone el nombre real, solo el seudónimo derivado del id.
    assert preview["candidate"]["name"] == "Candidato #c1"
    assert "Daniela" not in json.dumps(preview)
    assert preview["confirm_token"]
    assert calls == []  # el preview NO ejecutó la mutación


def test_contact_confirm_executes_via_route_impl(mcp_client, monkeypatch):
    import api.routes.candidates as candidates

    _mock_candidate(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(
        candidates, "contact_candidate",
        lambda cid, user: calls.append((cid, user["tenant_id"])) or {"contacted": True},
    )

    token = _token(role="recruiter")
    preview = _call_tool(mcp_client, "contact_candidate", {"candidate_id": "c1"},
                         token=token)["structuredContent"]
    result = _call_tool(
        mcp_client, "contact_candidate",
        {"candidate_id": "c1", "confirm_token": preview["confirm_token"]}, token=token,
    )
    assert not result.get("isError"), result
    assert result["structuredContent"] == {"contacted": True}
    assert calls == [("c1", "t1")]  # ejecutó el MISMO impl del dashboard, con el user del token


def test_contact_with_invalid_token_is_error(mcp_client, monkeypatch):
    _mock_candidate(monkeypatch)
    result = _call_tool(
        mcp_client, "contact_candidate",
        {"candidate_id": "c1", "confirm_token": "no-es-un-token"}, token=_token(role="recruiter"),
    )
    assert result.get("isError") is True
    assert "confirm_token" in result["content"][0]["text"]


def test_contact_preview_rejects_wrong_status(mcp_client, monkeypatch):
    _mock_candidate(monkeypatch, status="invited")
    result = _call_tool(mcp_client, "contact_candidate", {"candidate_id": "c1"},
                        token=_token(role="recruiter"))
    assert result.get("isError") is True
    assert "no está apto" in result["content"][0]["text"]


def test_mutations_require_recruiter_role(mcp_client, monkeypatch):
    _mock_candidate(monkeypatch)
    for name, args in (
        ("contact_candidate", {"candidate_id": "c1"}),
        ("decide_candidate", {"candidate_id": "c1", "decision": "reject"}),
    ):
        result = _call_tool(mcp_client, name, args, token=_token(role="viewer"))
        assert result.get("isError") is True
        assert "recruiter" in result["content"][0]["text"]


def test_mutations_cross_tenant_is_error(mcp_client, monkeypatch):
    _mock_candidate(monkeypatch, tenant_id="tenant-B")
    result = _call_tool(mcp_client, "decide_candidate",
                        {"candidate_id": "c1", "decision": "reject"},
                        token=_token(role="recruiter", tenant_id="tenant-A"))
    assert result.get("isError") is True


def test_decide_full_flow_with_audit(mcp_client, monkeypatch):
    import api.routes.candidates as candidates

    _mock_candidate(monkeypatch, status="interviewed")
    entries: list[dict] = []
    monkeypatch.setattr(db_repo, "add_audit_log", lambda entry: entries.append(entry))
    calls: list[str] = []
    monkeypatch.setattr(
        candidates, "decide_candidate",
        lambda cid, payload, user: calls.append(payload.decision) or {"status": "rejected"},
    )

    token = _token(role="recruiter")
    preview = _call_tool(mcp_client, "decide_candidate",
                         {"candidate_id": "c1", "decision": "reject"},
                         token=token)["structuredContent"]
    assert preview["requires_confirmation"] is True
    assert "rejected" in preview["effects"]
    assert calls == []

    # el token emitido para 'reject' NO sirve para 'advance'
    cross = _call_tool(mcp_client, "decide_candidate",
                       {"candidate_id": "c1", "decision": "advance",
                        "confirm_token": preview["confirm_token"]}, token=token)
    assert cross.get("isError") is True

    result = _call_tool(mcp_client, "decide_candidate",
                        {"candidate_id": "c1", "decision": "reject",
                         "confirm_token": preview["confirm_token"]}, token=token)
    assert not result.get("isError"), result
    assert result["structuredContent"] == {"status": "rejected"}
    assert calls == ["reject"]
    actions = [e["action"] for e in entries]
    assert "mcp.decide_candidate.preview" in actions and "mcp.decide_candidate" in actions


def test_decide_rejects_invalid_decision(mcp_client, monkeypatch):
    _mock_candidate(monkeypatch)
    result = _call_tool(mcp_client, "decide_candidate",
                        {"candidate_id": "c1", "decision": "hire"}, token=_token(role="recruiter"))
    assert result.get("isError") is True
    assert "advance" in result["content"][0]["text"]


# ── Minimización de PII (perfil "sin PII" para clientes externos, plan B) ────────


def test_pseudonym_is_stable_and_id_derived():
    from adaptadores_mcp.mcp import _pseudonym

    assert _pseudonym("c1") == "Candidato #c1"
    assert _pseudonym("0123456789abcdef") == "Candidato #01234567"  # cap a 8
    assert _pseudonym("") == "Candidato"
    assert _pseudonym(None) == "Candidato"


def test_mask_candidate_row_hides_name_keeps_operational():
    from adaptadores_mcp.mcp import _mask_candidate_row

    row = {"id": "abc123ff", "name": "Daniela Ramírez", "status": "invited",
           "semaphore": "green", "total_score": 92, "prescreen_score": 88}
    masked = _mask_candidate_row(row)
    assert masked["name"] == "Candidato #abc123ff"
    assert "Daniela" not in json.dumps(masked)
    # el valor operativo se conserva intacto
    assert masked["semaphore"] == "green" and masked["total_score"] == 92
    assert masked["prescreen_score"] == 88 and masked["status"] == "invited"


def test_mask_candidates_result_handles_items_and_list():
    from adaptadores_mcp.mcp import _mask_candidates_result

    paged = {"items": [{"id": "x1", "name": "Ana"}], "total": 1, "limit": 50, "offset": 0}
    out = _mask_candidates_result(paged)
    assert out["items"][0]["name"] == "Candidato #x1"
    assert out["total"] == 1 and out["limit"] == 50  # metadata intacta

    flat = [{"id": "y2", "name": "Beto"}]
    assert _mask_candidates_result(flat)[0]["name"] == "Candidato #y2"


def test_mask_candidate_detail_strips_all_pii():
    from adaptadores_mcp.mcp import _mask_candidate_detail

    detail = {
        "candidate": {
            "id": "cand9999", "name": "Daniela Ramírez", "status": "advanced",
            "cv_profile": {"email": "dani@mail.com", "phone": "+51999888777", "summary": "..."},
            "prescreen": {"pre_score": 92, "verdict": "pass",
                          "summary": "Daniela Ramírez cumple los requisitos del puesto"},
            "channel_user_id": "555",
            "documents": [{"type": "cv", "filename": "CV_Daniela_Ramirez.pdf", "stored": "db"}],
        },
        "scorecard": {"total_score": 92, "semaphore": "green",
                      "per_criterion": [{"label": "Experiencia", "score": 90}]},
        "transcript": [{"role": "user", "text": "Hola, soy Daniela, mi cel es 999888777"}],
        "meetings": [{"id": "m1", "stage": "hr", "modality": "virtual", "status": "scheduled",
                      "candidate_phone": "+51999888777", "recruiter_phone": "+51988",
                      "meet_link": "https://meet.example/x", "location": "Oficina Lima"}],
        "stage_feedback": [{"stage": "hr", "decision": "advance"}],
    }
    masked = _mask_candidate_detail(detail)
    blob = json.dumps(masked, ensure_ascii=False)

    # nada de PII de contacto ni CV ni nombre real ni transcripción
    assert "Daniela" not in blob
    assert "dani@mail.com" not in blob and "999888777" not in blob
    assert "CV_Daniela_Ramirez.pdf" not in blob
    assert "cv_profile" not in masked["candidate"]
    assert masked["candidate"]["name"] == "Candidato #cand9999"
    assert masked["candidate"]["cv_profile_present"] is True
    assert "channel_user_id" not in masked["candidate"]
    # prescreen: se conserva score+verdict, el summary (cita el nombre/CV) fuera
    assert masked["candidate"]["prescreen"] == {"pre_score": 92, "verdict": "pass"}
    assert masked["candidate"]["documents"] == [{"type": "cv", "stored": "db"}]
    assert "transcript" not in masked and masked["transcript_messages"] == 1

    # valor operativo intacto: scorecard, etapa, modalidad, estado, ubicación (empresa)
    assert masked["scorecard"]["total_score"] == 92
    m = masked["meetings"][0]
    assert m["stage"] == "hr" and m["status"] == "scheduled" and m["location"] == "Oficina Lima"
    assert "candidate_phone" not in m and "recruiter_phone" not in m
    assert masked["stage_feedback"] == [{"stage": "hr", "decision": "advance"}]


def test_mask_vacancy_minimizes_recruiter_contact():
    from adaptadores_mcp.mcp import _mask_vacancy

    v = {"id": "v1", "title": "Analista IA", "candidate_count": 3,
         "recruiter": {"id": "r1", "name": "Grace Mendieta", "role": "RR.HH.",
                       "email": "grace@sifrah.com", "phone": "+51900", "telegram_chat_id": "42"}}
    masked = _mask_vacancy(v)
    rec = masked["recruiter"]
    assert rec["name"] == "Grace Mendieta" and rec["role"] == "RR.HH."  # staff útil se conserva
    assert "email" not in rec and "phone" not in rec and "telegram_chat_id" not in rec
    assert masked["title"] == "Analista IA" and masked["candidate_count"] == 3
