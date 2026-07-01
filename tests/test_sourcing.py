"""Tests del conector de sourcing simulado (fixture de postulantes)."""

from __future__ import annotations

from integrations.sourcing import SimulatedConnector, get_connector


def test_fetch_applicants_loads_fixture_for_vacancy():
    conn = SimulatedConnector()
    applicants = conn.fetch_applicants({"title": "Analista de Automatizaciones e IA"})
    assert len(applicants) >= 3
    a = applicants[0]
    assert a.name
    assert a.cv_profile.get("years_experience") is not None
    assert isinstance(a.cv_profile.get("skills"), list)


def test_fetch_applicants_unknown_vacancy_is_empty():
    conn = SimulatedConnector()
    assert conn.fetch_applicants({"title": "Vacante inexistente"}) == []


def test_platform_user_id_falls_back_to_external_id():
    # Sin chat real, el conector usa el id de plataforma (no fabrica chats reales).
    conn = SimulatedConnector()
    applicants = conn.fetch_applicants({"title": "Analista de Automatizaciones e IA"})
    a = applicants[0]
    assert a.platform_user_id == a.external_id
    assert a.channel_user_id == ""


def test_get_connector_defaults_to_simulated():
    class S:
        sourcing_provider = "simulated"
        demo_telegram_chat_id = ""

    conn = get_connector(S())
    assert conn.name == "bumeran"
