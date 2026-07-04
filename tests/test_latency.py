"""Fase O-3 — Percentiles p95/p99 + latencia end-to-end del turno del candidato.

Cubre:
  - HttpMetrics: histograma de buckets → p95/p99 por ruta (overflow reporta max_ms).
  - `_latency_summary`/`_aggregate_tokens`: percentiles por etapa desde filas de
    llm_usage; las filas stage="turn" quedan FUERA de los agregados de tokens/LLM.
  - `record_usage`: una fila solo-latencia (stage="turn", 0 tokens) SÍ se registra.
  - `InterviewService.process()`: registra la fila stage="turn" del turno.
"""

from __future__ import annotations

import agente.service as svc
from agente.service import InterviewService
from observabilidad.httpmetrics import HttpMetrics, percentile_from_buckets
from channels.base import InboundMessage
from db import repositories
from db.repositories import TURN_STAGE, _aggregate_tokens, _latency_summary, _percentile


# ── HttpMetrics: percentiles desde buckets ────────────────────────────────────

def test_http_metrics_percentiles_from_buckets():
    m = HttpMetrics()
    # 98 requests rápidos (≤10 ms) + 2 lentos (~2 s): p95 queda en el bucket bajo,
    # p99 alcanza el bucket de los lentos (techo 2500 ms).
    for _ in range(98):
        m.record("GET", "/api/x", 200, 8.0)
    m.record("GET", "/api/x", 200, 2000.0)
    m.record("GET", "/api/x", 200, 2200.0)
    row = m.snapshot()[0]
    assert row["count"] == 100
    assert row["p95_ms"] == 10  # techo del bucket de los rápidos
    assert row["p99_ms"] == 2500  # techo del bucket de los lentos
    assert row["max_ms"] == 2200


def test_http_metrics_overflow_bucket_reports_max():
    m = HttpMetrics()
    m.record("GET", "/api/slow", 200, 45000.0)  # > último techo (10 s) → desborde
    row = m.snapshot()[0]
    assert row["p95_ms"] == 45000 and row["p99_ms"] == 45000


def test_percentile_from_buckets_empty_is_zero():
    assert percentile_from_buckets([0] * 12, 95, 0.0) == 0


# ── Percentiles LLM desde filas de llm_usage ──────────────────────────────────

def test_percentile_nearest_rank():
    samples = sorted(float(v) for v in range(1, 101))  # 1..100
    assert _percentile(samples, 50) == 50.0
    assert _percentile(samples, 95) == 95.0
    assert _percentile(samples, 99) == 99.0
    assert _percentile([], 95) == 0.0


def test_latency_summary_weights_by_calls_and_includes_turn():
    rows = [
        # 2 llamadas que sumaron 200 ms → dos muestras de 100 ms.
        {"stage": "evaluate", "calls": 2, "duration_ms": 200},
        {"stage": "evaluate", "calls": 1, "duration_ms": 900},
        {"stage": TURN_STAGE, "calls": 1, "duration_ms": 1500},
        {"stage": "classify", "calls": 0, "duration_ms": 0},  # sin llamadas: fuera
    ]
    lat = _latency_summary(rows)
    assert lat["evaluate"]["calls"] == 3
    assert lat["evaluate"]["p50_ms"] == 100
    assert lat["evaluate"]["p99_ms"] == 900
    assert lat[TURN_STAGE] == {
        "calls": 1, "avg_ms": 1500, "p50_ms": 1500, "p95_ms": 1500, "p99_ms": 1500,
    }
    assert "classify" not in lat


def test_aggregate_tokens_excludes_turn_from_llm_aggregates():
    rows = [
        {"stage": "evaluate", "model": "m1", "input_tokens": 100, "output_tokens": 20,
         "total_tokens": 120, "calls": 1, "errors": 0, "duration_ms": 400},
        {"stage": TURN_STAGE, "model": "m1", "input_tokens": 0, "output_tokens": 0,
         "total_tokens": 0, "calls": 1, "errors": 0, "duration_ms": 3000},
    ]
    agg = _aggregate_tokens(rows)
    # Tokens/llamadas/latencia LLM: solo la fila de evaluate.
    assert agg["total"] == 120 and agg["calls"] == 1 and agg["avg_ms"] == 400
    assert TURN_STAGE not in agg["by_stage"]
    assert agg["by_model"] == {"m1": {"input": 100, "output": 20, "total": 120}}
    # El bloque latency sí resume ambas etapas.
    assert agg["latency"]["evaluate"]["p95_ms"] == 400
    assert agg["latency"][TURN_STAGE]["p95_ms"] == 3000


# ── record_usage: la fila solo-latencia se persiste ───────────────────────────

class _FakeTable:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def insert(self, row):
        self._sink.append(row)
        return self

    def execute(self):
        return self


class _FakeClient:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def table(self, name):
        return _FakeTable(self._sink)


def test_record_usage_persists_latency_only_row(monkeypatch):
    inserted: list = []
    monkeypatch.setattr(repositories, "get_supabase", lambda: _FakeClient(inserted))
    repositories.record_usage(TURN_STAGE, "m1", {"calls": 1, "duration_ms": 850}, vacancy_id="v1")
    assert len(inserted) == 1
    assert inserted[0]["stage"] == TURN_STAGE
    assert inserted[0]["duration_ms"] == 850 and inserted[0]["total_tokens"] == 0


def test_record_usage_still_skips_empty_rows(monkeypatch):
    inserted: list = []
    monkeypatch.setattr(repositories, "get_supabase", lambda: _FakeClient(inserted))
    repositories.record_usage("evaluate", "m1", {"calls": 0, "duration_ms": 0})
    assert inserted == []


# ── El servicio registra la latencia end-to-end del turno ─────────────────────

class _FakeRunner:
    llm = None  # sin instrumentación: la fila "turn" no depende del metering

    def get_state(self, thread_id):
        return {}

    def start(self, thread_id, vacancy, questions, cv_profile=None):
        return {"phase": "greeting", "outbound": ["¡Hola!"]}


def test_process_records_turn_latency(monkeypatch):
    repo = svc.repositories
    vacancy = {"id": "v1", "status": "open", "tenant_id": "t1", "title": "Demo"}
    monkeypatch.setattr(repo, "get_conversation_by_thread", lambda t: None)
    monkeypatch.setattr(repo, "get_default_open_vacancy", lambda: vacancy)
    monkeypatch.setattr(repo, "get_vacancy_questions", lambda vid: [])
    monkeypatch.setattr(
        repo, "get_or_create_candidate",
        lambda *a, **k: {"id": "c1", "cv_profile": {}},
    )
    monkeypatch.setattr(
        repo, "get_or_create_conversation",
        lambda *a, **k: {"id": "conv1", "candidate_id": "c1", "vacancy_id": "v1"},
    )
    monkeypatch.setattr(repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(repo, "update_conversation", lambda cid, p: p)
    monkeypatch.setattr(repo, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(repo, "add_state_transition", lambda *a, **k: None)
    monkeypatch.setattr(repo, "get_scorecard", lambda cid: None)
    usages: list = []
    monkeypatch.setattr(
        repo, "record_usage",
        lambda stage, model, tokens, **kw: usages.append((stage, tokens, kw)),
    )

    InterviewService(_FakeRunner()).process(
        InboundMessage(channel="telegram", chat_id="99", text=None)
    )

    turn_rows = [u for u in usages if u[0] == TURN_STAGE]
    assert len(turn_rows) == 1
    stage, tokens, kw = turn_rows[0]
    assert tokens["calls"] == 1 and tokens["duration_ms"] >= 1
    assert kw["vacancy_id"] == "v1" and kw["candidate_id"] == "c1"
    assert kw["conversation_id"] == "conv1"
