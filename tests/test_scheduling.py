"""Tests del agendamiento de entrevista (fase 2): cómputo de horarios, parseo de la
elección del candidato, backend simulado y la fase de coordinación del motor.

No tocan Supabase: ejercitan los helpers puros, el SimulatedScheduler y el grafo
(con checkpointer en memoria), igual que test_interview.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from agent.graph import make_memory_runner
from agent.state import PHASE_SCHEDULED, PHASE_SCHEDULING
from evaluation.scorer import parse_slot_choice
from integrations.scheduling import (
    SimulatedScheduler,
    _tz,
    compute_free_slots,
    human_slot,
)

_CFG = {
    "slot_minutes": 60,
    "work_days": [1, 2, 3, 4, 5],
    "work_start": "09:00",
    "work_end": "11:00",
    "timezone": "America/Lima",
    "horizon_days": 7,
    "options": 3,
}


# ── LLM fake (parsea la elección del horario) ────────────────────────────────────

class FakeLLM:
    def complete(self, prompt: str) -> str:
        if '"choice"' in prompt:
            m = re.search(r'El candidato respondió:\s*"(.*?)"', prompt, re.S)
            msg = m.group(1) if m else ""
            digits = [c for c in msg if c.isdigit()]
            return json.dumps({"choice": int(digits[0]) if digits else 0})
        return "{}"


# ── compute_free_slots (puro) ─────────────────────────────────────────────────────

def _monday_8am():
    tz = _tz("America/Lima")
    return datetime(2026, 6, 22, 8, 0, tzinfo=tz)  # 2026-06-22 es lunes


def test_free_slots_fills_working_hours_then_next_day():
    slots = compute_free_slots([], _CFG, now=_monday_8am())
    assert len(slots) == 3
    # Ventana 09:00-11:00 → dos slots de 60 min el lunes, luego el martes 09:00.
    assert (slots[0].day, slots[0].hour) == (22, 9)
    assert (slots[1].day, slots[1].hour) == (22, 10)
    assert (slots[2].day, slots[2].hour) == (23, 9)


def test_free_slots_skips_busy_intervals():
    tz = _tz("America/Lima")
    busy = [(datetime(2026, 6, 22, 9, 0, tzinfo=tz), datetime(2026, 6, 22, 10, 0, tzinfo=tz))]
    slots = compute_free_slots(busy, _CFG, now=_monday_8am())
    assert (slots[0].day, slots[0].hour) == (22, 10)  # el de las 09:00 está ocupado


def test_free_slots_skips_weekend():
    tz = _tz("America/Lima")
    saturday = datetime(2026, 6, 27, 8, 0, tzinfo=tz)  # sábado
    slots = compute_free_slots([], _CFG, now=saturday, count=1)
    assert slots[0].isoweekday() == 1  # salta al lunes


# ── parse_slot_choice ─────────────────────────────────────────────────────────────

def test_parse_slot_choice_picks_index():
    options = ["lunes 22/06 a las 09:00", "lunes 22/06 a las 10:00", "martes 23/06 a las 09:00"]
    assert parse_slot_choice(FakeLLM(), options, "el 2 me queda bien") == 1
    assert parse_slot_choice(FakeLLM(), options, "ninguno por ahora") is None


# ── SimulatedScheduler ─────────────────────────────────────────────────────────────

def test_simulated_scheduler_creates_meeting(tmp_path):
    backend = SimulatedScheduler(sheet_path=tmp_path / "meetings.csv")
    start = _monday_8am() + timedelta(hours=2)
    end = start + timedelta(minutes=45)
    res = backend.create_meeting(
        calendar_id="primary", summary="Entrevista", start=start, end=end,
        attendees=["a@x.com"], description="",
    )
    assert res.meet_link.startswith("https://meet.google.com/sim-")
    assert res.event_id
    ref = backend.append_sheet_row("sheet", "Reuniones", ["fila", "demo"])
    assert ref.startswith("local:")
    assert (tmp_path / "meetings.csv").exists()


# ── Fase de coordinación en el motor ───────────────────────────────────────────────

def test_engine_scheduling_proposal_then_choice():
    runner = make_memory_runner(FakeLLM())
    tid = "test:sched"
    slots = [s.isoformat() for s in compute_free_slots([], _CFG, now=_monday_8am())]
    recruiter = {"name": "Grace Mendieta", "company": "SIFRAH"}

    s0 = runner.send(tid, start_scheduling=slots, recruiter=recruiter)
    assert s0["phase"] == PHASE_SCHEDULING
    joined = " ".join(s0["outbound"])
    assert "Grace Mendieta" in joined and "SIFRAH" in joined
    assert "1." in joined and "2." in joined  # opciones numeradas

    s1 = runner.send(tid, text="me quedo con la 2")
    assert s1["phase"] == PHASE_SCHEDULED
    assert s1["meeting_slot"] == slots[1]
    assert "agendando" in " ".join(s1["outbound"]).lower()


def test_engine_scheduling_reprompts_on_unclear_choice():
    runner = make_memory_runner(FakeLLM())
    tid = "test:sched2"
    slots = [s.isoformat() for s in compute_free_slots([], _CFG, now=_monday_8am())]
    runner.send(tid, start_scheduling=slots, recruiter={"name": "Grace", "company": "SIFRAH"})

    s = runner.send(tid, text="no sé todavía")
    assert s["phase"] == PHASE_SCHEDULING        # sigue coordinando
    assert s.get("meeting_slot") is None
    assert "número" in " ".join(s["outbound"]).lower()
