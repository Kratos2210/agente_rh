"""Tests del pre-filtro del CV (gate) con LLM fake y fallback determinista."""

from __future__ import annotations

import json

from evaluation.prescreen import prescreen_cv

_VACANCY = {
    "title": "Analista de Automatizaciones e IA",
    "requirements": "Bachiller en Sistemas; mínimo 2 años en automatización e IA; RPA, Python, cloud.",
}

_STRONG_CV = {
    "education": {"level": "bachiller", "career": "Ingeniería de Sistemas"},
    "years_experience": 4,
    "skills": ["UiPath", "Python", "Azure", "APIs REST"],
    "location": "Lima",
    "salary_expectation": "S/ 6500",
}

_WEAK_CV = {
    "education": {"level": "técnico", "career": "Diseño Gráfico"},
    "years_experience": 0,
    "skills": ["Photoshop", "Canva"],
    "location": "Arequipa",
}


class FakeLLM:
    def __init__(self, score):
        self.score = score

    def complete(self, prompt: str) -> str:
        return json.dumps(
            {
                "pre_score": self.score,
                "summary": "ok",
                "per_requirement": [{"requirement": "Experiencia", "met": self.score >= 60, "note": "—"}],
            }
        )


def test_prescreen_pass_with_high_score():
    res = prescreen_cv(FakeLLM(85), vacancy=_VACANCY, cv_profile=_STRONG_CV, pass_min=60)
    assert res.verdict == "pass"
    assert res.is_fit is True
    assert res.pre_score == 85


def test_prescreen_reject_with_low_score():
    res = prescreen_cv(FakeLLM(30), vacancy=_VACANCY, cv_profile=_WEAK_CV, pass_min=60)
    assert res.verdict == "reject"
    assert res.is_fit is False


def test_prescreen_borderline_band():
    res = prescreen_cv(FakeLLM(65), vacancy=_VACANCY, cv_profile=_STRONG_CV, pass_min=60)
    assert res.verdict == "borderline"
    assert res.is_fit is True


def test_prescreen_heuristic_fallback_without_llm():
    # Sin LLM → heurística: CV fuerte (4 años + carrera afín + skills) debe pasar.
    strong = prescreen_cv(None, vacancy=_VACANCY, cv_profile=_STRONG_CV, pass_min=60)
    assert strong.is_fit is True
    # CV débil (0 años, carrera no afín, skills no técnicas) debe ser rechazado.
    weak = prescreen_cv(None, vacancy=_VACANCY, cv_profile=_WEAK_CV, pass_min=60)
    assert weak.verdict == "reject"


def test_prescreen_falls_back_on_bad_json():
    class Broken:
        def complete(self, prompt: str) -> str:
            return "no soy json"

    res = prescreen_cv(Broken(), vacancy=_VACANCY, cv_profile=_STRONG_CV, pass_min=60)
    assert res.is_fit is True  # cae a la heurística, CV fuerte
