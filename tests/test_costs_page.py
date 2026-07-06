"""Página Costos — `GET /api/costs`: RBAC, aislamiento por tenant y agregación
por día (zona Lima) / vacante / candidato con costo por pricing del tenant."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import api.auth as auth
import api.main as main
from api.routes.costs import _cost_report
from core.config import get_settings
from fastapi.testclient import TestClient

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

VACS = [{"id": "v1", "tenant_id": "t1", "title": "Dev", "status": "open"}]

LIMA = timezone(timedelta(hours=-5))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(vid="v1", cid="c1", model="qwen3-32b", inp=100, out=20, stage="evaluate", created=None, calls=1):
    return {
        "vacancy_id": vid, "candidate_id": cid, "model": model, "stage": stage,
        "calls": calls, "input_tokens": inp, "output_tokens": out,
        "total_tokens": inp + out, "created_at": created or _now_iso(),
    }


def _patch_repo(monkeypatch, rows, vacancies=VACS, names=None, seen=None):
    def _list_vacancies(status=None, tenant_id=None):
        if seen is not None:
            seen["tenant"] = tenant_id
        return vacancies

    monkeypatch.setattr(main.repo, "list_vacancies", _list_vacancies)
    monkeypatch.setattr(main.repo, "usage_rows_detailed_since", lambda since: rows)
    monkeypatch.setattr(
        main.repo, "get_app_setting",
        lambda key, default=None, tenant_id=None: PRICING if key == "llm_pricing" else default,
    )
    monkeypatch.setattr(main.repo, "candidate_names", lambda ids: dict(names or {}))


# ── RBAC ───────────────────────────────────────────────────────────────────────

def test_costs_requires_admin(monkeypatch):
    _patch_repo(monkeypatch, [])
    assert client.get("/api/costs").status_code == 401
    assert client.get("/api/costs", headers=_auth("viewer")).status_code == 403
    assert client.get("/api/costs", headers=_auth("recruiter")).status_code == 403
    assert client.get("/api/costs", headers=_auth("admin")).status_code == 200


# ── Aislamiento por tenant ─────────────────────────────────────────────────────

def test_costs_isolated_by_tenant(monkeypatch):
    seen: dict = {}
    rows = [_row(vid="v1", inp=100, out=20), _row(vid="v-ajena", inp=9000, out=9000)]
    _patch_repo(monkeypatch, rows, seen=seen)
    r = client.get("/api/costs", headers=_auth("admin", "TENANT_X"))
    assert r.status_code == 200
    body = r.json()
    assert seen["tenant"] == "TENANT_X"  # las vacantes se listaron con el tenant del token
    assert body["totals"]["tokens"]["total"] == 120  # la vacante ajena no cuenta
    assert [v["vacancy_id"] for v in body["vacancies"]] == ["v1"]


# ── Exclusión de la fila sintética "turn" ──────────────────────────────────────

def test_costs_excludes_turn_stage(monkeypatch):
    rows = [
        _row(stage="turn", inp=0, out=0, calls=1),
        _row(stage="evaluate", inp=100, out=20),
    ]
    _patch_repo(monkeypatch, rows)
    body = client.get("/api/costs", headers=_auth("admin")).json()
    assert body["totals"]["tokens"]["total"] == 120
    assert body["totals"]["calls"] == 1  # solo la llamada LLM real


# ── Clamp del rango ────────────────────────────────────────────────────────────

def test_costs_days_clamped(monkeypatch):
    _patch_repo(monkeypatch, [])
    assert client.get("/api/costs?days=0", headers=_auth("admin")).json()["days"] == 1
    assert client.get("/api/costs?days=99999", headers=_auth("admin")).json()["days"] == 365
    body = client.get("/api/costs", headers=_auth("admin")).json()
    assert body["days"] == 30 and len(body["daily"]) == 30  # default + eje continuo


# ── Costo con el pricing del tenant ────────────────────────────────────────────

def test_costs_apply_tenant_pricing(monkeypatch):
    rows = [_row(inp=1_000_000, out=1_000_000)]
    _patch_repo(monkeypatch, rows, names={"c1": "Daniela"})
    body = client.get("/api/costs", headers=_auth("admin")).json()
    assert body["totals"]["cost"] == 0.88  # 0.29 + 0.59 por 1M+1M
    assert body["totals"]["cost_by_model"]["qwen3-32b"] == 0.88
    vac = body["vacancies"][0]
    assert vac["cost"] == 0.88
    assert vac["candidates"][0]["name"] == "Daniela"
    assert vac["candidates"][0]["cost"] == 0.88


# ── Drill-down por candidato ───────────────────────────────────────────────────

def test_costs_candidates_grouped_and_sorted(monkeypatch):
    rows = [
        _row(cid="c-small", inp=100, out=10),
        _row(cid="c-big", inp=2_000_000, out=500_000),
        _row(cid=None, inp=300, out=30),  # prescreen batch sin candidato
    ]
    _patch_repo(monkeypatch, rows, names={"c-big": "Ana"})  # c-small sin nombre (anonimizada)
    vac = client.get("/api/costs", headers=_auth("admin")).json()["vacancies"][0]
    cands = vac["candidates"]
    assert [c["candidate_id"] for c in cands][0] == "c-big"  # orden desc por costo
    by_id = {c["candidate_id"]: c for c in cands}
    assert by_id["c-big"]["name"] == "Ana"
    assert by_id["c-small"]["name"] == ""  # sin nombre → fallback del frontend
    assert None in by_id  # bucket "proceso general"
    assert by_id[None]["tokens"] == 330


# ── Unit tests puros de _cost_report ───────────────────────────────────────────

def test_cost_report_day_bucket_in_lima_tz():
    # 03:00 UTC del 5-jul = 22:00 del 4-jul en Lima → bucket 2026-07-04.
    rows = [_row(created="2026-07-05T03:00:00+00:00", inp=100, out=20)]
    rep = _cost_report(rows, VACS, {}, PRICING, LIMA, date(2026, 7, 1), 7)
    daily = {d["day"]: d for d in rep["daily"]}
    assert len(daily) == 7  # relleno a cero: eje continuo
    assert daily["2026-07-04"]["tokens"] == 120
    assert daily["2026-07-05"]["tokens"] == 0


def test_cost_report_multi_model_and_days():
    rows = [
        _row(model="qwen3-32b", inp=1_000_000, out=0, created="2026-07-01T15:00:00+00:00"),
        _row(model="barato", inp=1_000_000, out=0, created="2026-07-02T15:00:00+00:00"),
    ]
    rep = _cost_report(rows, VACS, {}, PRICING, LIMA, date(2026, 7, 1), 3)
    assert rep["totals"]["cost_by_model"]["qwen3-32b"] == 0.29
    assert rep["totals"]["cost_by_model"]["barato"] == 1.0  # precio default
    daily = {d["day"]: d for d in rep["daily"]}
    assert daily["2026-07-01"]["cost"] == 0.29 and daily["2026-07-02"]["cost"] == 1.0


def test_cost_report_row_outside_axis_does_not_crash():
    # Una fila anterior al eje (borde de tz/clock skew) suma a totales pero no revienta.
    rows = [_row(created="2026-06-01T00:00:00+00:00", inp=100, out=0)]
    rep = _cost_report(rows, VACS, {}, PRICING, LIMA, date(2026, 7, 1), 3)
    assert rep["totals"]["tokens"]["total"] == 100
    assert all(d["tokens"] == 0 for d in rep["daily"])
