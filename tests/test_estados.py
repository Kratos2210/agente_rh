"""Catálogo central de estados del candidato (auditoría v4, R5).

Guard en el punto de escritura + invariante de paridad con el espejo frontend
(`stages.ts`): agregar un estado en un solo lado rompe CI, no producción.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.estados import CANDIDATE_STATUSES, STATUS_HIRED, ensure_valid_status
from db import repositories as repo


def test_catalog_accepts_all_known_statuses():
    for status in CANDIDATE_STATUSES:
        assert ensure_valid_status(status) == status


def test_typo_raises_with_pointer_to_catalog():
    with pytest.raises(ValueError, match="core/estados.py"):
        ensure_valid_status("hried")  # typo de hired


def test_update_candidate_guards_status_before_touching_db(monkeypatch):
    def _boom():
        raise AssertionError("no debe llegar a la DB con un status inválido")

    monkeypatch.setattr(repo, "get_supabase", _boom)
    with pytest.raises(ValueError):
        repo.update_candidate("c1", {"status": "contratado"})  # es "hired"


def test_update_candidate_without_status_skips_guard(monkeypatch):
    # Un payload sin status no debe pagar el guard (ni requerirlo).
    class _Q:
        def table(self, *_): return self
        def update(self, *_): return self
        def eq(self, *_): return self
        def execute(self):
            class R: data = [{"id": "c1"}]
            return R()

    monkeypatch.setattr(repo, "get_supabase", lambda: _Q())
    assert repo.update_candidate("c1", {"name": "X"})["id"] == "c1"
    assert repo.update_candidate("c1", {"status": STATUS_HIRED})["id"] == "c1"


def test_catalog_matches_frontend_stages_ts():
    """Paridad backend↔frontend: el kanban de stages.ts conoce EXACTAMENTE el catálogo.

    Un estado agregado en un solo lado (backend sin columna kanban, o kanban con un
    estado que el guard rechazaría) falla aquí, en CI."""
    stages_ts = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "stages.ts"
    text = stages_ts.read_text(encoding="utf-8")
    frontend: set[str] = set()
    for arr in re.findall(r"statuses:\s*\[([^\]]*)\]", text):
        frontend.update(re.findall(r'"([a-z_]+)"', arr))
    assert frontend == set(CANDIDATE_STATUSES), (
        f"solo backend: {sorted(set(CANDIDATE_STATUSES) - frontend)} · "
        f"solo frontend: {sorted(frontend - set(CANDIDATE_STATUSES))}"
    )
