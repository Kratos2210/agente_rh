"""Fase 0 — auth + RBAC + aislamiento por tenant + fallback del scheduler.

Cubre lo verificable sin una base de datos viva:
  - Funciones puras: hash/verify de contraseñas, round-trip/expiración/tamper del JWT,
    jerarquía de roles.
  - Endpoints (FastAPI TestClient, sin arrancar el lifespan → sin DB/bot): 401 sin
    token, 403 por rol insuficiente, /api/auth/me con token, /api/health público.
  - Helpers de tenant (con monkeypatch de los repos): aislamiento cross-tenant → 404.
  - Scheduler: sin DATABASE_URL cae al modo sin-lock (un solo proceso).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api.main as main
from api import auth
from core.config import Settings, get_settings


# ── Funciones puras ───────────────────────────────────────────────────────────

def test_password_hash_roundtrip():
    h = auth.hash_password("s3cr3t-pass")
    assert h != "s3cr3t-pass"                 # nunca se guarda en claro
    assert auth.verify_password("s3cr3t-pass", h)
    assert not auth.verify_password("otra", h)


def test_verify_password_bad_hash_no_raise():
    assert auth.verify_password("x", "no-es-un-hash") is False


def test_jwt_roundtrip():
    s = Settings(jwt_secret="a" * 40, jwt_expire_minutes=60)
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role="recruiter", tenant_id="t1", settings=s
    )
    claims = auth.decode_access_token(tok, s)
    assert claims["sub"] == "u1"
    assert claims["role"] == "recruiter"
    assert claims["tenant_id"] == "t1"


def test_jwt_wrong_secret_rejected():
    s = Settings(jwt_secret="a" * 40)
    tok = auth.create_access_token(user_id="u", email="e", role="admin", tenant_id="t", settings=s)
    with pytest.raises(pyjwt.PyJWTError):
        auth.decode_access_token(tok, Settings(jwt_secret="b" * 40))


def test_jwt_expired_rejected():
    s = Settings(jwt_secret="a" * 40, jwt_expire_minutes=-1)  # ya expirado
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    tok = auth.create_access_token(
        user_id="u", email="e", role="admin", tenant_id="t", settings=s, now=past
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        auth.decode_access_token(tok, s)


def test_role_hierarchy():
    assert auth.role_allows("admin", "admin")
    assert auth.role_allows("admin", "recruiter")
    assert auth.role_allows("admin", "viewer")
    assert auth.role_allows("recruiter", "viewer")
    assert not auth.role_allows("recruiter", "admin")
    assert not auth.role_allows("viewer", "recruiter")
    assert not auth.role_allows("desconocido", "viewer")


# ── Endpoints (TestClient sin lifespan → sin DB) ──────────────────────────────

client = TestClient(main.app)


def _token(role: str, tenant_id: str = "t1") -> str:
    return auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, tenant_id)}"}


def test_health_is_public():
    assert client.get("/api/health").status_code == 200


def test_protected_endpoint_requires_token():
    assert client.get("/api/vacancies").status_code == 401
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/recruiters").status_code == 401


def test_bad_token_is_401():
    r = client.get("/api/vacancies", headers={"Authorization": "Bearer no-es-un-jwt"})
    assert r.status_code == 401


def test_me_returns_claims():
    r = client.get("/api/auth/me", headers=_auth("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin"
    assert body["tenant_id"] == "t1"


def test_viewer_cannot_mutate_settings():
    # PUT de settings exige admin: un viewer recibe 403 antes de tocar la DB.
    r = client.put("/api/settings/scheduling", json={}, headers=_auth("viewer"))
    assert r.status_code == 403


def test_recruiter_cannot_manage_recruiters():
    # Crear reclutadores exige admin.
    r = client.post("/api/recruiters", json={"name": "X"}, headers=_auth("recruiter"))
    assert r.status_code == 403


def test_viewer_cannot_create_vacancy():
    r = client.post("/api/vacancies", json={"title": "X"}, headers=_auth("viewer"))
    assert r.status_code == 403


# ── Aislamiento por tenant (monkeypatch de los repos) ─────────────────────────

def test_vacancy_tenant_isolation(monkeypatch):
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "OWNER"})
    user_ok = {"tenant_id": "OWNER"}
    user_other = {"tenant_id": "INTRUSO"}
    assert main._require_vacancy_in_tenant("v1", user_ok)["id"] == "v1"
    with pytest.raises(HTTPException) as e:
        main._require_vacancy_in_tenant("v1", user_other)
    assert e.value.status_code == 404


def test_candidate_tenant_isolation(monkeypatch):
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: {"id": cid, "vacancy_id": "v1"})
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "OWNER"})
    cand, vac = main._require_candidate_in_tenant("c1", {"tenant_id": "OWNER"})
    assert cand["id"] == "c1" and vac["tenant_id"] == "OWNER"
    with pytest.raises(HTTPException) as e:
        main._require_candidate_in_tenant("c1", {"tenant_id": "INTRUSO"})
    assert e.value.status_code == 404


# ── Scheduler: fallback sin DATABASE_URL ──────────────────────────────────────

def test_scheduler_runs_without_database_url(monkeypatch):
    monkeypatch.setitem(main._state, "settings", Settings(database_url=""))
    # Sin DATABASE_URL no hay lock distribuido → el proceso ejecuta el trabajo (True).
    assert asyncio.run(main._ensure_scheduler_lock()) is True
