"""Listados sin N+1 (D1) + paginación/búsqueda (U1).

Endpoints con TestClient (sin lifespan → sin DB) y monkeypatch de `main.repo`:
los listados deben consumir los embeds de `list_candidate_rows` / el conteo
`count_candidates_by_status` (consultas fijas) y NUNCA `list_candidates` por vacante.
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
from fastapi.testclient import TestClient
from core.config import get_settings

client = TestClient(main.app)


def _auth(role: str = "recruiter", tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


def _embed_row(cid: str = "c1", vid: str = "v1", convs: list | None = None) -> dict:
    return {
        "id": cid, "name": "Ana", "status": "finished", "channel": "telegram",
        "source": "bumeran", "created_at": "2026-07-01T00:00:00Z", "vacancy_id": vid,
        "prescreen": {"pre_score": 92, "verdict": "pass"},
        "conversations": convs if convs is not None else [],
    }


# ── _candidate_row_from_embed (puro) ─────────────────────────────────────────────

def test_row_from_embed_picks_latest_conversation_and_scorecard():
    convs = [
        {"id": "conv-old", "created_at": "2026-06-01T00:00:00Z",
         "scorecards": [{"semaphore": "red", "total_score": 30}]},
        {"id": "conv-new", "created_at": "2026-06-20T00:00:00Z",
         "scorecards": [{"semaphore": "green", "total_score": 92}]},
    ]
    row = main._candidate_row_from_embed(_embed_row(convs=convs))
    assert row["conversation_id"] == "conv-new"
    assert row["semaphore"] == "green" and row["total_score"] == 92
    assert row["prescreen_score"] == 92 and row["prescreen_verdict"] == "pass"


def test_row_from_embed_accepts_to_one_object_shapes():
    """PostgREST embebe scorecards como OBJETO (unique de conversation_id), no lista —
    es la forma real en vivo; conversations también se acepta como objeto."""
    convs = {"id": "conv1", "created_at": "2026-06-01T00:00:00Z",
             "scorecards": {"semaphore": "green", "total_score": 92}}
    row = main._candidate_row_from_embed(_embed_row(convs=convs))
    assert row["conversation_id"] == "conv1"
    assert row["semaphore"] == "green" and row["total_score"] == 92


def test_row_from_embed_without_conversation_or_scorecard():
    row = main._candidate_row_from_embed(_embed_row(convs=[]))
    assert row["conversation_id"] is None and row["semaphore"] is None
    row2 = main._candidate_row_from_embed(
        _embed_row(convs=[{"id": "conv1", "created_at": "2026-06-01", "scorecards": []}])
    )
    assert row2["conversation_id"] == "conv1" and row2["total_score"] is None


def test_page_params_clamped():
    assert main._page_params(0, -5) == (1, 0)
    assert main._page_params(9999, 10) == (500, 10)
    assert main._page_params(100, 0) == (100, 0)


# ── Endpoints paginados ──────────────────────────────────────────────────────────

def _patch_rows(monkeypatch, *, rows: list, total: int):
    calls: dict = {}

    def list_candidate_rows(vacancy_ids, *, search="", limit=None, offset=0):
        calls.update(vacancy_ids=vacancy_ids, search=search, limit=limit, offset=offset)
        return (rows, total)

    monkeypatch.setattr(main.repo, "list_candidate_rows", list_candidate_rows)
    # Si algún endpoint listado vuelve a la ruta N+1, el test truena.
    monkeypatch.setattr(
        main.repo, "list_candidates",
        lambda vid: (_ for _ in ()).throw(AssertionError("N+1: list_candidates por vacante")),
    )
    monkeypatch.setattr(
        main.repo, "get_conversation_by_candidate",
        lambda cid: (_ for _ in ()).throw(AssertionError("N+1: consulta por candidato")),
    )
    return calls


def test_global_candidates_paginated_with_titles(monkeypatch):
    monkeypatch.setattr(
        main.repo, "list_vacancies",
        lambda status=None, tenant_id=None: [{"id": "v1", "title": "Analista", "tenant_id": tenant_id}],
    )
    calls = _patch_rows(monkeypatch, rows=[_embed_row()], total=134)
    r = client.get("/api/candidates?q=ana&limit=25&offset=50", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 134 and body["limit"] == 25 and body["offset"] == 50
    assert body["items"][0]["vacancy_title"] == "Analista"
    assert calls == {"vacancy_ids": ["v1"], "search": "ana", "limit": 25, "offset": 50}


def test_vacancy_candidates_paginated(monkeypatch):
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": "v1", "tenant_id": "t1"})
    calls = _patch_rows(monkeypatch, rows=[_embed_row()], total=1)
    r = client.get("/api/vacancies/v1/candidates?limit=99999", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 500  # clamp del tope
    assert body["items"][0]["id"] == "c1"
    assert calls["vacancy_ids"] == ["v1"]


def test_list_vacancies_uses_single_status_count(monkeypatch):
    monkeypatch.setattr(main.repo, "list_recruiters", lambda tenant_id=None, **k: [])
    monkeypatch.setattr(
        main.repo, "list_vacancies",
        lambda status=None, tenant_id=None: [{"id": "v1", "title": "A", "tenant_id": tenant_id}],
    )
    monkeypatch.setattr(
        main.repo, "list_candidates",
        lambda vid: (_ for _ in ()).throw(AssertionError("N+1: list_candidates por vacante")),
    )
    monkeypatch.setattr(
        main.repo, "count_candidates_by_status",
        lambda vids: {"v1": {"interviewing": 2, "finished": 1}},
    )
    r = client.get("/api/vacancies", headers=_auth())
    assert r.status_code == 200
    v = r.json()[0]
    assert v["candidate_count"] == 3
    assert v["stage_counts"] == {"interviewing": 2, "finished": 1}


def test_recruiters_workload_uses_single_status_count(monkeypatch):
    monkeypatch.setattr(
        main.repo, "list_vacancies",
        lambda status=None, tenant_id=None: [{"id": "v1", "recruiter_id": "r1", "tenant_id": tenant_id}],
    )
    monkeypatch.setattr(
        main.repo, "list_recruiters",
        lambda tenant_id=None, **k: [{"id": "r1", "name": "Grace", "tenant_id": tenant_id}],
    )
    monkeypatch.setattr(
        main.repo, "list_candidates",
        lambda vid: (_ for _ in ()).throw(AssertionError("N+1: list_candidates por vacante")),
    )
    monkeypatch.setattr(
        main.repo, "count_candidates_by_status",
        lambda vids: {"v1": {"interviewing": 2, "rejected": 5}},
    )
    r = client.get("/api/recruiters", headers=_auth())
    assert r.status_code == 200
    rec = r.json()[0]
    assert rec["open_vacancies"] == 1
    assert rec["active_candidates"] == 2  # rejected no cuenta como activo
