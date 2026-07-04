"""setup_tracing: activación + ocultamiento de PII (privacidad Ley 29733).

LangSmith es un SaaS en la nube y sus trazas incluyen el prompt/respuesta (PII del
candidato). Estos tests fijan el contrato: con hide=true, a LangSmith solo le llega la
estructura + latencia/tokens (las env vars LANGSMITH_HIDE_INPUTS/OUTPUTS quedan en "true").
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from observabilidad.observability import setup_tracing

_KEYS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_HIDE_INPUTS",
    "LANGSMITH_HIDE_OUTPUTS",
)


def _clear() -> None:
    for k in _KEYS:
        os.environ.pop(k, None)


def _settings(**over) -> SimpleNamespace:
    base = dict(
        langsmith_tracing="true",
        langsmith_api_key="lsv2_test",
        langsmith_project="rh_agent",
        langsmith_hide_inputs=True,
        langsmith_hide_outputs=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_tracing_enabled_hides_pii_by_default():
    _clear()
    assert setup_tracing(_settings()) is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"
    _clear()


def test_hide_false_lets_content_through():
    _clear()
    setup_tracing(_settings(langsmith_hide_inputs=False, langsmith_hide_outputs=False))
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "false"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "false"
    _clear()


def test_tracing_disabled_without_key():
    _clear()
    assert setup_tracing(_settings(langsmith_api_key="")) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"
    # sin tracing no tiene sentido setear los flags de hide
    assert "LANGSMITH_HIDE_INPUTS" not in os.environ
    _clear()
