"""Fase O-5 — Harness golden ampliado + juez de groundedness.

La CALIDAD de los prompts se valida manualmente contra el LLM real
(scripts/golden_eval.py, scripts/groundedness_judge.py); aquí se valida la
INFRAESTRUCTURA: forma del golden set, runners por suite con FakeLLM y los
helpers puros del juez.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


golden_eval = _load_script("golden_eval")
judge = _load_script("groundedness_judge")


# ── Forma del golden set ──────────────────────────────────────────────────────

def test_golden_set_shape_and_size():
    data = json.loads((ROOT / "tests" / "golden" / "golden_set.json").read_text(encoding="utf-8"))
    suites = {k: data.get(v) or [] for k, v in golden_eval.SUITE_KEYS.items()}
    total = sum(len(v) for v in suites.values())
    assert total >= 25, f"golden ampliado: se esperaban ≥25 casos, hay {total}"
    assert all(suites.values()), "toda suite debe tener al menos un caso"

    ids = [c["id"] for cases in suites.values() for c in cases]
    assert len(ids) == len(set(ids)), "IDs duplicados en el golden set"

    for c in suites["evaluate"]:
        assert {"question", "criterion", "answer"} <= set(c)
        assert 0 <= c["expected_min"] <= c["expected_max"] <= 100
    for c in suites["classify"]:
        assert c["expected"] in ("answer", "question", "offtopic")
    for c in suites["slot"]:
        assert c["expected"] is None or 0 <= c["expected"] < len(c["options"])
    for c in suites["prescreen"]:
        assert {"vacancy", "cv_profile", "criteria"} <= set(c)
        assert 0 <= c["expected_min"] <= c["expected_max"] <= 100


def test_every_suite_has_a_runner():
    assert set(golden_eval.SUITE_RUNNERS) == set(golden_eval.SUITE_KEYS)


# ── Runners con FakeLLM (sin red): cuentan fallos correctamente ───────────────

class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str) -> str:
        return self.reply


def test_run_classify_counts_failures(capsys):
    cases = [
        {"id": "a", "question": "q", "message": "m", "expected": "answer"},
        {"id": "b", "question": "q", "message": "m", "expected": "question"},
    ]
    # El fake siempre responde "answer": el caso "b" debe contarse como fallo.
    failures = golden_eval.run_classify(_FakeLLM('{"kind": "answer"}'), cases)
    assert failures == 1


def test_run_slot_counts_failures(capsys):
    cases = [
        {"id": "a", "options": ["x", "y"], "message": "la 2", "expected": 1},
        {"id": "b", "options": ["x", "y"], "message": "ninguna", "expected": None},
    ]
    failures = golden_eval.run_slot(_FakeLLM('{"choice": 2}'), cases)
    assert failures == 1  # "a" acierta (2→índice 1), "b" esperaba None


def test_run_prescreen_in_range(capsys):
    cases = [{
        "id": "a", "vacancy": {"title": "t", "requirements": "r"}, "criteria": ["c"],
        "cv_profile": {"career": "x"}, "expected_min": 60, "expected_max": 100,
    }]
    reply = '{"pre_score": 85, "summary": "ok", "per_requirement": []}'
    assert golden_eval.run_prescreen(_FakeLLM(reply), cases) == 0
    assert golden_eval.run_prescreen(_FakeLLM('{"pre_score": 10, "summary": "no", "per_requirement": []}'), cases) == 1


# ── Juez de calidad: helpers puros (evaluation/quality.py, compartido) ─────────

def test_judge_verdict_parses_grounded_and_relevance():
    from evaluation.quality import judge_verdict

    v = judge_verdict('{"grounded": true, "answer_relevant": true, "reason": "usa el aviso"}')
    assert v["grounded"] is True and v["answer_relevant"] is True and v["reason"] == "usa el aviso"
    v = judge_verdict('{"grounded": false, "answer_relevant": true, "reason": "inventa salario"}')
    assert v["grounded"] is False and v["answer_relevant"] is True
    # Veredicto ilegible → conservador: NO fundamentado NI relevante.
    v = judge_verdict("no soy json")
    assert v["grounded"] is False and v["answer_relevant"] is False and "ilegible" in v["reason"]


def test_quality_rate():
    from evaluation.quality import rate

    assert rate([True, True, False, True]) == 0.75
    assert rate([]) == 1.0
