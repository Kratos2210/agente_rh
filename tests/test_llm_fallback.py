"""Fallback de proveedor LLM + circuit breaker (auditoría v4, R3).

Cubre: CircuitBreaker (abre a los N fallos, half-open tras cooldown, cierra con éxito,
reabre si la sonda falla), FallbackLLM (failover transparente, atribución del modelo que
sirvió vía MeteredLLM, circuito abierto no toca el principal, ambos fallan → propaga) y
el gating por config (wrap_with_fallback no-op sin LLM_FALLBACK_MODEL; _build_pair envuelve).
"""

from __future__ import annotations

import pytest

from core.config import Settings
from orquestacion.fallback import CircuitBreaker, FallbackLLM, wrap_with_fallback
from orquestacion.llm import MeteredLLM


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _OkLLM:
    def __init__(self, name: str):
        self.model = name
        self.last_usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
        self.metadata: dict[str, str] = {}
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return f"{self.model}:ok"


class _FailingLLM(_OkLLM):
    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        raise RuntimeError("proveedor caído")


def _breaker(clock: _FakeClock, failures: int = 3, cooldown: float = 60.0) -> CircuitBreaker:
    return CircuitBreaker(failures=failures, cooldown_seconds=cooldown, now=clock)


# ── CircuitBreaker ────────────────────────────────────────────────────────────

def test_breaker_opens_after_consecutive_failures():
    clock = _FakeClock()
    b = _breaker(clock)
    for _ in range(2):
        b.record_failure()
    assert b.allow_primary()  # 2 < 3: sigue cerrado
    b.record_failure()
    assert b.state == "open"
    assert not b.allow_primary()


def test_breaker_half_open_after_cooldown_and_closes_on_success():
    clock = _FakeClock()
    b = _breaker(clock)
    for _ in range(3):
        b.record_failure()
    assert not b.allow_primary()
    clock.t = 61.0
    assert b.allow_primary()  # half-open: deja pasar la sonda
    b.record_success()
    assert b.state == "closed" and b.allow_primary()


def test_breaker_reopens_if_probe_fails():
    clock = _FakeClock()
    b = _breaker(clock)
    for _ in range(3):
        b.record_failure()
    clock.t = 61.0
    assert b.allow_primary()      # sonda
    b.record_failure()            # la sonda falló → reabre YA (sin exigir N de nuevo)
    assert b.state == "open"
    clock.t = 100.0               # cooldown reiniciado en t=61
    assert not b.allow_primary()
    clock.t = 122.0
    assert b.allow_primary()


def test_breaker_success_resets_consecutive_count():
    clock = _FakeClock()
    b = _breaker(clock)
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.state == "closed"  # nunca juntó 3 consecutivos


# ── FallbackLLM ───────────────────────────────────────────────────────────────

def test_primary_ok_serves_primary():
    clock = _FakeClock()
    primary, fallback = _OkLLM("principal"), _OkLLM("respaldo")
    llm = FallbackLLM(primary, fallback, _breaker(clock))
    assert llm.complete("hola") == "principal:ok"
    assert not fallback.calls
    assert llm.model == "principal"


def test_failover_serves_fallback_and_metered_attributes_real_model():
    clock = _FakeClock()
    primary, fallback = _FailingLLM("principal"), _OkLLM("respaldo")
    m = MeteredLLM(FallbackLLM(primary, fallback, _breaker(clock)), trace=True)

    out = m.for_stage("evaluate").complete("evaluá esto")

    assert out == "respaldo:ok"
    assert primary.calls and fallback.calls  # intentó el principal, sirvió el respaldo
    assert m.drain_models()["evaluate"] == "respaldo"      # atribución del que SIRVIÓ
    trace = m.drain_traces()[-1]
    assert trace["model"] == "respaldo" and trace["error"] is None
    acc = m.drain()["evaluate"]
    assert acc["total_tokens"] == 10 and acc["errors"] == 0  # el turno no vio error


def test_open_circuit_skips_primary():
    clock = _FakeClock()
    primary, fallback = _FailingLLM("principal"), _OkLLM("respaldo")
    llm = FallbackLLM(primary, fallback, _breaker(clock))
    for _ in range(3):
        llm.complete("x")  # 3 failovers → circuito abierto
    primary.calls.clear()
    assert llm.complete("y") == "respaldo:ok"
    assert not primary.calls  # no pagó el timeout del principal


def test_probe_recovers_primary_after_cooldown():
    clock = _FakeClock()
    primary, fallback = _FailingLLM("principal"), _OkLLM("respaldo")
    llm = FallbackLLM(primary, fallback, _breaker(clock))
    for _ in range(3):
        llm.complete("x")
    # El principal "vuelve": la sonda tras el cooldown lo readopta.
    primary.complete = lambda prompt: "principal:ok"  # type: ignore[method-assign]
    clock.t = 61.0
    assert llm.complete("y") == "principal:ok"
    assert llm.model == "principal"


def test_both_fail_raises_for_low_confidence_path():
    clock = _FakeClock()
    llm = FallbackLLM(_FailingLLM("principal"), _FailingLLM("respaldo"), _breaker(clock))
    with pytest.raises(RuntimeError):
        llm.complete("x")


def test_metadata_propagates_to_serving_llm():
    clock = _FakeClock()
    primary, fallback = _FailingLLM("principal"), _OkLLM("respaldo")
    m = MeteredLLM(FallbackLLM(primary, fallback, _breaker(clock)))
    m.set_context(tenant="acme", conversation="c1")
    m.for_stage("answer").complete("duda")
    assert fallback.metadata.get("tenant") == "acme"
    assert fallback.metadata.get("stage") == "answer"


# ── Gating por config ─────────────────────────────────────────────────────────

def test_wrap_is_noop_without_config():
    s = Settings(llm_fallback_model="")
    inner = _OkLLM("principal")
    assert wrap_with_fallback(inner, s) is inner


def test_wrap_builds_fallback_from_settings(monkeypatch):
    s = Settings(
        llm_fallback_model="modelo-respaldo",
        llm_fallback_base_url="https://otro-proveedor/v1",
        llm_fallback_api_key="k",
        llm_breaker_failures=2,
    )
    captured: dict = {}

    def fake_build(model=None, *, base_url=None, api_key=None, **_):
        captured.update(model=model, base_url=base_url, api_key=api_key)
        return _OkLLM(model or "?")

    monkeypatch.setattr("orquestacion.llm.build_default_llm", fake_build)
    wrapped = wrap_with_fallback(_OkLLM("principal"), s)
    assert isinstance(wrapped, FallbackLLM)
    assert captured == {
        "model": "modelo-respaldo",
        "base_url": "https://otro-proveedor/v1",
        "api_key": "k",
    }


def test_build_pair_wraps_env_and_byok_paths(monkeypatch):
    from orquestacion import providers

    s = Settings(llm_fallback_model="modelo-respaldo")
    monkeypatch.setattr(
        "orquestacion.llm.build_default_llm",
        lambda model=None, **kw: _OkLLM(model or "env-model"),
    )
    inner_env, _ = providers._build_pair(None, s)
    assert isinstance(inner_env, FallbackLLM)

    cfg = {"provider": "groq", "base_url": "https://api.groq.com/openai/v1",
           "api_key": "k", "model": "m", "cheap_model": "", "cheap_stages": ""}
    inner_byok, _ = providers._build_pair(cfg, s)
    assert isinstance(inner_byok, FallbackLLM)
