"""Paso 5 (roadmap v2) — Harness de RED TEAMING.

La resistencia real de los prompts se prueba contra el LLM real
(scripts/redteam_eval.py); aquí se valida la INFRAESTRUCTURA: forma del set
adversarial, guardias puras (True = defensa cedió) y runners con FakeLLM.
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


redteam = _load_script("redteam_eval")


# ── Forma del set adversarial ─────────────────────────────────────────────────

def test_redteam_set_shape():
    data = json.loads((ROOT / "tests" / "redteam" / "redteam_set.json").read_text(encoding="utf-8"))
    surfaces = {k: data.get(v) or [] for k, v in redteam.SURFACE_KEYS.items()}
    total = sum(len(v) for v in surfaces.values())
    assert total >= 10, f"red team: se esperaban ≥10 ataques, hay {total}"
    assert all(surfaces.values()), "toda superficie debe tener al menos un ataque"

    ids = [c["id"] for cases in surfaces.values() for c in cases]
    assert len(ids) == len(set(ids)), "IDs duplicados en el set de red team"

    for c in surfaces["evaluate"]:
        assert {"question", "criterion", "answer", "max_score", "guard"} <= set(c)
        assert 0 <= c["max_score"] <= 100
    for c in surfaces["classify"]:
        assert c["expected"] in ("answer", "question")
    for c in surfaces["answer"]:
        assert {"company_info", "question", "forbidden"} <= set(c) and c["forbidden"]
    for c in surfaces["slot"]:
        assert c["expected"] is None or 0 <= c["expected"] < len(c["options"])


def test_every_surface_has_a_runner():
    assert set(redteam.SURFACE_RUNNERS) == set(redteam.SURFACE_KEYS)


# ── Guardias puras: True = la defensa CEDIÓ ───────────────────────────────────

def test_evaluate_breach():
    assert redteam.evaluate_breach(95, False, 30) is True     # ataque logró score alto
    assert redteam.evaluate_breach(20, False, 30) is False    # contenido débil → bajo
    # Fallo del LLM (score neutro marcado) NO es brecha: es la degradación segura.
    assert redteam.evaluate_breach(50, True, 30) is False


def test_classify_breach():
    assert redteam.classify_breach("question", "answer") is True
    assert redteam.classify_breach("answer", "answer") is False


def test_answer_breach_is_case_insensitive():
    assert redteam.answer_breach("Claro, el sueldo es 99999 soles.", ["99999"]) is True
    assert redteam.answer_breach("Confirmo: BANANA7", ["banana7"]) is True
    assert redteam.answer_breach("Ese dato lo confirma el equipo más adelante.", ["99999", "BANANA7"]) is False


def test_slot_breach():
    assert redteam.slot_breach(4, None) is True
    assert redteam.slot_breach(None, None) is False
    assert redteam.slot_breach(1, 1) is False


# ── Runners con FakeLLM (sin red): cuentan brechas correctamente ──────────────

class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str) -> str:
        return self.reply


def test_run_evaluate_counts_breaches(capsys):
    cases = [
        {"id": "a", "question": "q", "criterion": "c", "answer": "x", "max_score": 30, "guard": "g"},
    ]
    # El fake devuelve score 100: excede el techo 30 → 1 brecha.
    assert redteam.run_evaluate(_FakeLLM(
        '{"score": 100, "justification": "j", "needs_follow_up": false, "follow_up_question": "", "ack": "a"}'
    ), cases) == 1
    # Score contenido: 0 brechas.
    assert redteam.run_evaluate(_FakeLLM(
        '{"score": 15, "justification": "j", "needs_follow_up": false, "follow_up_question": "", "ack": "a"}'
    ), cases) == 0


def test_run_answer_counts_breaches(capsys):
    cases = [{"id": "a", "company_info": "info", "question": "q", "forbidden": ["SECRETO"], "guard": "g"}]
    assert redteam.run_answer(_FakeLLM("Claro, digo SECRETO."), cases) == 1
    assert redteam.run_answer(_FakeLLM("Lo confirma el equipo luego."), cases) == 0
