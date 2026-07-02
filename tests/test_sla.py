"""Fase O-4 — SLAs push: barrido por tenant con dedupe una-por-condición-por-día,
umbral de latencia p95 del turno (últimas 24 h) y endpoints de settings."""

from __future__ import annotations

import api.auth as auth
import api.main as main
import api.scheduler as scheduler
from fastapi.testclient import TestClient
from src.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


def _reset_sweep_state():
    scheduler._state.pop("sla_sweep_last", None)
    scheduler._state.pop("sla_alerted", None)


# ── Helpers puros ─────────────────────────────────────────────────────────────

def test_sla_breaches_groups_ops_alerts_and_excludes_budget():
    ops = [
        {"type": "dead_letter", "detail": "2 envío(s) en dead-letter."},
        {"type": "scheduling_stuck", "detail": "conv A estancada."},
        {"type": "scheduling_stuck", "detail": "conv B estancada."},
        {"type": "budget_exceeded", "detail": "presupuesto al 90%."},  # lo empuja _budget_sweep
    ]
    cfg = {"ops_alerts": True, "turn_p95_ms": 0}
    breaches = dict(scheduler._sla_breaches(cfg, ops, None))
    assert set(breaches) == {"dead_letter", "scheduling_stuck"}
    assert "(+1 más)" in breaches["scheduling_stuck"]


def test_sla_breaches_turn_p95_threshold():
    cfg = {"ops_alerts": False, "turn_p95_ms": 3000}
    assert scheduler._sla_breaches(cfg, [], 2999) == []
    over = scheduler._sla_breaches(cfg, [], 4500)
    assert over[0][0] == "turn_p95" and "4500 ms" in over[0][1]
    # Sin muestras (tenant sin turnos en 24 h) no hay incumplimiento.
    assert scheduler._sla_breaches(cfg, [], None) == []


def test_tenant_turn_p95_only_turn_rows_per_tenant(monkeypatch):
    rows = [
        {"vacancy_id": "v1", "stage": "turn", "calls": 1, "duration_ms": 1000},
        {"vacancy_id": "v1", "stage": "turn", "calls": 1, "duration_ms": 5000},
        {"vacancy_id": "v2", "stage": "turn", "calls": 1, "duration_ms": 200},
        {"vacancy_id": "v1", "stage": "evaluate", "calls": 1, "duration_ms": 9999},  # no es turno
    ]
    monkeypatch.setattr(scheduler.repo, "usage_rows_since", lambda since: rows)
    monkeypatch.setattr(scheduler, "_vacancy_tenant_map", lambda: {"v1": "t1", "v2": "t2"})
    p95s = scheduler._tenant_turn_p95()
    assert p95s == {"t1": 5000, "t2": 200}


# ── Barrido: dedupe por condición/día + correo por outbox ─────────────────────

def _patch_sla_env(monkeypatch, *, cfgs: dict, ops_by_tenant: dict, turn_rows=None):
    monkeypatch.setattr(scheduler.repo, "list_tenants", lambda: [{"id": t} for t in cfgs])
    monkeypatch.setattr(
        scheduler.repo, "get_app_setting",
        lambda key, default, tenant_id=None: cfgs.get(tenant_id, default) if key == "sla_alerts" else default,
    )
    monkeypatch.setattr(scheduler, "_collect_ops_alerts", lambda tid=None: ops_by_tenant.get(tid, []))
    monkeypatch.setattr(scheduler.repo, "usage_rows_since", lambda since: turn_rows or [])
    monkeypatch.setattr(scheduler, "_vacancy_tenant_map", lambda: {"v1": "t1"})
    sent: list = []
    monkeypatch.setattr(
        scheduler.outbox, "deliver",
        lambda s, kind, payload, **kw: sent.append((kind, payload, kw)) or True,
    )
    return sent


def test_sla_sweep_alerts_once_per_condition_per_day(monkeypatch):
    cfgs = {"t1": {"enabled": True, "notify_email": "ops@x.com", "ops_alerts": True, "turn_p95_ms": 0}}
    ops = {"t1": [{"type": "dead_letter", "detail": "1 envío en dead-letter."}]}
    sent = _patch_sla_env(monkeypatch, cfgs=cfgs, ops_by_tenant=ops)
    _reset_sweep_state()

    assert scheduler._sla_sweep(get_settings())["alerted"] == 1
    kind, payload, kw = sent[0]
    assert kind == "ops_email" and payload["recipients"] == ["ops@x.com"]
    assert "dead_letter" in payload["text"] and kw["tenant_id"] == "t1"

    # Segunda corrida el mismo día: dedupe (aun forzando el intervalo).
    scheduler._state.pop("sla_sweep_last", None)
    assert scheduler._sla_sweep(get_settings())["alerted"] == 0
    assert len(sent) == 1
    _reset_sweep_state()


def test_sla_sweep_new_condition_same_day_still_alerts(monkeypatch):
    cfgs = {"t1": {"enabled": True, "notify_email": "ops@x.com", "ops_alerts": True, "turn_p95_ms": 1000}}
    ops = {"t1": [{"type": "dead_letter", "detail": "1 envío en dead-letter."}]}
    turn_rows = [{"vacancy_id": "v1", "stage": "turn", "calls": 1, "duration_ms": 8000}]
    sent = _patch_sla_env(monkeypatch, cfgs=cfgs, ops_by_tenant=ops)
    _reset_sweep_state()
    assert scheduler._sla_sweep(get_settings())["alerted"] == 1  # solo dead_letter

    # Aparece el incumplimiento de latencia más tarde el mismo día → alerta SOLO esa condición.
    monkeypatch.setattr(scheduler.repo, "usage_rows_since", lambda since: turn_rows)
    scheduler._state.pop("sla_sweep_last", None)
    assert scheduler._sla_sweep(get_settings())["alerted"] == 1
    assert len(sent) == 2 and "turn_p95" in sent[1][1]["text"]
    assert "dead_letter" not in sent[1][1]["text"]  # la ya avisada no se repite
    _reset_sweep_state()


def test_sla_sweep_disabled_tenant_is_silent(monkeypatch):
    cfgs = {"t1": {"enabled": False, "notify_email": "ops@x.com", "ops_alerts": True, "turn_p95_ms": 1}}
    ops = {"t1": [{"type": "dead_letter", "detail": "x"}]}
    sent = _patch_sla_env(monkeypatch, cfgs=cfgs, ops_by_tenant=ops)
    _reset_sweep_state()
    assert scheduler._sla_sweep(get_settings())["alerted"] == 0
    assert sent == []
    _reset_sweep_state()


def test_sla_sweep_respects_interval(monkeypatch):
    cfgs = {"t1": {"enabled": True, "notify_email": "", "ops_alerts": True, "turn_p95_ms": 0}}
    ops = {"t1": [{"type": "dead_letter", "detail": "x"}]}
    _patch_sla_env(monkeypatch, cfgs=cfgs, ops_by_tenant=ops)
    _reset_sweep_state()
    assert scheduler._sla_sweep(get_settings())["alerted"] == 1
    # Sin limpiar `sla_sweep_last`: dentro del intervalo de 15 min no re-evalúa.
    scheduler._state.pop("sla_alerted", None)
    assert scheduler._sla_sweep(get_settings())["alerted"] == 0
    _reset_sweep_state()


# ── Endpoints de settings (RBAC + tenant) ─────────────────────────────────────

def test_sla_settings_endpoints_rbac_and_tenant(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(
        main.repo, "get_app_setting",
        lambda key, default, tenant_id=None: store.get((tenant_id, key), default),
    )
    monkeypatch.setattr(
        main.repo, "set_app_setting",
        lambda key, value, tenant_id=None: store.__setitem__((tenant_id, key), value),
    )
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    assert client.get("/api/settings/sla-alerts").status_code == 401
    body = {"enabled": True, "notify_email": "ops@x.com", "ops_alerts": True, "turn_p95_ms": 5000}
    assert client.put("/api/settings/sla-alerts", json=body, headers=_auth("recruiter")).status_code == 403

    r = client.put("/api/settings/sla-alerts", json=body, headers=_auth("admin", "T_A"))
    assert r.status_code == 200 and r.json()["turn_p95_ms"] == 5000
    # Otro tenant no ve la config de T_A (cae al default apagado).
    r2 = client.get("/api/settings/sla-alerts", headers=_auth("admin", "T_B"))
    assert r2.json()["enabled"] is False
    # Umbral negativo → 422.
    bad = {**body, "turn_p95_ms": -1}
    assert client.put("/api/settings/sla-alerts", json=bad, headers=_auth("admin")).status_code == 422
