"""Tests del helper puro de decisión de inactividad (`_inactivity_decision`).

Valida la máquina wait → remind → finalize según el silencio acumulado, los
recordatorios ya enviados y la configuración (minutos / máximo de recordatorios).
"""

from __future__ import annotations

from agent.graph import make_memory_runner
from agent.state import PHASE_CLOSED, PHASE_GREETING
from api.main import _inactivity_decision, _reminder_messages

_CFG = {"enabled": True, "reminder_minutes": 2, "max_reminders": 2}


class _NoLLM:
    def complete(self, prompt: str) -> str:  # el saludo/timeout no llaman al LLM
        return "{}"


def test_wait_before_threshold():
    assert _inactivity_decision(60, 0, _CFG) == "wait"  # 1 min < 2 min


def test_remind_after_threshold():
    assert _inactivity_decision(130, 0, _CFG) == "remind"  # >2 min, sin recordatorios
    assert _inactivity_decision(130, 1, _CFG) == "remind"  # ya envió 1 recordatorio


def test_finalize_after_max_reminders():
    assert _inactivity_decision(130, 2, _CFG) == "finalize"  # alcanzó el máximo


def test_threshold_uses_reminder_minutes():
    cfg = {"reminder_minutes": 5, "max_reminders": 1}
    assert _inactivity_decision(200, 0, cfg) == "wait"      # 200s < 5 min
    assert _inactivity_decision(301, 0, cfg) == "remind"    # pasó los 5 min
    assert _inactivity_decision(301, 1, cfg) == "finalize"  # tras 1 recordatorio


def test_defaults_when_cfg_missing_keys():
    # Sin claves usa los defaults (2 min / 2 recordatorios).
    assert _inactivity_decision(60, 0, {}) == "wait"
    assert _inactivity_decision(121, 0, {}) == "remind"
    assert _inactivity_decision(121, 2, {}) == "finalize"


# ── Saludo inicial (Acepto / No interesado): también recuerda y cierra ──────────────

def test_greeting_reminder_message():
    """El recordatorio del saludo invita a tocar Acepto (no incluye pregunta de entrevista)."""
    msgs = _reminder_messages({"state": PHASE_GREETING, "langgraph_thread_id": "x"}, None)
    assert len(msgs) == 1 and "Acepto" in msgs[0]


def test_greeting_timeout_closes_as_no_response():
    """Sin pulsar Acepto/No interesado: el timeout cierra la conversación como 'no respondió'."""
    runner = make_memory_runner(_NoLLM())
    tid = "test:greet-timeout"
    s0 = runner.start(tid, {"title": "Analista"}, [])
    assert s0["phase"] == PHASE_GREETING
    s1 = runner.send(tid, timeout=True)
    assert s1["phase"] == PHASE_CLOSED
    assert s1["closed_reason"] == "no_response"
    assert s1["outbound"]  # envía un cierre cordial
