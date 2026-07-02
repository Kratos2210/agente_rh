"""Cierre del backlog de la auditoría e2e (S4, R4, A5, D2, D3, D4, D5, G3, G4, O3).

  - S4: erasure y retención purgan la PII residual (payloads del outbox + resúmenes
        de auditoría); el audit del borrado no incluye el nombre.
  - R4: sync-applicants con throttle por tenant (2/min) → 429.
  - A5: `get_or_create_candidate` sobrevive al insert concurrente (retry-on-conflict:
        el perdedor relee la fila del ganador, sin duplicar).
  - D2: sobre `document_db_max_bytes` el PDF queda solo en disco (stored="disk").
  - D3: `replace_vacancy_questions`/`claim_candidate_chat` van por RPC atómico y caen
        al camino multi-request si el RPC no existe (0022 no aplicada).
  - D4: `_checkpoint_purge_sweep` gated por config (0=off) e intervalo mínimo de 6 h.
  - D5: la retención mide antigüedad por `updated_at` (fallback `created_at`).
  - G3: la alerta `delivery_failed` solo salta si el candidato NO interactuó después
        del fallo de entrega.
  - G4: `_sync_business` registra la transición de fase una sola vez (al cambiar).
  - O3: `HttpMetrics` acumula conteo/errores/latencia por ruta; el endpoint es admin-only.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.main as main
import api.routes.vacancies as vac_routes
import api.scheduler as scheduler
import db.repositories as db_repo
from api import auth
from api.httpmetrics import HttpMetrics, http_metrics
from src.config import get_settings

client = TestClient(main.app)


def _auth_headers(role: str, user_id: str = "u1", tenant_id: str = "t1") -> dict[str, str]:
    token = auth.create_access_token(
        user_id=user_id, email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Estado proceso-global limpio: caché de revocación, limiter del sync y métricas HTTP."""
    auth._revocation_cache.clear()
    monkeypatch.setattr(db_repo, "get_user", lambda uid: {"id": uid, "active": True})
    vac_routes._sync_limiter.reset()
    http_metrics.reset()
    yield
    auth._revocation_cache.clear()
    vac_routes._sync_limiter.reset()
    http_metrics.reset()


# ── Fakes del cliente Supabase (encadenables, con guion de respuestas) ─────────────


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    """Cadena select/eq/limit/insert/delete/... que registra y responde según guion."""

    def __init__(self, log: list, result=None, fail: bool = False):
        self._log, self._result, self._fail = log, result, fail

    def __getattr__(self, name):
        def _chain(*args, **kwargs):
            self._log.append((name, args))
            return self

        return _chain

    def execute(self):
        if self._fail:
            raise RuntimeError("duplicate key value violates unique constraint")
        return _Res(self._result)


class _FakeSB:
    """`table()` entrega consultas del guion en orden; `rpc()` registra o falla."""

    def __init__(self, queries=None, rpc_fails: bool = False, table_forbidden: bool = False):
        self.log: list = []
        self._queries = list(queries or [])
        self._rpc_fails = rpc_fails
        self._table_forbidden = table_forbidden

    def rpc(self, name, params):
        self.log.append(("rpc", name, params))
        return _Query(self.log, fail=self._rpc_fails)

    def table(self, name):
        if self._table_forbidden:
            raise AssertionError("el camino RPC no debe tocar tablas")
        self.log.append(("table", name))
        if self._queries:
            return self._queries.pop(0)
        return _Query(self.log)


# ── R4: throttle del sync por tenant ────────────────────────────────────────────


def test_sync_applicants_throttled_per_tenant(monkeypatch):
    monkeypatch.setattr(db_repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "t1"})
    # Agota la ventana del tenant t1 (2/min) sin correr el sync pesado.
    assert vac_routes._sync_limiter.allow("sync:t1")
    assert vac_routes._sync_limiter.allow("sync:t1")
    r = client.post("/api/vacancies/v1/sync-applicants", headers=_auth_headers("recruiter"))
    assert r.status_code == 429
    assert "muy frecuente" in r.json()["detail"]
    # La ventana es POR tenant: otra empresa no queda bloqueada por el abuso de t1.
    assert vac_routes._sync_limiter.allow("sync:t2") is True


# ── A5: candidato concurrente sin duplicar ─────────────────────────────────────


def test_get_or_create_candidate_retries_on_conflict(monkeypatch):
    log: list = []
    winner = {"id": "winner", "vacancy_id": "v1", "channel": "telegram", "channel_user_id": "99"}
    sb = _FakeSB(queries=[
        _Query(log, result=[]),            # select inicial: vacío (ambos mensajes lo ven así)
        _Query(log, fail=True),            # insert: pierde contra el unique
        _Query(log, result=[winner]),      # re-select: la fila del ganador
    ])
    monkeypatch.setattr(db_repo, "get_supabase", lambda: sb)
    row = db_repo.get_or_create_candidate("v1", "telegram", "99", name="Daniela")
    assert row["id"] == "winner"  # el perdedor NO duplica: adopta la fila existente


def test_get_or_create_candidate_reraises_without_winner(monkeypatch):
    log: list = []
    sb = _FakeSB(queries=[
        _Query(log, result=[]),
        _Query(log, fail=True),
        _Query(log, result=[]),  # el fallo no era el unique: no hay fila que adoptar
    ])
    monkeypatch.setattr(db_repo, "get_supabase", lambda: sb)
    with pytest.raises(RuntimeError):
        db_repo.get_or_create_candidate("v1", "telegram", "99")


# ── D3: RPCs atómicos con fallback retro-compatible ─────────────────────────────


def test_replace_vacancy_questions_uses_rpc(monkeypatch):
    sb = _FakeSB(table_forbidden=True)
    monkeypatch.setattr(db_repo, "get_supabase", lambda: sb)
    db_repo.replace_vacancy_questions("v1", [{"question": "¿?", "position": 0}])
    rpc = next(e for e in sb.log if e[0] == "rpc")
    assert rpc[1] == "app_replace_vacancy_questions"
    assert rpc[2]["vid"] == "v1" and len(rpc[2]["qs"]) == 1


def test_replace_vacancy_questions_falls_back_without_rpc(monkeypatch):
    sb = _FakeSB(rpc_fails=True)
    monkeypatch.setattr(db_repo, "get_supabase", lambda: sb)
    db_repo.replace_vacancy_questions("v1", [{"question": "¿?", "position": 0}])
    ops = [e[0] for e in sb.log]
    assert ops.count("table") == 2      # delete + insert (camino previo)
    assert "delete" in ops and "insert" in ops


def test_claim_candidate_chat_uses_rpc(monkeypatch):
    sb = _FakeSB(table_forbidden=True)
    monkeypatch.setattr(db_repo, "get_supabase", lambda: sb)
    db_repo.claim_candidate_chat("c1", "v1", "telegram", "999", "telegram:999")
    rpc = next(e for e in sb.log if e[0] == "rpc")
    assert rpc[1] == "app_claim_candidate_chat"
    assert rpc[2] == {
        "target": "c1", "vid": "v1", "chan": "telegram", "chat": "999", "thread": "telegram:999",
    }


# ── S4: erasure y retención purgan la PII residual ──────────────────────────────


def test_erasure_purges_outbox_and_audit(monkeypatch):
    called: dict[str, object] = {}
    monkeypatch.setattr(db_repo, "get_candidate",
                        lambda cid: {"id": cid, "vacancy_id": "v1", "name": "Daniela Torres"})
    monkeypatch.setattr(db_repo, "get_vacancy", lambda vid: {"id": vid, "tenant_id": "t1"})
    monkeypatch.setattr(db_repo, "get_conversation_by_candidate",
                        lambda cid: {"id": "x1", "langgraph_thread_id": "telegram:1"})
    for fn in ("delete_langgraph_checkpoint", "delete_outbox_by_candidate",
               "scrub_audit_for_entity", "delete_candidate"):
        monkeypatch.setattr(db_repo, fn, lambda arg, _fn=fn: called.setdefault(_fn, arg))
    monkeypatch.setattr(db_repo, "add_audit_log", lambda entry: called.setdefault("audit", entry))

    r = client.delete("/api/candidates/c1", headers=_auth_headers("admin"))
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert called["delete_outbox_by_candidate"] == "c1"
    assert called["scrub_audit_for_entity"] == "c1"
    assert called["delete_candidate"] == "c1"
    # El registro del borrado no re-introduce el nombre que se acaba de olvidar.
    assert "Daniela" not in called["audit"]["summary"]


def test_retention_sweep_purges_outbox_and_audit(monkeypatch):
    cand = {
        "id": "c1", "vacancy_id": "v1", "name": "Daniela", "cv_profile": {"email": "d@x.pe"},
        "created_at": "2025-12-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    monkeypatch.setattr(scheduler, "_vacancy_tenant_map", lambda: {"v1": "t1"})
    monkeypatch.setattr(scheduler, "_tenant_cfg_resolver",
                        lambda key, default: (lambda tid: {"enabled": True, "days": 30}))
    monkeypatch.setattr(db_repo, "list_candidates_by_statuses", lambda statuses: [dict(cand)])
    monkeypatch.setattr(db_repo, "get_conversation_by_candidate",
                        lambda cid: {"id": "x1", "langgraph_thread_id": "telegram:1"})
    called: dict[str, object] = {}
    for fn in ("delete_messages", "delete_langgraph_checkpoint", "delete_candidate_documents",
               "delete_outbox_by_candidate", "scrub_audit_for_entity", "anonymize_candidate"):
        monkeypatch.setattr(db_repo, fn, lambda arg, _fn=fn: called.setdefault(_fn, arg))

    report = scheduler._retention_sweep(settings=None)
    assert report == {"anonymized": 1}
    assert called["delete_outbox_by_candidate"] == "c1"
    assert called["scrub_audit_for_entity"] == "c1"
    assert called["anonymize_candidate"] == "c1"


# ── D5: reloj de retención por última modificación ──────────────────────────────


def test_retention_reference_prefers_updated_at():
    cand = {"created_at": "2026-01-01", "updated_at": "2026-06-01"}
    assert scheduler._retention_reference_ts(cand) == "2026-06-01"
    assert scheduler._retention_reference_ts({"created_at": "2026-01-01"}) == "2026-01-01"


# ── D4: purga de checkpoints gated por config e intervalo ───────────────────────


def test_checkpoint_purge_sweep_gating(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(scheduler, "_has_database_url", lambda: True)
    monkeypatch.setattr(db_repo, "purge_stale_checkpoints",
                        lambda days: calls.__setitem__("n", calls["n"] + 1) or 3)
    # 0 días = desactivado: ni toca la DB.
    monkeypatch.setitem(scheduler._state, "checkpoint_purge_last", None)
    assert scheduler._checkpoint_purge_sweep(SimpleNamespace(checkpoint_retention_days=0)) == {"purged": 0}
    assert calls["n"] == 0
    # Activado y vencido el intervalo → purga; el tick siguiente (a los 30 s) NO repite.
    monkeypatch.setitem(scheduler._state, "checkpoint_purge_last", time.monotonic() - 7 * 3600)
    settings = SimpleNamespace(checkpoint_retention_days=30)
    assert scheduler._checkpoint_purge_sweep(settings) == {"purged": 3}
    assert scheduler._checkpoint_purge_sweep(settings) == {"purged": 0}
    assert calls["n"] == 1


# ── D2: umbral de contenido en DB para documentos ───────────────────────────────


def _doc_service_env(monkeypatch, *, size: int, max_db: int):
    from agent import service as svc

    service = svc.InterviewService(runner=None, settings=SimpleNamespace(document_db_max_bytes=max_db))
    saved: dict[str, object] = {}
    monkeypatch.setattr(svc, "_read_document_b64", lambda path: ("QUJD", size))
    monkeypatch.setattr(db_repo, "save_document_content",
                        lambda cid, **kw: saved.setdefault("db", kw) or {"id": "d1"})
    monkeypatch.setattr(db_repo, "add_candidate_document",
                        lambda cid, doc: saved.setdefault("meta", doc) or {})
    monkeypatch.setattr(db_repo, "add_message", lambda *a, **k: {})
    state = {"save_document": {"type": "cv", "filename": "cv.pdf", "local_path": "uploads/cv.pdf"}}
    service._persist_save_document({"id": "c1"}, {"id": "x1"}, state)
    return saved


def test_document_over_threshold_stays_on_disk(monkeypatch):
    saved = _doc_service_env(monkeypatch, size=100, max_db=10)
    assert "db" not in saved                      # no infla PostgREST con el base64
    assert saved["meta"]["stored"] == "disk"


def test_document_under_threshold_goes_to_db(monkeypatch):
    saved = _doc_service_env(monkeypatch, size=5, max_db=10)
    assert saved["db"]["size_bytes"] == 5
    assert saved["meta"]["stored"] == "db"


# ── G3: alerta de entrega fallida solo si el candidato no volvió ────────────────


def test_delivery_failed_alert_filtered_by_activity(monkeypatch):
    monkeypatch.setattr(db_repo, "count_outbox_by_status", lambda tid=None: {})
    monkeypatch.setattr(db_repo, "list_meetings_without_link", lambda: [])
    monkeypatch.setattr(db_repo, "list_conversations_by_states", lambda states: [])
    monkeypatch.setattr(db_repo, "get_meeting_by_conversation", lambda cid: None)
    monkeypatch.setattr(db_repo, "list_delivery_failed_conversations", lambda: [
        {"id": "x1", "candidate_id": "c1", "vacancy_id": "v1",
         "last_delivery_failed_at": "2026-07-02T12:00:00Z",
         "last_activity_at": "2026-07-02T11:00:00Z"},   # falló DESPUÉS del último mensaje → alerta
        {"id": "x2", "candidate_id": "c2", "vacancy_id": "v1",
         "last_delivery_failed_at": "2026-07-02T10:00:00Z",
         "last_activity_at": "2026-07-02T11:00:00Z"},   # el candidato volvió a escribir → canal vivo
    ])
    monkeypatch.setitem(scheduler._state, "service", None)
    alerts = [a for a in scheduler._collect_ops_alerts() if a["type"] == "delivery_failed"]
    assert [a["conversation_id"] for a in alerts] == ["x1"]


# ── G4: transición de fase registrada una sola vez ──────────────────────────────


def test_sync_business_records_transition_once(monkeypatch):
    from agent import service as svc

    transitions: list[tuple] = []
    monkeypatch.setattr(db_repo, "update_candidate", lambda cid, payload: {})
    monkeypatch.setattr(db_repo, "update_conversation", lambda cid, payload: {})
    monkeypatch.setattr(db_repo, "add_state_transition",
                        lambda conv_id, a, b: transitions.append((conv_id, a, b)))
    service = svc.InterviewService(runner=None)
    state = {"phase": "interviewing", "current_idx": 0, "answers": []}

    service._sync_business({"id": "v1"}, {"id": "c1"}, {"id": "x1", "state": "greeting"}, state)
    assert transitions == [("x1", "greeting", "interviewing")]
    # Mismo estado en el turno siguiente: sin transición nueva.
    service._sync_business({"id": "v1"}, {"id": "c1"}, {"id": "x1", "state": "interviewing"}, state)
    assert len(transitions) == 1


# ── O3: métricas HTTP por ruta + RBAC del endpoint ──────────────────────────────


def test_http_metrics_accumulates_by_route():
    m = HttpMetrics()
    m.record("GET", "/api/vacancies", 200, 10.0)
    m.record("GET", "/api/vacancies", 200, 30.0)
    m.record("GET", "/api/vacancies", 500, 100.0)
    m.record("GET", "/api/vacancies", 404, 5.0)
    m.record("POST", "/api/auth/login", 200, 8.0)
    rows = m.snapshot()
    assert rows[0]["route"] == "GET /api/vacancies"   # ordenado por tráfico
    assert rows[0]["count"] == 4
    assert rows[0]["errors"] == 1 and rows[0]["client_errors"] == 1
    assert rows[0]["max_ms"] == 100
    m.reset()
    assert m.snapshot() == []


def test_http_metrics_endpoint_admin_only():
    assert client.get("/api/ops/http-metrics", headers=_auth_headers("recruiter")).status_code == 403
    r = client.get("/api/ops/http-metrics", headers=_auth_headers("admin"))
    assert r.status_code == 200
    assert isinstance(r.json()["routes"], list)
