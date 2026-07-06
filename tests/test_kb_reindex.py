"""Linaje de la KB RAG (auditoría v4, R4): reindex de company_kb al editar la vacante.

Sin torch/Chroma: se prueba la composición del documento, el encolado en el outbox
(sin intento en línea), el gating por flag RAG y el registro del kind en los handlers.
"""

from __future__ import annotations

import api.auth as auth
import api.main as main
import api.routes.vacancies as vacancies_route
from core.config import Settings, get_settings
from fastapi.testclient import TestClient
from notifications.outbox import _HANDLERS
from retrieval import company_kb

client = TestClient(main.app)


def _auth(role: str = "recruiter", tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


_VACANCY_BODY = {"title": "Analista", "company_info": "Empresa X", "questions": []}


def _patch_crud(monkeypatch):
    vac = {"id": "v1", "tenant_id": "t1", "title": "Analista"}
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: vac)
    monkeypatch.setattr(main.repo, "create_vacancy", lambda data: vac)
    monkeypatch.setattr(main.repo, "update_vacancy", lambda vid, data: vac)
    monkeypatch.setattr(main.repo, "replace_vacancy_questions", lambda vid, qs: None)
    monkeypatch.setattr(main.repo, "get_vacancy_questions", lambda vid: [])
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)


# ── Composición del documento ───────────────────────────────────────────────────────

def test_compose_vacancy_text_includes_salary_and_topics():
    text = company_kb.compose_vacancy_text(
        {"title": "Analista", "company_info": "Somos X", "salary_min": 4000, "salary_max": 6000,
         "benefits": ["EPS", "Bono"]},
        [{"label": "Formación"}, {"question": "¿Experiencia con n8n?"}],
    )
    assert "# Vacante: Analista" in text
    assert "4000 a 6000" in text
    assert "Formación" in text and "EPS" in text


def test_vacancy_source_is_by_id():
    # Por id: purgar/citar sobrevive a un cambio de título.
    assert company_kb.vacancy_source({"id": "v9", "title": "Otro"}) == "vacante:v9"


# ── Encolado (sin intento en línea) ─────────────────────────────────────────────────

def test_enqueue_reindex_enqueues_due_row(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(company_kb, "get_settings", lambda: get_settings())
    import db.repositories as repo

    monkeypatch.setattr(repo, "enqueue_outbox", lambda row: rows.append(row) or row)
    company_kb.enqueue_reindex("v1", "t1")
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "kb_reindex" and row["payload"] == {"vacancy_id": "v1"}
    assert row["status"] == "pending" and row["tenant_id"] == "t1"
    # Vencido ya (NULL no matchea el lte de list_due_outbox): el próximo drain lo toma.
    assert row["next_attempt_at"]


def test_enqueue_reindex_is_best_effort(monkeypatch):
    import db.repositories as repo

    def _boom(row):
        raise RuntimeError("db caída")

    monkeypatch.setattr(repo, "enqueue_outbox", _boom)
    company_kb.enqueue_reindex("v1", "t1")  # no debe lanzar (el CRUD no se rompe)


def test_kb_reindex_kind_registered():
    assert "kb_reindex" in _HANDLERS


# ── Hook en el CRUD de vacantes (gated por el flag RAG) ─────────────────────────────

def test_update_vacancy_schedules_reindex(monkeypatch):
    _patch_crud(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(vacancies_route, "enqueue_reindex", lambda vid, tid: calls.append((vid, tid)))
    monkeypatch.setattr(vacancies_route, "current_settings",
                        lambda: Settings(interview_rag_enabled=True))
    r = client.put("/api/vacancies/v1", json=_VACANCY_BODY, headers=_auth())
    assert r.status_code == 200
    assert calls == [("v1", "t1")]


def test_create_vacancy_schedules_reindex(monkeypatch):
    _patch_crud(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(vacancies_route, "enqueue_reindex", lambda vid, tid: calls.append((vid, tid)))
    monkeypatch.setattr(vacancies_route, "current_settings",
                        lambda: Settings(interview_rag_enabled=True))
    r = client.post("/api/vacancies", json=_VACANCY_BODY, headers=_auth())
    assert r.status_code == 201
    assert calls == [("v1", "t1")]


def test_no_reindex_with_rag_disabled(monkeypatch):
    _patch_crud(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(vacancies_route, "enqueue_reindex", lambda vid, tid: calls.append((vid, tid)))
    monkeypatch.setattr(vacancies_route, "current_settings",
                        lambda: Settings(interview_rag_enabled=False))
    r = client.put("/api/vacancies/v1", json=_VACANCY_BODY, headers=_auth())
    assert r.status_code == 200
    assert calls == []
