"""Roadmap v2, paso 1 — guard del "perfil de producción" (auditoria_v2 · Riesgo 1).

`warn_production_profile` AVISA (no bloquea) si en producción los signos vitales de
calidad/costo nacen apagados. Fuera de producción no dice nada. Todo con `Settings`
construido explícito (sin DB, sin .env real).
"""

from __future__ import annotations

from api.runtime import warn_production_profile
from src.config import Settings

_ALL_ON = {
    "environment": "production",
    "llm_trace_enabled": True,
    "llm_cheap_model": "llama-3.1-8b-instant",
    "interview_answer_cache_enabled": True,
}


def _settings(**overrides) -> Settings:
    base = dict(_ALL_ON)
    base.update(overrides)
    return Settings(**base)


def test_prod_all_on_no_warnings():
    assert warn_production_profile(_settings()) == []


def test_dev_never_warns_even_with_everything_off():
    s = _settings(
        environment="development",
        llm_trace_enabled=False,
        llm_cheap_model="",
        interview_answer_cache_enabled=False,
    )
    assert warn_production_profile(s) == []


def test_prod_warns_when_traces_off():
    warnings = warn_production_profile(_settings(llm_trace_enabled=False))
    assert len(warnings) == 1
    assert "LLM_TRACE_ENABLED" in warnings[0]


def test_prod_warns_when_cheap_model_empty():
    warnings = warn_production_profile(_settings(llm_cheap_model=""))
    assert len(warnings) == 1
    assert "LLM_CHEAP_MODEL" in warnings[0]


def test_prod_warns_when_cache_off():
    warnings = warn_production_profile(_settings(interview_answer_cache_enabled=False))
    assert len(warnings) == 1
    assert "ANSWER_CACHE" in warnings[0]


def test_prod_all_off_warns_thrice_and_does_not_raise():
    s = _settings(
        llm_trace_enabled=False, llm_cheap_model="", interview_answer_cache_enabled=False
    )
    warnings = warn_production_profile(s)  # no lanza
    assert len(warnings) == 3
