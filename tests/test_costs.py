"""Fase O-2 — Costos LLM por modelo/tenant + presupuesto: agregación por modelo,
cálculo de costo, endpoints de settings y barrido de presupuesto."""

from __future__ import annotations

import api.auth as auth
import api.main as main
import api.scheduler as scheduler
from api.deps import _with_cost, compute_cost
from db.repositories import _aggregate_tokens
from fastapi.testclient import TestClient
from src.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


PRICING = {
    "models": {"qwen3-32b": {"input_per_1m": 0.29, "output_per_1m": 0.59}},
    "default": {"input_per_1m": 1.0, "output_per_1m": 2.0},
}


# ── Agregación por modelo + cálculo puro ──────────────────────────────────────

def test_aggregate_tokens_groups_by_model():
    rows = [
        {"stage": "evaluate", "model": "qwen3-32b", "input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        {"stage": "classify", "model": "qwen3-32b", "input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        {"stage": "evaluate", "model": "gpt-x", "input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
    ]
    agg = _aggregate_tokens(rows)
    assert agg["by_model"]["qwen3-32b"] == {"input": 150, "output": 30, "total": 180}
    assert agg["by_model"]["gpt-x"] == {"input": 30, "output": 5, "total": 35}


def test_compute_cost_uses_model_price_and_default_fallback():
    by_model = {
        "qwen3-32b": {"input": 1_000_000, "output": 1_000_000, "total": 2_000_000},
        "otro-modelo": {"input": 500_000, "output": 0, "total": 500_000},
    }
    cost = compute_cost(by_model, PRICING)
    assert cost["by_model"]["qwen3-32b"] == 0.88  # 0.29 + 0.59
    assert cost["by_model"]["otro-modelo"] == 0.5  # default 1.0 × 0.5M
    assert cost["total"] == 1.38


def test_compute_cost_zero_pricing_yields_zero():
    cost = compute_cost({"m": {"input": 1000, "output": 1000, "total": 2000}},
                        {"models": {}, "default": {"input_per_1m": 0, "output_per_1m": 0}})
    assert cost["total"] == 0.0 and cost["by_model"] == {}


# ── _with_cost: precios por-tenant + fallback legado ──────────────────────────

def test_with_cost_reads_tenant_pricing(monkeypatch):
    seen = {}

    def _get(key, default, tenant_id=None):
        seen["key"], seen["tenant"] = key, tenant_id
        return PRICING

    monkeypatch.setattr(main.repo, "get_app_setting", _get)
    metrics = {"tokens": {"total": 2_000_000, "by_model": {
        "qwen3-32b": {"input": 1_000_000, "output": 1_000_000, "total": 2_000_000}}}}
    out = _with_cost(metrics, "TENANT_X")
    assert seen == {"key": "llm_pricing", "tenant": "TENANT_X"}
    assert out["est_cost"] == 0.88
    assert out["cost_by_model"]["qwen3-32b"] == 0.88


def test_with_cost_falls_back_to_legacy_scalar(monkeypatch):
    # Sin precios por modelo configurados, cae al escalar global token_price_per_1k.
    import api.deps as deps

    class _S:
        token_price_per_1k = 0.002

    monkeypatch.setattr(
        main.repo, "get_app_setting",
        lambda key, default, tenant_id=None: {"models": {}, "default": {"input_per_1m": 0, "output_per_1m": 0}},
    )
    monkeypatch.setattr(deps, "current_settings", lambda: _S())
    metrics = {"tokens": {"total": 10_000, "by_model": {"m": {"input": 9000, "output": 1000, "total": 10_000}}}}
    out = _with_cost(metrics, "t1")
    assert out["est_cost"] == 0.02
    assert out["cost_by_model"] == {}


# ── Endpoints de settings (RBAC + tenant) ─────────────────────────────────────

def test_llm_pricing_endpoints_rbac_and_tenant(monkeypatch):
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

    assert client.get("/api/settings/llm-pricing").status_code == 401
    assert client.put("/api/settings/llm-pricing", json=PRICING, headers=_auth("recruiter")).status_code == 403

    r = client.put("/api/settings/llm-pricing", json=PRICING, headers=_auth("admin", "T_A"))
    assert r.status_code == 200
    assert r.json()["models"]["qwen3-32b"]["input_per_1m"] == 0.29
    # Otro tenant no ve los precios de T_A (cae al default).
    r2 = client.get("/api/settings/llm-pricing", headers=_auth("admin", "T_B"))
    assert r2.json()["models"] == {}


def test_llm_budget_endpoint_roundtrip(monkeypatch):
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

    body = {"enabled": True, "monthly_usd": 50, "alert_pct": 80, "notify_email": "ops@x.com"}
    r = client.put("/api/settings/llm-budget", json=body, headers=_auth("admin"))
    assert r.status_code == 200 and r.json()["monthly_usd"] == 50
    assert client.get("/api/settings/llm-budget", headers=_auth("viewer")).json()["enabled"] is True


# ── Barrido de presupuesto (gasto del mes por tenant + dedupe) ────────────────

def _patch_budget_env(monkeypatch, *, spend_rows, budgets):
    """Fakes comunes del sweep: tenants, settings por tenant, uso del mes y mapa vacante→tenant."""
    monkeypatch.setattr(scheduler.repo, "list_tenants", lambda: [{"id": t} for t in budgets])
    monkeypatch.setattr(
        scheduler.repo, "get_app_setting",
        lambda key, default, tenant_id=None: (
            budgets.get(tenant_id, default) if key == "llm_budget" else PRICING
        ),
    )
    monkeypatch.setattr(scheduler.repo, "usage_rows_since", lambda since: spend_rows)
    monkeypatch.setattr(scheduler, "_vacancy_tenant_map", lambda: {"v1": "t1", "v2": "t2"})


def test_budget_sweep_alerts_once_and_emails(monkeypatch):
    # t1 gastó 1M in + 1M out de qwen3-32b = $0.88 ≥ 80% de $1 → alerta; t2 sin presupuesto.
    rows = [{"vacancy_id": "v1", "model": "qwen3-32b",
             "input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}]
    budgets = {
        "t1": {"enabled": True, "monthly_usd": 1.0, "alert_pct": 80, "notify_email": "ops@x.com"},
        "t2": {"enabled": False, "monthly_usd": 0, "alert_pct": 80, "notify_email": ""},
    }
    _patch_budget_env(monkeypatch, spend_rows=rows, budgets=budgets)
    sent: list = []
    monkeypatch.setattr(scheduler.outbox, "deliver", lambda s, kind, payload, **kw: sent.append((kind, payload, kw)) or True)
    scheduler._state.pop("budget_sweep_last", None)
    scheduler._state.pop("budget_alerted", None)

    report = scheduler._budget_sweep(get_settings())
    assert report["alerted"] == 1
    kind, payload, kw = sent[0]
    assert kind == "ops_email" and payload["recipients"] == ["ops@x.com"] and kw["tenant_id"] == "t1"
    assert "$0.88" in payload["text"]

    # Segunda corrida en el mismo mes: dedupe (ni alerta ni correo), aun forzando el intervalo.
    scheduler._state.pop("budget_sweep_last", None)
    assert scheduler._budget_sweep(get_settings())["alerted"] == 0
    assert len(sent) == 1
    scheduler._state.pop("budget_alerted", None)


def test_budget_sweep_below_threshold_is_silent(monkeypatch):
    rows = [{"vacancy_id": "v1", "model": "qwen3-32b",
             "input_tokens": 100_000, "output_tokens": 100_000, "total_tokens": 200_000}]  # $0.088
    budgets = {"t1": {"enabled": True, "monthly_usd": 1.0, "alert_pct": 80, "notify_email": ""}}
    _patch_budget_env(monkeypatch, spend_rows=rows, budgets=budgets)
    scheduler._state.pop("budget_sweep_last", None)
    scheduler._state.pop("budget_alerted", None)
    assert scheduler._budget_sweep(get_settings())["alerted"] == 0


def test_ops_alerts_include_budget_for_tenant(monkeypatch):
    # La vista por-tenant del dashboard muestra budget_exceeded; requiere presupuesto activo.
    rows = [{"vacancy_id": "v1", "model": "qwen3-32b",
             "input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}]
    budgets = {"t1": {"enabled": True, "monthly_usd": 1.0, "alert_pct": 80, "notify_email": ""}}
    _patch_budget_env(monkeypatch, spend_rows=rows, budgets=budgets)
    # Vacía el resto de señales del colector.
    monkeypatch.setattr(scheduler.repo, "count_outbox_by_status", lambda tid=None: {})
    monkeypatch.setattr(scheduler.repo, "list_meetings_without_link", lambda: [])
    monkeypatch.setattr(scheduler.repo, "list_conversations_by_states", lambda states: [])
    monkeypatch.setattr(scheduler.repo, "list_delivery_failed_conversations", lambda: [])
    monkeypatch.setattr(scheduler, "_state", {**scheduler._state, "service": None})

    alerts = scheduler._collect_ops_alerts("t1")
    assert any(a["type"] == "budget_exceeded" for a in alerts)
