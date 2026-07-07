"""Dimensión B — Series de tiempo de observabilidad (dashboards de tendencias).

Cubre las funciones PURAS de agregación diaria del endpoint `/api/ops/timeseries`
(sin DB): operación LLM (throughput/tokens/costo/latencia), calidad esporádica y
volumen HTTP derivado como delta reset-aware entre snapshots acumulados.
"""

from __future__ import annotations

from datetime import date, timezone

from api.routes.timeseries import (
    _day_keys,
    http_delta_series,
    llm_daily_series,
    quality_series,
)

TZ = timezone.utc
PRICING = {
    "models": {"m1": {"input_per_1m": 1.0, "output_per_1m": 2.0}},
    "default": {"input_per_1m": 0.0, "output_per_1m": 0.0},
}


def _keys():
    return _day_keys(date(2026, 7, 1), 3)  # 2026-07-01, -02, -03


# ── Eje de días ───────────────────────────────────────────────────────────────

def test_day_keys_is_continuous_and_iso():
    assert _day_keys(date(2026, 7, 1), 3) == ["2026-07-01", "2026-07-02", "2026-07-03"]


# ── LLM diario ────────────────────────────────────────────────────────────────

def test_llm_daily_aggregates_and_isolates_tenant():
    keys = _keys()
    rows = [
        # dentro del tenant (vac A), día 1: 2 calls, 1 error, tokens/modelo
        {"vacancy_id": "A", "stage": "evaluate", "calls": 2, "errors": 1, "duration_ms": 400,
         "input_tokens": 100, "output_tokens": 50, "total_tokens": 150, "model": "m1",
         "created_at": "2026-07-01T10:00:00+00:00"},
        # etapa sintética "turn" → EXCLUIDA de tokens/calls
        {"vacancy_id": "A", "stage": "turn", "calls": 1, "errors": 0, "duration_ms": 5000,
         "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "model": "",
         "created_at": "2026-07-01T11:00:00+00:00"},
        # otro tenant (vac Z) → EXCLUIDA por aislamiento
        {"vacancy_id": "Z", "stage": "evaluate", "calls": 9, "errors": 9, "duration_ms": 9,
         "input_tokens": 999, "output_tokens": 999, "total_tokens": 999, "model": "m1",
         "created_at": "2026-07-01T12:00:00+00:00"},
    ]
    out = llm_daily_series(rows, {"A"}, TZ, keys, PRICING)
    d1 = next(p for p in out if p["day"] == "2026-07-01")
    assert d1["calls"] == 2
    assert d1["errors"] == 1
    assert d1["tokens"] == 150
    assert d1["avg_ms"] == 200  # 400ms / 2 calls
    # costo: 100/1M*$1 + 50/1M*$2 = 0.0001 + 0.0001 = 0.0002
    assert d1["cost"] == 0.0002
    # días sin filas → ceros continuos
    assert [p["day"] for p in out] == keys
    assert next(p for p in out if p["day"] == "2026-07-02")["calls"] == 0


def test_llm_daily_ignores_rows_outside_window():
    keys = _keys()
    rows = [{"vacancy_id": "A", "stage": "evaluate", "calls": 5, "errors": 0, "duration_ms": 10,
             "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "model": "m1",
             "created_at": "2026-06-15T10:00:00+00:00"}]  # fuera de la ventana
    out = llm_daily_series(rows, {"A"}, TZ, keys, PRICING)
    assert sum(p["calls"] for p in out) == 0


# ── Calidad esporádica ────────────────────────────────────────────────────────

def test_quality_series_groups_by_metric_no_fill():
    keys = _keys()
    rows = [
        {"metric": "grounded", "day": "2026-07-01", "rate": 0.9, "sample_size": 20, "threshold": 0.9},
        {"metric": "grounded", "day": "2026-07-03", "rate": 0.8, "sample_size": 15, "threshold": 0.9},
        {"metric": "answer_relevance", "day": "2026-07-02", "rate": 1.0, "sample_size": 10, "threshold": 0.8},
        {"metric": "grounded", "day": "2026-06-01", "rate": 0.5, "sample_size": 5, "threshold": 0.9},  # fuera
    ]
    out = quality_series(rows, keys)
    # NO rellena días faltantes: solo puntos reales, ascendente
    assert [p["day"] for p in out["grounded"]] == ["2026-07-01", "2026-07-03"]
    assert out["grounded"][0]["rate"] == 0.9
    assert [p["day"] for p in out["answer_relevance"]] == ["2026-07-02"]


# ── HTTP delta reset-aware ────────────────────────────────────────────────────

def test_http_delta_between_consecutive_snapshots():
    keys = _keys()
    snaps = [
        # baseline día 1 (sin intervalo previo → no cuenta)
        {"route": "/api/x", "taken_at": "2026-07-01T08:00:00+00:00", "count": 100, "errors": 2, "client_errors": 1, "p95_ms": 50},
        # día 2: +50 requests, +1 error respecto al baseline
        {"route": "/api/x", "taken_at": "2026-07-02T08:00:00+00:00", "count": 150, "errors": 3, "client_errors": 1, "p95_ms": 80},
    ]
    out = http_delta_series(snaps, TZ, keys)
    d2 = next(p for p in out if p["day"] == "2026-07-02")
    assert d2["requests"] == 50
    assert d2["errors"] == 1
    assert d2["peak_p95_ms"] == 80
    # el baseline no aporta volumen al día 1
    assert next(p for p in out if p["day"] == "2026-07-01")["requests"] == 0


def test_http_delta_detects_process_restart():
    keys = _keys()
    snaps = [
        {"route": "/api/x", "taken_at": "2026-07-02T06:00:00+00:00", "count": 500, "errors": 10, "client_errors": 0, "p95_ms": 40},
        # reinicio: contador vuelve a bajo → delta negativo → se usa el contador nuevo tal cual
        {"route": "/api/x", "taken_at": "2026-07-02T09:00:00+00:00", "count": 30, "errors": 1, "client_errors": 0, "p95_ms": 20},
    ]
    out = http_delta_series(snaps, TZ, keys)
    d2 = next(p for p in out if p["day"] == "2026-07-02")
    assert d2["requests"] == 30  # no negativo: cuenta el contador nuevo tras el reset
    assert d2["errors"] == 1


def test_http_delta_multiple_routes_independent():
    keys = _keys()
    snaps = [
        {"route": "/a", "taken_at": "2026-07-01T08:00:00+00:00", "count": 10, "errors": 0, "client_errors": 0, "p95_ms": 10},
        {"route": "/a", "taken_at": "2026-07-02T08:00:00+00:00", "count": 40, "errors": 0, "client_errors": 0, "p95_ms": 10},
        {"route": "/b", "taken_at": "2026-07-01T08:00:00+00:00", "count": 100, "errors": 0, "client_errors": 0, "p95_ms": 10},
        {"route": "/b", "taken_at": "2026-07-02T08:00:00+00:00", "count": 105, "errors": 0, "client_errors": 0, "p95_ms": 10},
    ]
    out = http_delta_series(snaps, TZ, keys)
    d2 = next(p for p in out if p["day"] == "2026-07-02")
    assert d2["requests"] == 35  # 30 de /a + 5 de /b
