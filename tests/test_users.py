"""Roadmap v2 · paso 4 — Gestión de usuarios (2.º operador).

Endpoints admin `GET/POST /api/users` + `PATCH /api/users/{id}` con TestClient
(sin lifespan → sin DB): RBAC, enmascarado del hash, unicidad de email, aislamiento
por tenant y guardas anti auto-bloqueo (monkeypatch de repos).
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
import pytest
from fastapi.testclient import TestClient
from src.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1", user_id: str = "admin1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id=user_id, email="admin@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def _clear_revocation_cache():
    # El caché de revocación es proceso-local; limpiarlo evita fugas entre tests.
    auth._revocation_cache.clear()
    yield
    auth._revocation_cache.clear()


# ── RBAC ──────────────────────────────────────────────────────────────────────

def test_users_requires_admin():
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/users", headers=_auth("recruiter")).status_code == 403
    assert client.get("/api/users", headers=_auth("viewer")).status_code == 403


# ── Listado: enmascara el hash y se aísla por tenant ──────────────────────────

def test_list_users_masks_password_and_scopes_tenant(monkeypatch):
    seen = {}

    def _list(tenant_id=None):
        seen["tenant"] = tenant_id
        return [{"id": "u1", "email": "a@b.com", "role": "viewer", "active": True, "password_hash": "SECRET"}]

    monkeypatch.setattr(main.repo, "list_users", _list)
    r = client.get("/api/users", headers=_auth("admin", "TENANT_X"))
    assert r.status_code == 200
    body = r.json()
    assert seen["tenant"] == "TENANT_X"
    assert "password_hash" not in body[0] and body[0]["email"] == "a@b.com"


# ── Alta ──────────────────────────────────────────────────────────────────────

def test_create_user_hashes_password_and_audits(monkeypatch):
    created_payload = {}
    monkeypatch.setattr(main.repo, "get_user_by_email", lambda email: None)

    def _create(payload):
        created_payload.update(payload)
        return {**payload, "id": "new1"}

    monkeypatch.setattr(main.repo, "create_user", _create)
    audited = {}
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: audited.update(row) or row)

    r = client.post(
        "/api/users",
        headers=_auth("admin", "TENANT_X"),
        json={"email": "Ops@Empresa.com", "password": "s3cretaza", "role": "viewer", "name": "Ops"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "password_hash" not in body
    # El hash guardado NO es la contraseña en claro y verifica con bcrypt.
    assert created_payload["password_hash"] != "s3cretaza"
    assert auth.verify_password("s3cretaza", created_payload["password_hash"])
    assert created_payload["email"] == "ops@empresa.com"      # normalizado
    assert created_payload["tenant_id"] == "TENANT_X"          # tenant del token
    assert audited["action"] == "user.create"


def test_create_duplicate_email_is_409(monkeypatch):
    monkeypatch.setattr(main.repo, "get_user_by_email", lambda email: {"id": "x", "email": email})
    r = client.post(
        "/api/users", headers=_auth("admin"),
        json={"email": "dup@b.com", "password": "s3cretaza", "role": "viewer"},
    )
    assert r.status_code == 409


def test_create_invalid_role_and_short_password(monkeypatch):
    monkeypatch.setattr(main.repo, "get_user_by_email", lambda email: None)
    bad_role = client.post(
        "/api/users", headers=_auth("admin"),
        json={"email": "a@b.com", "password": "s3cretaza", "role": "root"},
    )
    assert bad_role.status_code == 422
    short_pwd = client.post(
        "/api/users", headers=_auth("admin"),
        json={"email": "a@b.com", "password": "123", "role": "viewer"},
    )
    assert short_pwd.status_code == 422


# ── Actualización / desactivación ─────────────────────────────────────────────

def test_patch_deactivates_user(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_user",
        lambda uid: {"id": uid, "tenant_id": "t1", "email": "op@b.com", "role": "viewer", "active": True},
    )
    updated = {}
    monkeypatch.setattr(main.repo, "update_user", lambda uid, fields: updated.update({"id": uid, **fields}) or {"id": uid, **fields})
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    r = client.patch("/api/users/op9", headers=_auth("admin", "t1"), json={"active": False})
    assert r.status_code == 200
    assert updated["active"] is False and updated["id"] == "op9"


def test_patch_other_tenant_is_404(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_user",
        lambda uid: {"id": uid, "tenant_id": "OWNER", "role": "viewer", "active": True},
    )
    r = client.patch("/api/users/op9", headers=_auth("admin", "INTRUSO"), json={"active": False})
    assert r.status_code == 404


def test_patch_self_deactivate_blocked(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_user",
        lambda uid: {"id": uid, "tenant_id": "t1", "role": "admin", "active": True},
    )
    # El token y el objetivo son el mismo usuario (admin1).
    r = client.patch("/api/users/admin1", headers=_auth("admin", "t1", user_id="admin1"), json={"active": False})
    assert r.status_code == 400


def test_patch_self_demote_blocked(monkeypatch):
    monkeypatch.setattr(
        main.repo, "get_user",
        lambda uid: {"id": uid, "tenant_id": "t1", "role": "admin", "active": True},
    )
    r = client.patch("/api/users/admin1", headers=_auth("admin", "t1", user_id="admin1"), json={"role": "viewer"})
    assert r.status_code == 400
