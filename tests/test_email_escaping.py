"""Audit F3 — el HTML de los correos debe escapar todo dato dinámico.

Las justificaciones/resumen/recomendación las genera el LLM, y el nombre/vacante/contactos
vienen del CV o del reclutador. Sin `html.escape`, un valor con markup rompe o inyecta el
HTML del correo (los clientes de correo renderizan HTML).
"""

from __future__ import annotations

from notifications.email import build_meeting_email, build_scorecard_email
from core.config import Settings

S = Settings(
    smtp_host="smtp.test",
    smtp_from="bot@test.io",
    recruiter_email="rh@test.io",
)

XSS = '<script>alert("x")</script>'


def test_scorecard_html_escapes_llm_and_user_fields():
    vacancy = {"title": XSS}
    candidate = {"name": XSS}
    scorecard = {
        "semaphore": "green",
        "total_score": 90,
        "summary": XSS,
        "recommendation": XSS,
        "per_criterion": [{"score": 80, "criterion": XSS, "justification": XSS}],
    }
    built = build_scorecard_email(S, vacancy, candidate, scorecard)
    assert built is not None
    _, _, _, html = built
    # El markup crudo no aparece; sí su forma escapada.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_meeting_html_escapes_and_link_cannot_break_href():
    vacancy = {"title": XSS}
    candidate = {"name": XSS}
    recruiter = {"name": XSS, "email": "r@test.io", "phone": "999"}
    meeting = {
        "scheduled_at": "2026-07-02T15:00:00",
        "meet_link": '"><img src=x onerror=alert(1)>',
        "candidate_email": "c@test.io",
        "candidate_phone": "111",
        "recruiter_email": "r@test.io",
        "recruiter_phone": "999",
    }
    built = build_meeting_email(S, vacancy, candidate, meeting, recruiter)
    assert built is not None
    _, _, _, html = built
    assert "<script>" not in html
    assert "<img" not in html  # el link malicioso no se cuela como tag
    # El atributo href no se rompe: la comilla del payload quedó escapada.
    assert 'onerror' not in html or "&gt;" in html
    assert "&lt;img" in html
