"""Paso 5 — optimización de costos: routing de modelo por etapa + caché semántica de dudas.

Cubre: MeteredLLM multi-modelo con atribución de modelo POR ETAPA, build_stage_overrides,
best_match puro, AnswerCache (round-trip con embeddings fake) y el corto-circuito de la caché
en el nodo de entrevista (hit → sin LLM).
"""

from __future__ import annotations

import numpy as np

from agent.llm import MeteredLLM, build_stage_overrides
from src.config import Settings


class _FakeInner:
    """LLM interno fake: devuelve un texto fijo y reporta tokens/modelo."""

    def __init__(self, name: str, tokens: int = 10):
        self.model = name
        self.last_usage = {"input_tokens": tokens, "output_tokens": tokens, "total_tokens": 2 * tokens}
        self.metadata: dict[str, str] = {}
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return f"{self.model}:ok"


# ── MeteredLLM multi-modelo ───────────────────────────────────────────────────

def test_metered_routes_cheap_stage_and_attributes_model_per_stage():
    big = _FakeInner("big-model")
    cheap = _FakeInner("cheap-model")
    m = MeteredLLM(big, overrides={"classify": cheap})

    m.for_stage("classify").complete("clasificá esto")   # va al barato
    m.for_stage("evaluate").complete("evaluá esto")      # va al principal

    assert cheap.calls and not big.calls[:0]  # cheap recibió la de classify
    assert big.calls == ["evaluá esto"]
    models = m.drain_models()
    assert models["classify"] == "cheap-model" and models["evaluate"] == "big-model"


def test_metered_default_model_when_no_override():
    big = _FakeInner("big-model")
    m = MeteredLLM(big)
    m.for_stage("answer").complete("responde")
    assert m.drain_models()["answer"] == "big-model"
    assert m.model == "big-model"


def test_metered_traces_carry_per_stage_model():
    big = _FakeInner("big-model")
    cheap = _FakeInner("cheap-model")
    m = MeteredLLM(big, trace=True, overrides={"schedule": cheap})
    m.for_stage("schedule").complete("elegí opción")
    m.for_stage("evaluate").complete("evaluá")
    traces = m.drain_traces()
    by_stage = {t["stage"]: t["model"] for t in traces}
    assert by_stage == {"schedule": "cheap-model", "evaluate": "big-model"}


def test_set_context_fans_out_to_overrides():
    big = _FakeInner("big-model")
    cheap = _FakeInner("cheap-model")
    m = MeteredLLM(big, overrides={"classify": cheap})
    m.set_context(conversation_id="c1")
    assert big.metadata["conversation_id"] == "c1"
    assert cheap.metadata["conversation_id"] == "c1"


# ── build_stage_overrides ─────────────────────────────────────────────────────

def test_build_stage_overrides_empty_without_cheap_model():
    assert build_stage_overrides(Settings(llm_cheap_model="")) == {}


def test_build_stage_overrides_maps_configured_stages(monkeypatch):
    import agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "build_default_llm", lambda model=None: _FakeInner(model or "x"))
    ov = build_stage_overrides(Settings(llm_cheap_model="cheap-x", llm_cheap_stages="classify, schedule"))
    assert set(ov) == {"classify", "schedule"}
    # Una sola instancia compartida entre las etapas ruteadas.
    assert ov["classify"] is ov["schedule"] and ov["classify"].model == "cheap-x"


# ── best_match puro (semantic_cache) ──────────────────────────────────────────

def _vec(*xs) -> bytes:
    v = np.asarray(xs, dtype=np.float32)
    v = v / (np.linalg.norm(v) or 1.0)
    return v.tobytes()


def test_best_match_respects_threshold():
    from src.semantic_cache import best_match

    query = np.frombuffer(_vec(1, 0), dtype=np.float32)
    rows = [{"embedding": _vec(1, 0), "answer": "sí"}, {"embedding": _vec(0, 1), "answer": "no"}]
    hit = best_match(query, rows, 0.9)
    assert hit and hit["answer"] == "sí" and hit["score"] > 0.99
    # Vector ortogonal: nada supera el umbral.
    assert best_match(np.frombuffer(_vec(0, 1), dtype=np.float32), rows[:1], 0.9) is None


# ── AnswerCache (round-trip con embeddings fake) ──────────────────────────────

class _FakeEmbeddings:
    """embed_query determinista: hash de la pregunta → vector normalizado."""

    def embed_query(self, text: str):
        h = abs(hash(text)) % 997
        v = np.asarray([np.cos(h), np.sin(h), 1.0], dtype=np.float32)
        return (v / np.linalg.norm(v)).tolist()


def test_answer_cache_roundtrip_and_gating(monkeypatch, tmp_path):
    import src.embeddings as emb
    from agent.answer_cache import build_answer_cache

    monkeypatch.setattr(emb, "get_embeddings", lambda *a, **k: _FakeEmbeddings())
    db = str(tmp_path / "ac.db")

    assert build_answer_cache(Settings(interview_answer_cache_enabled=False)) is None
    cache = build_answer_cache(Settings(
        interview_answer_cache_enabled=True, interview_answer_cache_db=db, semantic_cache_threshold=0.99
    ))
    assert cache is not None

    # Miss inicial, luego store, luego hit exacto.
    assert cache.lookup("vac1", "¿Cuál es el sueldo?") is None
    cache.store("vac1", "¿Cuál es el sueldo?", "El rango se confirma en la entrevista.")
    assert cache.lookup("vac1", "¿Cuál es el sueldo?") == "El rango se confirma en la entrevista."
    # Aislado por vacante: otra vacante no ve el hit.
    assert cache.lookup("vac2", "¿Cuál es el sueldo?") is None


# ── Corto-circuito en el nodo de entrevista ───────────────────────────────────

class _SpyCache:
    def __init__(self, hit=None):
        self._hit = hit
        self.stored: list = []

    def lookup(self, vacancy_id, question):
        return self._hit

    def store(self, vacancy_id, question, answer):
        self.stored.append((vacancy_id, question, answer))


def _interview_state():
    return {
        "outbound": [],
        "questions_asked": 0,
        "vacancy": {"id": "vac1", "company_info": "info"},
    }


def test_answer_cache_hit_short_circuits_llm(monkeypatch):
    import agent.nodes as nodes

    monkeypatch.setattr(nodes, "is_meaningful_answer", lambda t: True)
    monkeypatch.setattr(nodes, "classify_turn", lambda llm, **k: "question")
    monkeypatch.setattr(nodes, "current_question", lambda s: {"text": "¿Experiencia?"})
    spy_llm_called = []
    monkeypatch.setattr(nodes, "answer_candidate_question",
                        lambda llm, **k: spy_llm_called.append(1) or "generada")

    state = _interview_state()
    cache = _SpyCache(hit="cacheada")
    nodes._handle_interview(state, llm=object(), text="¿beneficios?", answer_cache=cache)
    assert "cacheada" in state["outbound"][0]
    assert spy_llm_called == [] and cache.stored == []  # no LLM, no store en hit


def test_answer_cache_miss_generates_and_stores(monkeypatch):
    import agent.nodes as nodes

    monkeypatch.setattr(nodes, "is_meaningful_answer", lambda t: True)
    monkeypatch.setattr(nodes, "classify_turn", lambda llm, **k: "question")
    monkeypatch.setattr(nodes, "current_question", lambda s: {"text": "¿Experiencia?"})
    monkeypatch.setattr(nodes, "answer_candidate_question", lambda llm, **k: "generada")

    state = _interview_state()
    cache = _SpyCache(hit=None)
    nodes._handle_interview(state, llm=object(), text="¿beneficios?", answer_cache=cache)
    assert "generada" in state["outbound"][0]
    assert cache.stored == [("vac1", "¿beneficios?", "generada")]
