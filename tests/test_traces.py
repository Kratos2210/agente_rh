"""Fase O-1 — Trazas LLM con contenido: captura en MeteredLLM, persistencia,
endpoint admin y purga en retención/erasure."""

from __future__ import annotations

import api.auth as auth
import api.main as main
import pytest
from orquestacion.llm import MeteredLLM
from db import repositories
from fastapi.testclient import TestClient
from core.config import get_settings

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


class _OkInner:
    model = "fake-model"
    last_usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    def complete(self, prompt: str) -> str:
        return "respuesta-cruda"


class _BoomInner:
    model = "fake-model"

    def complete(self, prompt: str) -> str:
        raise RuntimeError("proveedor caído")


# ── Captura en MeteredLLM ─────────────────────────────────────────────────────

def test_trace_off_buffers_nothing():
    m = MeteredLLM(_OkInner())
    m.for_stage("evaluate").complete("hola")
    assert m.drain_traces() == []


def test_trace_on_captures_prompt_response_and_stage():
    m = MeteredLLM(_OkInner(), trace=True)
    m.for_stage("evaluate").complete("pregunta al modelo")
    traces = m.drain_traces()
    assert len(traces) == 1
    t = traces[0]
    assert t["stage"] == "evaluate"
    assert t["prompt"] == "pregunta al modelo"
    assert t["response"] == "respuesta-cruda"
    assert t["error"] is None
    assert t["duration_ms"] >= 0
    # drain limpia el buffer
    assert m.drain_traces() == []


def test_trace_caps_prompt_and_response():
    m = MeteredLLM(_OkInner(), trace=True, trace_max_chars=5)
    m.complete("x" * 100)
    t = m.drain_traces()[0]
    assert t["prompt"] == "xxxxx"
    assert t["response"] == "respu"


def test_trace_captures_provider_error_and_reraises():
    m = MeteredLLM(_BoomInner(), trace=True)
    with pytest.raises(RuntimeError):
        m.for_stage("classify").complete("hola")
    t = m.drain_traces()[0]
    assert t["response"] is None
    assert "proveedor caído" in t["error"]


# ── Persistencia (record_traces) ──────────────────────────────────────────────

class _FakeTable:
    def __init__(self, sink: list):
        self._sink = sink

    def insert(self, rows):
        self._sink.append(rows)
        return self

    def execute(self):
        return self


def test_record_traces_maps_rows(monkeypatch):
    inserted: list = []

    class _FakeClient:
        def table(self, name):
            assert name == "llm_traces"
            return _FakeTable(inserted)

    monkeypatch.setattr(repositories, "get_supabase", lambda: _FakeClient())
    repositories.record_traces(
        [{"stage": "evaluate", "prompt": "p", "response": "r", "error": None, "duration_ms": 42}],
        vacancy_id="v1", candidate_id="c1", conversation_id="k1",
        model="qwen", prompt_version="2026-07-02.1",
    )
    row = inserted[0][0]
    assert row["stage"] == "evaluate"
    assert row["prompt_text"] == "p"
    assert row["response_text"] == "r"
    assert row["duration_ms"] == 42
    assert row["candidate_id"] == "c1" and row["conversation_id"] == "k1"
    assert row["model"] == "qwen" and row["prompt_version"] == "2026-07-02.1"
    # Sin trazas no toca la DB; y un fallo de DB no propaga (best-effort).
    repositories.record_traces([], candidate_id="c1")
    assert len(inserted) == 1


def test_record_traces_swallows_db_errors(monkeypatch):
    def _boom():
        raise RuntimeError("db caída")

    monkeypatch.setattr(repositories, "get_supabase", _boom)
    repositories.record_traces([{"stage": "s", "prompt": "p"}], candidate_id="c1")  # no lanza


# ── Endpoint admin (RBAC + tenant) ────────────────────────────────────────────

def test_traces_endpoint_requires_admin():
    assert client.get("/api/candidates/c1/traces").status_code == 401
    assert client.get("/api/candidates/c1/traces", headers=_auth("recruiter")).status_code == 403


def test_traces_endpoint_returns_items_in_tenant(monkeypatch):
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: {"id": cid, "vacancy_id": "v1"})
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "t1"})
    monkeypatch.setattr(
        main.repo, "list_llm_traces", lambda cid, limit=200: [{"id": "tr1", "stage": "evaluate"}]
    )
    r = client.get("/api/candidates/c1/traces", headers=_auth("admin"))
    assert r.status_code == 200
    assert r.json()["items"][0]["id"] == "tr1"


def test_traces_endpoint_cross_tenant_is_404(monkeypatch):
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: {"id": cid, "vacancy_id": "v1"})
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "OWNER"})
    r = client.get("/api/candidates/c1/traces", headers=_auth("admin", "INTRUSO"))
    assert r.status_code == 404


# ── Purga (erasure borra también las trazas) ──────────────────────────────────

def test_erasure_purges_traces(monkeypatch):
    purged: list[str] = []
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: {"id": cid, "vacancy_id": "v1"})
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "t1"})
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: None)
    monkeypatch.setattr(main.repo, "delete_outbox_by_candidate", lambda cid: None)
    monkeypatch.setattr(main.repo, "scrub_audit_for_entity", lambda cid: None)
    monkeypatch.setattr(main.repo, "delete_llm_traces_by_candidate", lambda cid: purged.append(cid))
    monkeypatch.setattr(main.repo, "delete_candidate", lambda cid: None)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)

    r = client.delete("/api/candidates/c1", headers=_auth("admin"))
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert purged == ["c1"]
