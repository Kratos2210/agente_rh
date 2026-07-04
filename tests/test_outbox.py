"""Fase 1 — outbox durable (reintentos + dead-letter) y reconciliación.

Prueba la lógica sin base de datos: política de backoff (pura), y deliver/drain con
un handler falso y los repos monkeypatcheados.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import api.main as main
import db.repositories as repo
from agente.state import PHASE_SCHEDULING
from notifications import outbox
from core.config import Settings

S = Settings()
NOW = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


# ── Política de reintentos (pura) ─────────────────────────────────────────────

def test_backoff_monotonic_and_capped():
    vals = [outbox.backoff_seconds(i) for i in range(1, 9)]
    assert vals[0] == 60
    assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
    assert vals[-1] == outbox._BACKOFF[-1]  # cap en el último valor


def test_next_state_after_failure():
    assert outbox.next_state_after_failure(1, 6)[0] == "pending"
    assert outbox.next_state_after_failure(5, 6)[0] == "pending"
    assert outbox.next_state_after_failure(6, 6) == ("failed", 0)
    assert outbox.next_state_after_failure(9, 6)[0] == "failed"


# ── deliver: éxito no encola; fallo encola pending ────────────────────────────

def test_deliver_success_does_not_enqueue(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(repo, "enqueue_outbox", lambda row: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setitem(outbox._HANDLERS, "test_ok", lambda s, p: None)
    assert outbox.deliver(S, "test_ok", {"x": 1}) is True
    assert calls["n"] == 0


def test_deliver_failure_enqueues_pending(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(repo, "enqueue_outbox", lambda row: captured.update(row))

    def boom(s, p):
        raise RuntimeError("smtp caído")

    monkeypatch.setitem(outbox._HANDLERS, "test_fail", boom)
    assert outbox.deliver(S, "test_fail", {"x": 1}, candidate_id="c1", tenant_id="t1") is False
    assert captured["status"] == "pending"
    assert captured["attempts"] == 1
    assert captured["kind"] == "test_fail"
    assert captured["candidate_id"] == "c1"
    assert "smtp caído" in captured["last_error"]


# ── drain: reintenta, dead-letter al agotar, éxito marca sent ─────────────────

def test_drain_dead_letter_on_exhaustion(monkeypatch):
    updates: list = []
    row = {"id": "o1", "kind": "test_fail", "payload": {}, "attempts": 5, "max_attempts": 6}
    monkeypatch.setattr(repo, "list_due_outbox", lambda now_iso, limit=50: [row])
    monkeypatch.setattr(repo, "update_outbox", lambda oid, payload: updates.append((oid, payload)))
    monkeypatch.setitem(outbox._HANDLERS, "test_fail", lambda s, p: (_ for _ in ()).throw(RuntimeError("sigue caído")))
    report = outbox.drain(S, now=NOW)
    assert report["dead"] == 1 and report["retry"] == 0
    assert updates[0][1]["status"] == "failed"
    assert updates[0][1]["attempts"] == 6


def test_drain_reschedules_when_not_exhausted(monkeypatch):
    updates: list = []
    row = {"id": "o2", "kind": "test_fail", "payload": {}, "attempts": 1, "max_attempts": 6}
    monkeypatch.setattr(repo, "list_due_outbox", lambda now_iso, limit=50: [row])
    monkeypatch.setattr(repo, "update_outbox", lambda oid, payload: updates.append((oid, payload)))
    monkeypatch.setitem(outbox._HANDLERS, "test_fail", lambda s, p: (_ for _ in ()).throw(RuntimeError("x")))
    report = outbox.drain(S, now=NOW)
    assert report["retry"] == 1 and report["dead"] == 0
    assert updates[0][1]["status"] == "pending" and updates[0][1]["attempts"] == 2


def test_drain_success_marks_sent(monkeypatch):
    updates: list = []
    row = {"id": "o3", "kind": "test_ok", "payload": {}, "attempts": 0, "max_attempts": 6}
    monkeypatch.setattr(repo, "list_due_outbox", lambda now_iso, limit=50: [row])
    monkeypatch.setattr(repo, "update_outbox", lambda oid, payload: updates.append((oid, payload)))
    monkeypatch.setitem(outbox._HANDLERS, "test_ok", lambda s, p: None)
    report = outbox.drain(S, now=NOW)
    assert report["sent"] == 1
    assert updates[0][1]["status"] == "sent"


# ── Reconciliación: coordinaciones de horario estancadas (pura) ───────────────

def test_reconcile_scheduling_stuck():
    old = (NOW - timedelta(hours=30)).isoformat()
    fresh = (NOW - timedelta(minutes=5)).isoformat()
    convs = [
        {"id": "c1", "state": PHASE_SCHEDULING, "last_activity_at": old},    # estancada
        {"id": "c2", "state": PHASE_SCHEDULING, "last_activity_at": fresh},  # reciente → no
        {"id": "c3", "state": PHASE_SCHEDULING, "last_activity_at": old},    # con reunión → no
        {"id": "c4", "state": "interviewing", "last_activity_at": old},      # otra fase → no
    ]
    stuck = main._reconcile_scheduling_stuck(convs, {"c3"}, NOW, 24 * 3600)
    assert stuck == ["c1"]
