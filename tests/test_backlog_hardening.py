"""Backlog de la auditoría e2e — lote seguridad + operación (S2, S3, O2, A3).

  - S2: revocación de sesión — `get_current_user` consulta `users.active` (caché TTL);
        desactivar un usuario corta su token vigente; sin DB degrada en abierto.
  - S3: las credenciales del examen psicológico se enmascaran para el rol viewer.
  - O2: `_collect_ops_alerts` produce alertas estructuradas (filtradas por tenant) y
        el endpoint /api/ops/alerts las expone solo a admin.
  - A3: `InterviewService` serializa el acceso al motor por thread_id (el barrido de
        inactividad y un mensaje del candidato no deben pisar el mismo checkpoint).
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import api.main as main
import db.repositories as db_repo
from api import auth
from core.config import get_settings

client = TestClient(main.app)


def _token(role: str, user_id: str = "u1", tenant_id: str = "t1") -> str:
    return auth.create_access_token(
        user_id=user_id, email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )


def _auth_headers(role: str, user_id: str = "u1", tenant_id: str = "t1") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role, user_id, tenant_id)}"}


@pytest.fixture(autouse=True)
def _clean_revocation_cache():
    """El caché de revocación es proceso-global: no debe contaminar otros tests."""
    auth._revocation_cache.clear()
    yield
    auth._revocation_cache.clear()


# ── S2: revocación de sesión ───────────────────────────────────────────────────


def test_deactivated_user_token_is_rejected(monkeypatch):
    monkeypatch.setattr(db_repo, "get_user", lambda uid: {"id": uid, "active": False})
    r = client.get("/api/auth/me", headers=_auth_headers("admin", user_id="revocado"))
    assert r.status_code == 401


def test_active_user_token_still_valid(monkeypatch):
    monkeypatch.setattr(db_repo, "get_user", lambda uid: {"id": uid, "active": True})
    r = client.get("/api/auth/me", headers=_auth_headers("admin", user_id="activo"))
    assert r.status_code == 200


def test_revocation_fails_open_without_db(monkeypatch):
    # La caída de la DB no debe dejar a todo el dashboard afuera: el token sigue valiendo.
    def _boom(uid):
        raise RuntimeError("DB caída")

    monkeypatch.setattr(db_repo, "get_user", _boom)
    r = client.get("/api/auth/me", headers=_auth_headers("admin", user_id="sin-db"))
    assert r.status_code == 200


def test_revocation_check_is_cached(monkeypatch):
    calls = {"n": 0}

    def _get_user(uid):
        calls["n"] += 1
        return {"id": uid, "active": True}

    monkeypatch.setattr(db_repo, "get_user", _get_user)
    for _ in range(3):
        assert auth._is_user_revoked("cacheado") is False
    assert calls["n"] == 1  # una lectura por TTL, no por request


# ── S3: credenciales del examen psicológico por rol ────────────────────────────

_EXAM = {"link": "https://exam.example", "code": "AB12", "key": "clave-secreta",
         "sent_at": "2026-07-01T12:00:00Z", "sent_by": "grace@sifrah.pe"}


def test_psych_exam_masked_for_viewer():
    masked = main._psych_exam_for_role(dict(_EXAM), "viewer")
    assert masked["link"] == masked["code"] == masked["key"] == "•••"
    # El viewer sí ve que el examen fue enviado (cuándo y por quién).
    assert masked["sent_at"] == _EXAM["sent_at"] and masked["sent_by"] == _EXAM["sent_by"]


def test_psych_exam_full_for_recruiter_and_admin():
    assert main._psych_exam_for_role(dict(_EXAM), "recruiter") == _EXAM
    assert main._psych_exam_for_role(dict(_EXAM), "admin") == _EXAM
    assert main._psych_exam_for_role(None, "viewer") is None


def test_candidate_detail_masks_psych_exam_for_viewer(monkeypatch):
    cand = {"id": "c1", "vacancy_id": "v1", "name": "Daniela", "status": "finished",
            "channel": "telegram", "channel_user_id": "999", "created_at": "2026-07-01",
            "psych_exam": dict(_EXAM)}
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: dict(cand))
    monkeypatch.setattr(main.repo, "get_vacancy",
                        lambda vid: {"id": vid, "tenant_id": "t1", "title": "V", "semaphore_thresholds": None})
    monkeypatch.setattr(main.repo, "get_conversation_by_candidate", lambda cid: None)
    monkeypatch.setattr(main.repo, "list_meetings_by_candidate", lambda cid: [])
    monkeypatch.setattr(main.repo, "list_stage_feedback", lambda cid: [])

    body = client.get("/api/candidates/c1", headers=_auth_headers("viewer")).json()
    assert body["psych_exam"]["key"] == "•••"
    assert body["candidate"]["psych_exam"]["key"] == "•••"  # también dentro del candidato

    body = client.get("/api/candidates/c1", headers=_auth_headers("recruiter")).json()
    assert body["psych_exam"]["key"] == _EXAM["key"]


# ── O2: alertas operativas estructuradas ───────────────────────────────────────


def _patch_alert_repos(monkeypatch, *, meetings=None, failed=0):
    monkeypatch.setattr(main.repo, "count_outbox_by_status", lambda tid=None: {"failed": failed})
    monkeypatch.setattr(main.repo, "list_meetings_without_link", lambda: list(meetings or []))
    monkeypatch.setattr(main.repo, "list_conversations_by_states", lambda states: [])
    monkeypatch.setattr(main.repo, "get_meeting_by_conversation", lambda cid: None)
    monkeypatch.setattr(main.repo, "list_vacancies",
                        lambda **kw: [{"id": "vA", "tenant_id": "t1"}, {"id": "vB", "tenant_id": "t2"}])


def test_ops_alerts_filtered_by_tenant(monkeypatch):
    meetings = [
        {"id": "m1", "candidate_id": "c1", "vacancy_id": "vA", "conversation_id": "x1"},
        {"id": "m2", "candidate_id": "c2", "vacancy_id": "vB", "conversation_id": "x2"},
    ]
    _patch_alert_repos(monkeypatch, meetings=meetings, failed=2)
    alerts = main._collect_ops_alerts("t1")
    types = [a["type"] for a in alerts]
    assert types.count("meeting_no_link") == 1          # solo la reunión del tenant t1
    assert next(a for a in alerts if a["type"] == "meeting_no_link")["meeting_id"] == "m1"
    assert next(a for a in alerts if a["type"] == "dead_letter")["count"] == 2


def test_ops_alerts_endpoint_admin_only(monkeypatch):
    _patch_alert_repos(monkeypatch)
    assert client.get("/api/ops/alerts", headers=_auth_headers("recruiter")).status_code == 403
    r = client.get("/api/ops/alerts", headers=_auth_headers("admin"))
    assert r.status_code == 200
    assert r.json() == {"alerts": []}


def test_reconciliation_sweep_report_from_alerts(monkeypatch):
    _patch_alert_repos(monkeypatch, meetings=[
        {"id": "m1", "candidate_id": "c1", "vacancy_id": "vA", "conversation_id": "x1"},
    ], failed=3)
    report = main._reconciliation_sweep(settings=None)
    assert report["dead_letter"] == 3
    assert report["meetings_no_link"] == 1
    assert report["alerts"] == 2  # 1 agregada de dead-letter + 1 por reunión


# ── A4: purga del set de dedupe del auto-contacto ──────────────────────────────


def test_prune_fired_slots_drops_past_dates():
    from datetime import date

    today = date(2026, 7, 2)
    fired = {
        "t1|2026-06-20|11:00",   # pasado → se purga
        "t1|2026-07-01|11:00",   # ayer → se conserva (zonas horarias)
        "t1|2026-07-02|11:00",   # hoy → se conserva
        "t2|2026-07-03|09:00",   # mañana local de otro tenant → se conserva
    }
    pruned = main._prune_fired_slots(fired, today)
    assert "t1|2026-06-20|11:00" not in pruned
    assert len(pruned) == 3


# ── S5: CORS parametrizado por settings ────────────────────────────────────────


def test_cors_origins_parsed_from_settings():
    from core.config import Settings

    s = Settings(cors_origins=" https://app.midominio.pe , http://localhost:3000 ")
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    assert origins == ["https://app.midominio.pe", "http://localhost:3000"]


# ── A3: serialización por thread en InterviewService ───────────────────────────


def test_thread_lock_identity():
    from agente.service import InterviewService

    s = InterviewService(runner=None)
    assert s._thread_lock("telegram:1") is s._thread_lock("telegram:1")
    assert s._thread_lock("telegram:1") is not s._thread_lock("telegram:2")


def test_same_thread_engine_access_is_serialized(monkeypatch):
    from agente import service as svc

    s = svc.InterviewService(runner=None)
    active = {"n": 0, "max": 0}
    guard = threading.Lock()

    def slow_get_conversation(thread_id):
        with guard:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.03)
        with guard:
            active["n"] -= 1
        return None  # corta el turno antes de tocar el motor

    monkeypatch.setattr(svc.repositories, "get_conversation_by_thread", slow_get_conversation)
    threads = [
        threading.Thread(target=lambda: s.finalize_inactive("telegram:mismo")) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1  # nunca dos entradas simultáneas al mismo thread
