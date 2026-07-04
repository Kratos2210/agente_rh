"""Paso 4 — Medición continua de calidad.

Cubre: helpers puros del juez (evaluation/quality), el barrido _quality_sweep con fakes
(sin LLM ni DB), los endpoints de settings quality-alerts (RBAC + tenant), el endpoint
/api/ops/quality y el golden de recuperación (arnés con retriever fake).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import api.auth as auth
import api.main as main
import api.scheduler as scheduler
from fastapi.testclient import TestClient
from core.config import get_settings

client = TestClient(main.app)
ROOT = Path(__file__).resolve().parents[1]


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


def _reset_quality_state():
    scheduler._state.pop("quality_sweep_last", None)
    scheduler._state.pop("quality_swept", None)
    scheduler._state.pop("quality_llm", None)


# ── Helpers puros del juez (evaluation/quality) ───────────────────────────────

def test_judge_verdict_conservative_on_illegible():
    from evaluation.quality import judge_verdict

    v = judge_verdict(
        '{"grounded": true, "answer_relevant": false, "context_relevant": true, "reason": "evade"}'
    )
    assert v == {
        "grounded": True, "answer_relevant": False, "context_relevant": True, "reason": "evade",
    }
    bad = judge_verdict("<<no json>>")
    assert bad["grounded"] is False and bad["answer_relevant"] is False
    assert bad["context_relevant"] is False


def test_rate_pure():
    from evaluation.quality import rate

    assert rate([True, False, True, True]) == 0.75
    assert rate([]) == 1.0


def test_traces_by_tenant_groups_by_vacancy():
    traces = [
        {"id": "1", "vacancy_id": "vA"},
        {"id": "2", "vacancy_id": "vB"},
        {"id": "3", "vacancy_id": "vA"},
        {"id": "4", "vacancy_id": "vZ"},  # sin tenant → se descarta
    ]
    out = scheduler._traces_by_tenant(traces, {"vA": "t1", "vB": "t2"})
    assert [t["id"] for t in out["t1"]] == ["1", "3"]
    assert [t["id"] for t in out["t2"]] == ["2"]
    assert "vZ" not in out


# ── _quality_sweep con fakes (sin LLM ni DB) ──────────────────────────────────

def _patch_quality_env(monkeypatch, *, cfgs, traces, grounded, relevant, tenant_of, context=None):
    context = context if context is not None else relevant  # por defecto = relevancia
    monkeypatch.setattr(scheduler.repo, "list_tenants", lambda: [{"id": t} for t in cfgs])
    monkeypatch.setattr(
        scheduler.repo, "get_app_setting",
        lambda key, default, tenant_id=None: cfgs.get(tenant_id, default) if key == "quality_alerts" else default,
    )
    monkeypatch.setattr(scheduler.repo, "list_llm_traces_by_stage_since", lambda stage, since, limit=500: traces)
    monkeypatch.setattr(scheduler, "_vacancy_tenant_map", lambda: tenant_of)
    # Juez fake: no toca el LLM. Devuelve la 3-tupla (grounded, relevant, context).
    monkeypatch.setattr(
        scheduler, "_judge_traces",
        lambda llm, sample: (grounded[: len(sample)], relevant[: len(sample)], context[: len(sample)]),
    )
    monkeypatch.setattr(scheduler, "_quality_judge_llm", lambda: object())
    saved: list = []
    monkeypatch.setattr(scheduler.repo, "save_quality_metric",
                        lambda tid, metric, day, rate, n, thr: saved.append((tid, metric, rate, n, thr)))
    sent: list = []
    monkeypatch.setattr(scheduler.outbox, "deliver",
                        lambda s, kind, payload, **kw: sent.append((kind, payload, kw)) or True)
    return saved, sent


def test_quality_sweep_persists_three_metrics_and_alerts_below_threshold(monkeypatch):
    cfgs = {"t1": {"enabled": True, "sample": 4, "min_rate": 0.9, "notify_email": "q@x.com"}}
    traces = [{"id": str(i), "vacancy_id": "vA", "prompt_text": "p", "response_text": "r"} for i in range(4)]
    # 2/4 fundamentadas (0.5 < 0.9 → alerta); relevancia 4/4; contexto 3/4.
    saved, sent = _patch_quality_env(
        monkeypatch, cfgs=cfgs, traces=traces,
        grounded=[True, False, True, False], relevant=[True, True, True, True],
        context=[True, True, False, True],
        tenant_of={"vA": "t1"},
    )
    _reset_quality_state()

    report = scheduler._quality_sweep(get_settings())
    assert report["tenants"] == 1 and report["alerted"] == 1
    metrics = {m[1]: m for m in saved}
    assert metrics["grounded"][2] == 0.5 and metrics["grounded"][3] == 4
    assert metrics["answer_relevance"][2] == 1.0
    assert metrics["context_relevance"][2] == 0.75
    kind, payload, kw = sent[0]
    assert kind == "ops_email" and payload["recipients"] == ["q@x.com"] and kw["tenant_id"] == "t1"
    _reset_quality_state()


def test_quality_sweep_no_alert_when_above_threshold(monkeypatch):
    cfgs = {"t1": {"enabled": True, "sample": 4, "min_rate": 0.9, "notify_email": "q@x.com"}}
    traces = [{"id": str(i), "vacancy_id": "vA"} for i in range(4)]
    saved, sent = _patch_quality_env(
        monkeypatch, cfgs=cfgs, traces=traces,
        grounded=[True, True, True, True], relevant=[True, True, True, True],
        tenant_of={"vA": "t1"},
    )
    _reset_quality_state()
    report = scheduler._quality_sweep(get_settings())
    assert report["alerted"] == 0 and len(saved) == 3 and sent == []
    _reset_quality_state()


def test_quality_sweep_skips_tenant_without_traces(monkeypatch):
    cfgs = {"t1": {"enabled": True, "sample": 4, "min_rate": 0.9, "notify_email": ""}}
    saved, sent = _patch_quality_env(
        monkeypatch, cfgs=cfgs, traces=[], grounded=[], relevant=[], tenant_of={},
    )
    _reset_quality_state()
    report = scheduler._quality_sweep(get_settings())
    assert report["tenants"] == 0 and saved == []
    _reset_quality_state()


def test_quality_sweep_noop_when_no_tenant_enabled(monkeypatch):
    # Ningún tenant activo → ni siquiera se construye el LLM (si lo intentara, fallaría).
    monkeypatch.setattr(scheduler.repo, "list_tenants", lambda: [{"id": "t1"}])
    monkeypatch.setattr(scheduler.repo, "get_app_setting", lambda key, default, tenant_id=None: default)
    def _boom():
        raise AssertionError("no debe construir el juez sin tenants activos")
    monkeypatch.setattr(scheduler, "_quality_judge_llm", _boom)
    _reset_quality_state()
    assert scheduler._quality_sweep(get_settings()) == {"tenants": 0, "alerted": 0}
    _reset_quality_state()


def test_quality_sweep_dedupes_same_day(monkeypatch):
    cfgs = {"t1": {"enabled": True, "sample": 2, "min_rate": 0.9, "notify_email": ""}}
    traces = [{"id": str(i), "vacancy_id": "vA"} for i in range(2)]
    saved, _ = _patch_quality_env(
        monkeypatch, cfgs=cfgs, traces=traces,
        grounded=[True, True], relevant=[True, True], tenant_of={"vA": "t1"},
    )
    _reset_quality_state()
    scheduler._quality_sweep(get_settings())
    assert len(saved) == 3
    # Segunda corrida el mismo día (forzando el intervalo): no re-juzga.
    scheduler._state.pop("quality_sweep_last", None)
    scheduler._quality_sweep(get_settings())
    assert len(saved) == 3  # sin nuevas escrituras
    _reset_quality_state()


# ── Endpoints ─────────────────────────────────────────────────────────────────

def test_quality_settings_endpoints_rbac_and_tenant(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(main.repo, "get_app_setting",
                        lambda key, default, tenant_id=None: store.get((tenant_id, key), default))
    monkeypatch.setattr(main.repo, "set_app_setting",
                        lambda key, value, tenant_id=None: store.__setitem__((tenant_id, key), value))
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    assert client.get("/api/settings/quality-alerts").status_code == 401
    body = {"enabled": True, "sample": 30, "min_rate": 0.85, "notify_email": "q@x.com"}
    assert client.put("/api/settings/quality-alerts", json=body, headers=_auth("recruiter")).status_code == 403
    r = client.put("/api/settings/quality-alerts", json=body, headers=_auth("admin", "T_A"))
    assert r.status_code == 200 and r.json()["sample"] == 30
    # Otro tenant no ve la config de T_A.
    r2 = client.get("/api/settings/quality-alerts", headers=_auth("admin", "T_B"))
    assert r2.json()["enabled"] is False
    # min_rate fuera de rango → 422.
    assert client.put("/api/settings/quality-alerts", json={**body, "min_rate": 2},
                      headers=_auth("admin")).status_code == 422


def test_ops_quality_endpoint_admin_and_tenant(monkeypatch):
    rows = [{"metric": "grounded", "day": "2026-07-03", "rate": 0.95, "sample_size": 10, "threshold": 0.9}]
    monkeypatch.setattr(main.repo, "list_quality_metrics", lambda tenant_id, limit=60: rows)
    assert client.get("/api/ops/quality").status_code == 401
    assert client.get("/api/ops/quality", headers=_auth("recruiter")).status_code == 403
    r = client.get("/api/ops/quality", headers=_auth("admin"))
    assert r.status_code == 200 and r.json()["metrics"][0]["rate"] == 0.95


# ── Golden de recuperación (arnés con retriever fake) ─────────────────────────

def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


retrieval = _load_script("retrieval_eval")


def test_retrieval_golden_shape():
    import json

    data = json.loads((ROOT / "tests" / "golden" / "retrieval_set.json").read_text(encoding="utf-8"))
    assert 0 < data["min_hit_rate"] <= 1
    assert len(data["cases"]) >= 5
    for c in data["cases"]:
        assert c["id"] and c["question"] and c["expect"]


def test_hit_and_evaluate_and_rate():
    assert retrieval.hit("... EPS al 50% ...", "EPS al 50") is True
    assert retrieval.hit("nada relevante", "EPS al 50") is False
    cases = [{"id": "a", "question": "q1", "expect": "foo"}, {"id": "b", "question": "q2", "expect": "bar"}]
    # Retriever fake: devuelve el contexto que "recupera" según la pregunta.
    fake = {"q1": "contiene foo aquí", "q2": "no está lo pedido"}
    results = retrieval.evaluate_retrieval(cases, lambda q: fake.get(q, ""))
    assert results[0]["hit"] is True and results[1]["hit"] is False
    assert retrieval.hit_rate(results) == 0.5
    assert retrieval.hit_rate([]) == 1.0
