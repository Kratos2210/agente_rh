"""Fase O-6 — Logs JSON + request-id, Sentry config-gated y snapshot HTTP a DB."""

from __future__ import annotations

import json
import logging
import sys
import types
from types import SimpleNamespace

import api.main as main
import api.scheduler as scheduler
from observabilidad.httpmetrics import http_metrics
from fastapi.testclient import TestClient
from core.logging_config import JsonFormatter, get_request_id, set_request_id

client = TestClient(main.app)


# ── Logs JSON + request-id ────────────────────────────────────────────────────

def test_json_formatter_emits_parseable_json_with_request_id():
    set_request_id("req-abc")
    try:
        record = logging.LogRecord(
            name="api.test", level=logging.WARNING, pathname=__file__, lineno=1,
            msg="algo pasó con %s", args=("la vacante",), exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "api.test"
        assert payload["message"] == "algo pasó con la vacante"
        assert payload["request_id"] == "req-abc"
        assert "ts" in payload
    finally:
        set_request_id("-")


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="api.test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="falló", args=(), exc_info=sys.exc_info(),
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


def test_request_id_middleware_generates_and_propagates():
    r = client.get("/api/health")
    assert r.status_code == 200
    generated = r.headers.get("x-request-id", "")
    assert generated and len(generated) >= 8
    # El header entrante (gateway/proxy) se propaga tal cual.
    r2 = client.get("/api/health", headers={"X-Request-ID": "gw-12345"})
    assert r2.headers.get("x-request-id") == "gw-12345"
    # Fuera del request, el contextvar vuelve al default.
    assert get_request_id() == "-"


# ── Sentry config-gated ───────────────────────────────────────────────────────

def test_init_sentry_noop_without_dsn():
    from api.runtime import init_sentry

    assert init_sentry(SimpleNamespace(sentry_dsn="", environment="development",
                                       sentry_traces_sample_rate=0.0)) is False


def test_init_sentry_initializes_with_dsn(monkeypatch):
    from api.runtime import init_sentry

    seen: dict = {}
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kw: seen.update(kw)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)

    cfg = SimpleNamespace(sentry_dsn="https://x@sentry.io/1", environment="production",
                          sentry_traces_sample_rate=0.2)
    assert init_sentry(cfg) is True
    assert seen["dsn"] == "https://x@sentry.io/1"
    assert seen["environment"] == "production"
    assert seen["traces_sample_rate"] == 0.2
    assert seen["send_default_pii"] is False  # PII fuera de Sentry (Ley 29733)


# ── Phoenix (Arize) config-gated ──────────────────────────────────────────────

def test_init_phoenix_noop_when_disabled():
    from api.runtime import init_phoenix

    assert init_phoenix(SimpleNamespace(phoenix_enabled=False)) is False


def test_init_phoenix_registers_and_instruments(monkeypatch):
    from api.runtime import init_phoenix

    seen: dict = {}

    fake_otel = types.ModuleType("phoenix.otel")
    fake_otel.register = lambda **kw: seen.update(kw) or "TP"
    fake_phoenix = types.ModuleType("phoenix")
    fake_phoenix.otel = fake_otel

    instrumented: dict = {}

    class FakeInstrumentor:
        def instrument(self, tracer_provider=None):
            instrumented["tp"] = tracer_provider

    fake_oi = types.ModuleType("openinference.instrumentation.langchain")
    fake_oi.LangChainInstrumentor = FakeInstrumentor
    monkeypatch.setitem(sys.modules, "phoenix", fake_phoenix)
    monkeypatch.setitem(sys.modules, "phoenix.otel", fake_otel)
    monkeypatch.setitem(sys.modules, "openinference.instrumentation.langchain", fake_oi)

    cfg = SimpleNamespace(phoenix_enabled=True, phoenix_project="p",
                          phoenix_endpoint="http://x/v1/traces")
    assert init_phoenix(cfg) is True
    assert seen["project_name"] == "p" and seen["endpoint"] == "http://x/v1/traces"
    assert seen["set_global_tracer_provider"] is False
    assert instrumented["tp"] == "TP"  # el instrumentor recibió el provider


def test_init_phoenix_best_effort_on_failure(monkeypatch):
    from api.runtime import init_phoenix

    broken = types.ModuleType("phoenix.otel")

    def boom(**kw):
        raise RuntimeError("endpoint inválido")

    broken.register = boom
    fake_phoenix = types.ModuleType("phoenix")
    fake_phoenix.otel = broken
    monkeypatch.setitem(sys.modules, "phoenix", fake_phoenix)
    monkeypatch.setitem(sys.modules, "phoenix.otel", broken)

    cfg = SimpleNamespace(phoenix_enabled=True, phoenix_project="p", phoenix_endpoint="x")
    assert init_phoenix(cfg) is False  # no tumba el arranque


# ── Snapshot de métricas HTTP a DB ────────────────────────────────────────────

def _snap_settings(minutes=60, days=14):
    return SimpleNamespace(http_snapshot_minutes=minutes, http_snapshot_retention_days=days)


def test_http_snapshot_sweep_saves_and_prunes(monkeypatch):
    http_metrics.reset()
    http_metrics.record("GET", "/api/x", 200, 12.0)
    saved: list = []
    pruned: list = []
    monkeypatch.setattr(scheduler.repo, "save_http_snapshot", lambda rows: saved.extend(rows))
    monkeypatch.setattr(scheduler.repo, "prune_http_snapshots", lambda before: pruned.append(before))
    scheduler._state.pop("http_snapshot_last", None)

    report = scheduler._http_snapshot_sweep(_snap_settings())
    assert report["saved"] == 1
    assert saved[0]["route"] == "GET /api/x" and saved[0]["count"] == 1
    assert {"p95_ms", "p99_ms", "avg_ms", "max_ms"} <= set(saved[0])
    assert len(pruned) == 1  # retención activa → poda con timestamp ISO
    assert "T" in pruned[0]

    # Dentro del intervalo: no vuelve a volcar.
    assert scheduler._http_snapshot_sweep(_snap_settings())["saved"] == 0
    scheduler._state.pop("http_snapshot_last", None)
    http_metrics.reset()


def test_http_snapshot_sweep_disabled_and_no_prune_when_zero(monkeypatch):
    http_metrics.reset()
    http_metrics.record("GET", "/api/x", 200, 12.0)
    saved: list = []
    pruned: list = []
    monkeypatch.setattr(scheduler.repo, "save_http_snapshot", lambda rows: saved.extend(rows))
    monkeypatch.setattr(scheduler.repo, "prune_http_snapshots", lambda before: pruned.append(before))

    scheduler._state.pop("http_snapshot_last", None)
    assert scheduler._http_snapshot_sweep(_snap_settings(minutes=0))["saved"] == 0
    assert saved == []  # apagado: ni escribe ni poda
    assert pruned == []

    # Retención 0 = sin poda (pero sí escribe).
    scheduler._state.pop("http_snapshot_last", None)
    assert scheduler._http_snapshot_sweep(_snap_settings(days=0))["saved"] == 1
    assert pruned == []
    scheduler._state.pop("http_snapshot_last", None)
    http_metrics.reset()
