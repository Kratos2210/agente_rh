"""Pipeline LLM (auditoría e2e · sección 7): prompt_version + RAG inyectable + golden set.

  - El scorecard sella `prompt_version` (comparabilidad al cambiar prompts).
  - El retriever RAG se inyecta como el LLM (motor puro): sus fragmentos llegan al
    prompt de dudas; sin retriever el comportamiento es el previo; un retriever roto
    no tumba el turno.
  - `build_company_retriever` respeta el gate de config y degrada fail-safe.
  - El golden set existe y está bien formado (la corrida real es manual:
    scripts/golden_eval.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.graph import make_memory_runner
from agent.prompts import PROMPT_VERSION
from evaluation.scorecard import build_scorecard
from tests.test_interview import FakeLLM, _questions, _vacancy


# ── prompt_version en el scorecard ─────────────────────────────────────────────


def test_scorecard_carries_prompt_version():
    answers = [{"criterion": "c", "question": "q", "score": 80.0, "weight": 1.0,
                "justification": "ok", "raw_answer": "r", "question_id": "q1", "label": ""}]
    sc = build_scorecard(answers, vacancy_title="V", green_min=75, yellow_min=50, llm=None)
    assert sc["prompt_version"] == PROMPT_VERSION
    assert PROMPT_VERSION  # no vacío: el sello debe ser significativo


# ── RAG inyectable en las dudas del candidato ──────────────────────────────────


def _run_question_turn(retriever):
    """Arranca una entrevista con FakeLLM (clasifica '?' como duda) y envía una duda."""
    llm = FakeLLM(classify=lambda m: "question" if "?" in m else "answer")
    runner = make_memory_runner(llm, retriever)
    runner.start("t1", _vacancy(), _questions(n=1))
    runner.send("t1", button="accept")
    runner.send("t1", text="¿La empresa da capacitaciones?")
    return llm


def test_retriever_context_reaches_answer_prompt():
    llm = _run_question_turn(lambda q: "DATO-RAG-UNICO: sí, hay capacitaciones anuales.")
    answer_prompts = [p for p in llm.calls if "DATO-RAG-UNICO" in p]
    assert answer_prompts, "el contexto recuperado por el RAG debe llegar al prompt de dudas"
    # El company_info de la vacante se conserva (el RAG SUMA, no reemplaza).
    assert any("Empresa retail" in p for p in answer_prompts)


def test_without_retriever_behavior_unchanged():
    llm = _run_question_turn(None)
    assert not any("Base de conocimiento" in p for p in llm.calls)


def test_broken_retriever_does_not_break_turn():
    def boom(q):
        raise RuntimeError("chroma caído")

    llm = _run_question_turn(boom)  # no debe lanzar
    assert any('"kind"' in p for p in llm.calls)  # el turno se procesó igual


# ── build_company_retriever: gate de config + degradación ──────────────────────


def test_retriever_disabled_by_default():
    from agent.rag import build_company_retriever
    from src.config import Settings

    assert build_company_retriever(Settings(interview_rag_enabled=False)) is None


def test_retriever_fails_safe_and_does_not_retry(monkeypatch):
    from agent import rag
    from src.config import Settings

    retrieve = rag.build_company_retriever(Settings(interview_rag_enabled=True))
    assert retrieve is not None

    calls = {"n": 0}

    def fake_build(settings):
        calls["n"] += 1
        raise RuntimeError("sin corpus indexado")

    import src.vectorstore as vs

    monkeypatch.setattr(vs, "build_vectorstore", fake_build)
    assert retrieve("¿pregunta?") == ""   # degrada a vacío
    assert retrieve("¿otra?") == ""       # marcado failed: no reintenta
    assert calls["n"] == 1


# ── Golden set: existe y está bien formado ─────────────────────────────────────


def test_golden_set_is_well_formed():
    path = Path(__file__).parent / "golden" / "golden_set.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) >= 6
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))  # sin duplicados
    for c in cases:
        assert c["question"] and c["criterion"] and c["answer"]
        assert 0 <= c["expected_min"] <= c["expected_max"] <= 100
    # Debe incluir contraejemplos (rangos bajos), no solo respuestas buenas.
    assert any(c["expected_max"] <= 60 for c in cases)
