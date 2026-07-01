"""Tests del helper puro de decisión de inactividad (`_inactivity_decision`).

Valida la máquina wait → remind → finalize según el silencio acumulado, los
recordatorios ya enviados y la configuración (minutos / máximo de recordatorios).
"""

from __future__ import annotations

from api.main import _inactivity_decision

_CFG = {"enabled": True, "reminder_minutes": 2, "max_reminders": 2}


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
