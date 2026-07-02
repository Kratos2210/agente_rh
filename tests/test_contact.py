"""Tests del contacto controlado e idempotencia del sync (repos en memoria).

Usan el prescreen heurístico (llm=None) y un fake de `db.repositories` para no tocar
Supabase: validan estados por fase y que nunca se re-contacta.
"""

from __future__ import annotations

import agent.sourcing_service as ss
from integrations.sourcing import SimulatedConnector

VACANCY = {
    "id": "v1",
    "title": "Analista de Automatizaciones e IA",
    "requirements": "Bachiller en Sistemas; mínimo 2 años; RPA, Python, cloud.",
    "intro_message": "Hola",
}


def _fake_repo(monkeypatch):
    cands: dict[str, dict] = {}
    seq = {"n": 0}

    def get_vacancy(vid):
        return VACANCY

    def get_vacancy_questions(vid):
        return [{"criterion": "experiencia en automatización"}]

    def get_or_create_candidate(vid, channel, cuid, name="", source="telegram", source_ref=""):
        for c in cands.values():
            if (c["vacancy_id"], c["channel"], c["channel_user_id"]) == (vid, channel, cuid):
                return c
        seq["n"] += 1
        cid = str(seq["n"])
        c = {
            "id": cid, "vacancy_id": vid, "channel": channel, "channel_user_id": cuid,
            "name": name, "source": source, "source_ref": source_ref,
            "status": "pending", "cv_profile": {}, "prescreen": {},
        }
        cands[cid] = c
        return c

    def find_candidate_by_source_ref(vid, source, source_ref):
        if not source_ref:
            return None
        for c in cands.values():
            if (c["vacancy_id"], c.get("source"), c.get("source_ref")) == (vid, source, source_ref):
                return c
        return None

    def update_candidate(cid, payload):
        cands[cid].update(payload)
        return cands[cid]

    def get_candidate(cid):
        return cands.get(cid)

    monkeypatch.setattr(ss.repositories, "get_vacancy", get_vacancy)
    monkeypatch.setattr(ss.repositories, "get_vacancy_questions", get_vacancy_questions)
    monkeypatch.setattr(ss.repositories, "get_or_create_candidate", get_or_create_candidate)
    monkeypatch.setattr(ss.repositories, "find_candidate_by_source_ref", find_candidate_by_source_ref)
    monkeypatch.setattr(ss.repositories, "update_candidate", update_candidate)
    monkeypatch.setattr(ss.repositories, "get_candidate", get_candidate)
    return cands


def test_sync_manual_leaves_passed_not_invited(monkeypatch):
    cands = _fake_repo(monkeypatch)
    report = ss.sync_applicants("v1", llm=None, connector=SimulatedConnector(), pass_min=60)
    assert report.imported == 3
    assert report.passed == 1
    assert report.rejected == 2
    assert report.contacted == 0
    statuses = [c["status"] for c in cands.values()]
    assert statuses.count("prescreen_passed") == 1
    assert statuses.count("prescreen_rejected") == 2
    assert "invited" not in statuses  # contacto manual: nadie es contactado en el sync


def test_auto_contact_contacts_passed(monkeypatch):
    _fake_repo(monkeypatch)
    calls: list[str] = []

    def contact_fn(c):
        calls.append(c["id"])
        return True

    report = ss.sync_applicants("v1", llm=None, connector=SimulatedConnector(), contact_fn=contact_fn)
    assert report.contacted == 1
    assert len(calls) == 1  # solo el apto


def test_resync_does_not_recontact_or_downgrade(monkeypatch):
    cands = _fake_repo(monkeypatch)
    ss.sync_applicants("v1", llm=None, connector=SimulatedConnector())
    passed = next(c for c in cands.values() if c["status"] == "prescreen_passed")
    passed["status"] = "interviewing"  # simula que ya avanzó

    calls: list[str] = []

    def contact_fn(c):
        calls.append(c["id"])
        return True

    ss.sync_applicants("v1", llm=None, connector=SimulatedConnector(), contact_fn=contact_fn)
    assert cands[passed["id"]]["status"] == "interviewing"  # no se retrocede
    assert passed["id"] not in calls  # no se re-contacta


def test_resync_after_demo_chat_claim_does_not_duplicate(monkeypatch):
    """Regresión: el contacto demo reasigna channel_user_id al chat real; el re-sync
    debe reencontrar al candidato por source_ref (no duplicarlo ni re-contactarlo)."""
    cands = _fake_repo(monkeypatch)
    ss.sync_applicants("v1", llm=None, connector=SimulatedConnector())
    passed = next(c for c in cands.values() if c["status"] == "prescreen_passed")
    # Simula _claim_chat + contacto: el chat demo reemplaza al id de plataforma.
    passed["channel_user_id"] = "5016550129"
    passed["status"] = "invited"

    calls: list[str] = []

    def contact_fn(c):
        calls.append(c["id"])
        return True

    report = ss.sync_applicants("v1", llm=None, connector=SimulatedConnector(), contact_fn=contact_fn)
    assert len(cands) == 3  # sin duplicados
    assert cands[passed["id"]]["status"] == "invited"  # no se retrocede
    assert calls == [] and report.contacted == 0  # no se re-contacta
    assert cands[passed["id"]]["channel_user_id"] == "5016550129"  # conserva su chat


# ── Regla de horario laboral para el contacto (9–18, L–V) ────────────────────────

def _dt(text: str):
    from datetime import datetime

    return datetime.fromisoformat(text)  # 2026-06-19 = viernes; 2026-06-20 = sábado


def test_within_working_hours_inside_window():
    from api.main import _within_working_hours

    wd, wins = [1, 2, 3, 4, 5], [("09:00", "18:00")]
    assert _within_working_hours(_dt("2026-06-19T09:00"), wd, wins) is True   # apertura
    assert _within_working_hours(_dt("2026-06-19T14:30"), wd, wins) is True   # media tarde


def test_within_working_hours_outside_window():
    from api.main import _within_working_hours

    wd, wins = [1, 2, 3, 4, 5], [("09:00", "18:00")]
    assert _within_working_hours(_dt("2026-06-19T08:59"), wd, wins) is False  # antes de abrir
    assert _within_working_hours(_dt("2026-06-19T18:00"), wd, wins) is False  # cierre (exclusivo)
    assert _within_working_hours(_dt("2026-06-19T21:00"), wd, wins) is False  # noche
    assert _within_working_hours(_dt("2026-06-20T11:00"), wd, wins) is False  # sábado


def test_within_working_hours_two_windows():
    """Dos franjas (10:30–12:00 y 15:00–17:00): dentro de cualquiera = True; el hueco = False."""
    from api.main import _within_working_hours

    wd, wins = [1, 2, 3, 4, 5], [("10:30", "12:00"), ("15:00", "17:00")]
    assert _within_working_hours(_dt("2026-06-19T10:30"), wd, wins) is True   # abre franja 1
    assert _within_working_hours(_dt("2026-06-19T11:59"), wd, wins) is True   # fin franja 1
    assert _within_working_hours(_dt("2026-06-19T15:30"), wd, wins) is True   # dentro franja 2
    assert _within_working_hours(_dt("2026-06-19T09:30"), wd, wins) is False  # antes de la 1
    assert _within_working_hours(_dt("2026-06-19T13:30"), wd, wins) is False  # hueco mediodía
    assert _within_working_hours(_dt("2026-06-19T12:00"), wd, wins) is False  # cierre franja 1 (exclusivo)
    assert _within_working_hours(_dt("2026-06-19T17:00"), wd, wins) is False  # cierre franja 2 (exclusivo)


def test_work_windows_fallback_single():
    """Sin `work_windows`, cae a la ventana única work_start/work_end (compat hacia atrás)."""
    from api.main import _work_windows

    assert _work_windows({"work_start": "08:00", "work_end": "16:00"}) == [("08:00", "16:00")]
    assert _work_windows({"work_windows": [["10:30", "12:00"], ["15:00", "17:00"]]}) == [
        ("10:30", "12:00"),
        ("15:00", "17:00"),
    ]
