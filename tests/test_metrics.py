"""Tests de métricas: embudo de candidatos (puro) y metering de tokens."""

from __future__ import annotations

from agent.llm import MeteredLLM
from db.repositories import _aggregate_tokens, _funnel


def test_funnel_counts_by_stage_and_verdict():
    cands = [
        {"status": "prescreen_rejected", "prescreen": {"verdict": "reject"}},
        {"status": "invited", "prescreen": {"verdict": "pass"}},
        {"status": "interviewing", "prescreen": {"verdict": "borderline"}},
        {"status": "finished", "prescreen": {"verdict": "pass"}},
        {"status": "advanced", "prescreen": {"verdict": "pass"}},
        {"status": "pending"},  # candidato directo por Telegram (sin prescreen)
    ]
    f = _funnel(cands)
    assert f["imported"] == 5  # los que tienen verdict
    assert f["prescreen_rejected"] == 1
    assert f["prescreen_passed"] == 4
    assert f["invited"] == 1
    assert f["interviewing"] == 1
    assert f["finished"] == 1
    assert f["advanced"] == 1


def test_aggregate_tokens_sums_and_groups_by_stage():
    rows = [
        {"stage": "prescreen", "input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        {"stage": "evaluate", "input_tokens": 200, "output_tokens": 50, "total_tokens": 250},
        {"stage": "evaluate", "input_tokens": 80, "output_tokens": 10, "total_tokens": 90},
    ]
    agg = _aggregate_tokens(rows)
    assert agg["total"] == 460
    assert agg["input"] == 380
    assert agg["output"] == 80
    assert agg["by_stage"]["evaluate"] == 340
    assert agg["by_stage"]["prescreen"] == 120


class _FakeInner:
    """LLM fake que expone last_usage como el LangChainLLM real."""

    model = "fake-model"

    def __init__(self):
        self.last_usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    def complete(self, prompt: str) -> str:
        return "{}"


def test_metered_llm_accumulates_by_stage_and_drains():
    m = MeteredLLM(_FakeInner())
    m.for_stage("prescreen").complete("a")
    m.for_stage("evaluate").complete("b")
    m.for_stage("evaluate").complete("c")
    drained = m.drain()
    assert drained["prescreen"]["total_tokens"] == 15
    assert drained["evaluate"]["total_tokens"] == 30
    # drain limpia el acumulado
    assert m.drain() == {}
