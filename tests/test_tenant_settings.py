"""Fase 0.1 — settings POR-TENANT.

Verifica que (1) los endpoints de configuración leen/escriben con el `tenant_id` del token,
(2) el resolver de config por-tenant de los barridos aísla y cachea por empresa, y
(3) `_vacancy_tenant_map` mapea vacante→tenant.
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
from fastapi.testclient import TestClient
from core.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


# ── Endpoints: aislados por el tenant del token ───────────────────────────────

def test_get_settings_reads_own_tenant(monkeypatch):
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(
        main.repo, "get_app_setting",
        lambda key, default=None, tenant_id=None: seen.update({key: tenant_id}) or default,
    )
    r = client.get("/api/settings/auto-contact", headers=_auth("admin", "TENANT_A"))
    assert r.status_code == 200
    assert seen["auto_contact"] == "TENANT_A"


def test_put_settings_writes_own_tenant(monkeypatch):
    written: dict[str, tuple] = {}
    monkeypatch.setattr(
        main.repo, "set_app_setting",
        lambda key, value, tenant_id: written.update({key: (value, tenant_id)}),
    )
    monkeypatch.setattr(main.repo, "get_app_setting", lambda key, default=None, tenant_id=None: default)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    r = client.put(
        "/api/settings/inactivity",
        json={"enabled": True, "reminder_minutes": 3, "max_reminders": 1},
        headers=_auth("admin", "TENANT_B"),
    )
    assert r.status_code == 200
    value, tenant = written["inactivity"]
    assert tenant == "TENANT_B"
    assert value["reminder_minutes"] == 3


# ── Resolver por-tenant de los barridos ───────────────────────────────────────

def test_tenant_cfg_resolver_isolates_and_caches(monkeypatch):
    calls: list[str | None] = []

    def _get(key, default=None, tenant_id=None):
        calls.append(tenant_id)
        return {"enabled": tenant_id == "on"}

    monkeypatch.setattr(main.repo, "get_app_setting", _get)
    cfg_for = main._tenant_cfg_resolver("inactivity", main._DEFAULT_INACTIVITY)

    assert cfg_for("on")["enabled"] is True
    assert cfg_for("off")["enabled"] is False
    assert cfg_for("on")["enabled"] is True  # segunda vez: desde caché
    assert calls == ["on", "off"]  # una sola lectura por tenant


def test_vacancy_tenant_map(monkeypatch):
    monkeypatch.setattr(
        main.repo, "list_vacancies",
        lambda *a, **k: [{"id": "v1", "tenant_id": "t1"}, {"id": "v2", "tenant_id": "t2"}],
    )
    m = main._vacancy_tenant_map()
    assert m == {"v1": "t1", "v2": "t2"}
