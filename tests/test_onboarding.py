"""Onboarding (auditoría v3, Parte C): kit de materiales en la fecha de ingreso.

Barrido del scheduler con fakes (día exacto, catch-up, dedupe por sent_at, sin kit,
horario laboral), endpoints manuales (start-date + envío ahora), builders del correo
y regresión del fix de anonimización (los exámenes no sobreviven a la retención).
"""

from __future__ import annotations

import datetime as dt

import api.auth as auth
import api.main as main
import api.scheduler as sched
import db.repositories as repositories
from fastapi.testclient import TestClient
from notifications.email import build_onboarding_email, render_kit_materials_text
from core.config import get_settings

client = TestClient(main.app)

_KIT = {"welcome": "¡Bienvenido al equipo!",
        "materials": [{"title": "Guía de bienvenida", "url": "http://kb.x/guia", "note": "léela antes"},
                      {"title": "Organigrama"}]}


def _auth(role: str = "recruiter", tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


# ── Barrido del scheduler ──────────────────────────────────────────────────────────

def _hired(start_date, onboarding=None):
    return {"id": "cand1", "vacancy_id": "v1", "name": "Luis", "channel": "telegram",
            "channel_user_id": "123456", "status": "hired", "start_date": start_date,
            "onboarding": onboarding, "cv_profile": {"email": "luis@x.com"}}


def _setup_sweep(monkeypatch, cands, *, kit=_KIT, working=True, today="2026-07-13"):
    vac = {"id": "v1", "tenant_id": "t1", "title": "Analista", "onboarding_kit": kit}
    monkeypatch.setattr(sched.repo, "list_candidates_by_statuses", lambda st: cands)
    monkeypatch.setattr(sched.repo, "list_vacancies", lambda **k: [vac])
    monkeypatch.setattr(sched.repo, "get_vacancy", lambda vid: vac)
    monkeypatch.setattr(sched.repo, "get_app_setting", lambda key, default, tid=None: default)
    updated: dict = {}
    monkeypatch.setattr(sched.repo, "update_candidate", lambda cid, p: updated.update({cid: p}) or p)
    monkeypatch.setattr(sched, "_is_working_now", lambda s, t=None: working)
    fixed = dt.datetime.fromisoformat(f"{today}T10:00:00")
    monkeypatch.setattr(sched, "_now_local", lambda tz: fixed)
    sent = {"mail": 0, "tg": 0}
    monkeypatch.setattr(sched.outbox, "deliver_onboarding",
                        lambda *a, **k: sent.__setitem__("mail", sent["mail"] + 1) or True)
    monkeypatch.setattr(sched.outbox, "deliver_candidate_text",
                        lambda *a, **k: sent.__setitem__("tg", sent["tg"] + 1) or True)
    sched._state.pop("onboarding_sent", None)
    return updated, sent


def test_sweep_sends_on_start_date(monkeypatch):
    updated, sent = _setup_sweep(monkeypatch, [_hired("2026-07-13")])
    report = sched._onboarding_sweep(get_settings())
    assert report["sent"] == 1
    assert sent == {"mail": 1, "tg": 1}
    assert updated["cand1"]["onboarding"]["sent_by"] == "scheduler"
    # Segundo pase del MISMO proceso: dedupe por el set en memoria.
    report2 = sched._onboarding_sweep(get_settings())
    assert report2["sent"] == 0 and sent == {"mail": 1, "tg": 1}


def test_sweep_does_not_send_before_start_date(monkeypatch):
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-20")])
    assert sched._onboarding_sweep(get_settings())["sent"] == 0
    assert sent == {"mail": 0, "tg": 0}


def test_sweep_catchup_within_window_but_not_beyond(monkeypatch):
    # Ayer (backend caído el día D) → catch-up envía.
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-12")])
    assert sched._onboarding_sweep(get_settings())["sent"] == 1
    # Hace 10 días (fuera de la ventana) → ya no.
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-03")])
    assert sched._onboarding_sweep(get_settings())["sent"] == 0


def test_sweep_skips_already_sent_no_kit_and_off_hours(monkeypatch):
    # Ya enviado (sello en DB): restart-safe, no reenvía.
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-13", onboarding={"sent_at": "x"})])
    assert sched._onboarding_sweep(get_settings())["sent"] == 0
    # Vacante sin kit configurado: no-op sin error.
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-13")], kit={})
    assert sched._onboarding_sweep(get_settings())["sent"] == 0
    # Fuera de horario laboral: espera (lo recogerá un tick dentro de horario).
    _, sent = _setup_sweep(monkeypatch, [_hired("2026-07-13")], working=False)
    assert sched._onboarding_sweep(get_settings())["sent"] == 0


# ── Endpoints: fecha de ingreso + envío manual ─────────────────────────────────────

def _patch_endpoint(monkeypatch, *, status="hired", onboarding=None, kit=_KIT):
    cand = {"id": "cand1", "vacancy_id": "v1", "name": "Luis", "channel": "telegram",
            "channel_user_id": "123456", "status": status, "onboarding": onboarding,
            "cv_profile": {"email": "luis@x.com"}}
    vac = {"id": "v1", "title": "Analista", "tenant_id": "t1", "onboarding_kit": kit}
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: cand)
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: vac)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)
    updated: dict = {}
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: updated.update(p) or p)
    monkeypatch.setattr(main.outbox, "deliver_onboarding", lambda *a, **k: True)
    monkeypatch.setattr(main.outbox, "deliver_candidate_text", lambda *a, **k: True)
    return updated


def test_start_date_saves_for_hired_and_409_otherwise(monkeypatch):
    updated = _patch_endpoint(monkeypatch)
    r = client.post("/api/candidates/cand1/start-date",
                    json={"start_date": "2026-07-20"}, headers=_auth())
    assert r.status_code == 200 and updated["start_date"] == "2026-07-20"
    # Fecha inválida → 422 (validator).
    assert client.post("/api/candidates/cand1/start-date",
                       json={"start_date": "20/07/2026"}, headers=_auth()).status_code == 422
    # Solo contratados.
    _patch_endpoint(monkeypatch, status="interviewing")
    assert client.post("/api/candidates/cand1/start-date",
                       json={"start_date": "2026-07-20"}, headers=_auth()).status_code == 409


def test_manual_onboarding_sends_and_is_idempotent(monkeypatch):
    updated = _patch_endpoint(monkeypatch)
    r = client.post("/api/candidates/cand1/onboarding", headers=_auth())
    assert r.status_code == 200
    assert r.json()["email_sent"] is True and r.json()["telegram_sent"] is True
    assert updated["onboarding"]["sent_by"] == "a@b.com"
    # Ya enviado → 409.
    _patch_endpoint(monkeypatch, onboarding={"sent_at": "2026-07-13T09:00:00"})
    assert client.post("/api/candidates/cand1/onboarding", headers=_auth()).status_code == 409
    # Sin kit configurado → 409 con mensaje humano.
    _patch_endpoint(monkeypatch, kit={})
    assert client.post("/api/candidates/cand1/onboarding", headers=_auth()).status_code == 409


# ── Builders del correo / texto ────────────────────────────────────────────────────

class _MailSettings:
    smtp_host = "smtp.test"; smtp_from = "rrhh@x.com"; smtp_port = 587
    smtp_user = ""; smtp_password = ""; recruiter_email = "rec@x.com"


def test_onboarding_email_lists_materials():
    cand = {"name": "Luis", "cv_profile": {"email": "luis@x.com"}}
    built = build_onboarding_email(_MailSettings(), {"title": "Analista"}, cand, _KIT)
    assert built is not None
    recipients, subject, text, html = built
    assert recipients == ["luis@x.com"] and "onboarding" in subject.lower()
    assert "Guía de bienvenida" in text and "http://kb.x/guia" in text
    assert "Organigrama" in html and "http://kb.x/guia" in html


def test_onboarding_email_none_without_candidate_email():
    assert build_onboarding_email(_MailSettings(), {"title": "A"},
                                  {"name": "X", "cv_profile": {}}, _KIT) is None


def test_render_kit_materials_text_handles_empty_kit():
    lines = render_kit_materials_text(_KIT)
    assert "• Guía de bienvenida — http://kb.x/guia (léela antes)" in lines
    assert "• Organigrama" in lines
    # Kit sin materiales: línea de fallback (RR.HH. los entrega en persona).
    assert "RR.HH." in render_kit_materials_text({})


# ── Regresión: la anonimización purga también los exámenes (gap S4/Ley 29733) ─────

def test_anonymize_clears_exams_and_onboarding(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(repositories, "update_candidate", lambda cid, p: captured.update(p) or p)
    repositories.anonymize_candidate("abcd1234-x")
    assert captured["psych_exam"] is None
    assert captured["medical_exam"] is None
    assert captured["onboarding"] is None and captured["start_date"] is None
    assert captured["name"] == "" and captured["cv_profile"] == {}


# ── Vista Onboarding: endpoint del "cierre" + KPIs ────────────────────────────────

def _raw_closing(cid, status, *, start_date=None, onboarding=None, vacancy_id="v1", medical_exam=None):
    """Fila cruda como la devuelve repo.list_closing_candidates (cols + embed)."""
    return {"id": cid, "name": f"Cand {cid}", "status": status, "channel": "telegram",
            "source": "bumeran", "created_at": "2026-07-01T00:00:00+00:00", "vacancy_id": vacancy_id,
            "start_date": start_date, "onboarding": onboarding, "medical_exam": medical_exam,
            "prescreen": {}, "conversations": []}


def _patch_closing(monkeypatch, rows, *, capture=None):
    vacs = [{"id": "v1", "tenant_id": "t1", "title": "Analista", "onboarding_kit": _KIT},
            {"id": "v2", "tenant_id": "t1", "title": "Vendedor", "onboarding_kit": {}}]

    def _list_vacancies(status=None, *, tenant_id=None):
        if capture is not None:
            capture["tenant_id"] = tenant_id
        return vacs

    monkeypatch.setattr(main.repo, "list_vacancies", _list_vacancies)
    monkeypatch.setattr(main.repo, "list_closing_candidates",
                        lambda vids, **k: (capture.__setitem__("vids", vids) if capture is not None else None) or (rows, len(rows)))


def test_onboarding_list_returns_closing_with_summary(monkeypatch):
    cap: dict = {}
    rows = [
        _raw_closing("c1", "hired", start_date="2026-07-15"),                       # kit pendiente
        _raw_closing("c2", "hired"),                                                # sin fecha
        _raw_closing("c3", "hired", start_date="2026-07-13", onboarding={"sent_at": "2026-07-13T09:00:00"}),  # enviado
        _raw_closing("c4", "medical_pending"),                                      # en médico
        _raw_closing("c5", "hired", start_date="2026-08-01", vacancy_id="v2"),      # v2 sin kit
    ]
    _patch_closing(monkeypatch, rows, capture=cap)
    r = client.get("/api/onboarding", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 5
    assert cap["tenant_id"] == "t1"  # aislamiento: deriva las vacantes del tenant del token
    # kit_configured por vacante (v1 con kit, v2 sin).
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id["c1"]["kit_configured"] is True and by_id["c5"]["kit_configured"] is False
    assert by_id["c1"]["start_date"] == "2026-07-15"
    # Summary (los deterministas, independientes de "hoy").
    assert body["summary"]["sin_fecha"] == 1
    assert body["summary"]["en_medico"] == 1
    # Mismo criterio que la columna del tablero: c1 y c5 (aunque v2 no tenga kit — el
    # banner de vacancies_without_kit señala ese caso).
    assert body["summary"]["kit_pendiente"] == 2
    assert isinstance(body["summary"]["proximos_ingresos"], int)
    # Vacantes con contratados pero sin kit: v2 sí, v1 no.
    assert body["vacancies_without_kit"] == [{"id": "v2", "title": "Vendedor"}]


def test_onboarding_requires_auth():
    assert client.get("/api/onboarding").status_code == 401


def test_onboarding_masks_medical_result_for_viewer(monkeypatch):
    rows = [_raw_closing("c9", "medical_scheduled",
                          medical_exam={"clinic": "Clínica X", "scheduled_at": "2026-07-20 10:00",
                                        "result": "apto", "result_notes": "ok"})]
    _patch_closing(monkeypatch, rows)
    # viewer: el resultado (dato de salud) llega enmascarado.
    med_v = client.get("/api/onboarding", headers=_auth(role="viewer")).json()["items"][0]["medical_exam"]
    assert med_v["result"] == "•••" and med_v["result_notes"] == "•••"
    assert med_v["scheduled_at"] == "2026-07-20 10:00"  # la cita sí es visible
    # recruiter: lo ve completo.
    med_r = client.get("/api/onboarding", headers=_auth(role="recruiter")).json()["items"][0]["medical_exam"]
    assert med_r["result"] == "apto"


def test_closing_summary_helper_counts_subphases():
    from api.routes.onboarding import _closing_summary

    today = dt.date(2026, 7, 13)
    cands = [
        {"status": "hired", "start_date": "2026-07-15", "onboarding": None, "vacancy_id": "v1"},  # upcoming+pending
        {"status": "hired", "start_date": "", "onboarding": None, "vacancy_id": "v1"},            # sin fecha
        {"status": "hired", "start_date": "2026-07-13", "onboarding": {"sent_at": "x"}, "vacancy_id": "v1"},  # enviado
        {"status": "medical_pending", "vacancy_id": "v1"},                                        # en médico
        {"status": "hired", "start_date": "2026-08-30", "onboarding": None, "vacancy_id": "v2"},  # lejano (cuenta como kit pendiente, no como próximo)
    ]
    s = _closing_summary(cands, today)
    assert s == {"proximos_ingresos": 1, "kit_pendiente": 2, "sin_fecha": 1, "en_medico": 1}


# ── Alerta de examen médico estancado (reconciliación) ─────────────────────────────

def test_ops_alerts_include_medical_stuck(monkeypatch):
    monkeypatch.setattr(sched.repo, "count_outbox_by_status", lambda tid=None: {})
    monkeypatch.setattr(sched.repo, "list_meetings_without_link", lambda: [])
    monkeypatch.setattr(sched.repo, "list_conversations_by_states", lambda st: [])
    monkeypatch.setattr(sched.repo, "list_delivery_failed_conversations", lambda: [])
    monkeypatch.setattr(
        sched.repo, "list_candidates_by_statuses",
        lambda st: [{"id": "c9", "vacancy_id": "v1", "status": "medical_pending",
                     "updated_at": "2026-06-01T00:00:00+00:00"}],
    )
    monkeypatch.setitem(sched._state, "service", None)
    alerts = sched._collect_ops_alerts()
    stuck = [a for a in alerts if a["type"] == "medical_stuck"]
    assert len(stuck) == 1 and "sin cita" in stuck[0]["detail"]
