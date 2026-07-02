"""Endpoints del roster de RR.HH. (reclutadores/entrevistadores)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user, require_role
from api.deps import _audit
from db import repositories as repo

router = APIRouter()


class RecruiterIn(BaseModel):
    name: str
    email: str = ""
    company: str = ""
    role: str = "Reclutador"
    phone: str = ""
    telegram_chat_id: str = ""
    calendar_id: str = "primary"
    location: str = ""                          # dirección de oficina (entrevistas presenciales)
    active: bool = True


# Estados "activos" de un candidato (en proceso, no descartado/terminal off-path).
_ACTIVE_STATUSES = {
    "sourced", "prescreen_passed", "invited", "consented",
    "interviewing", "finished", "scheduling", "scheduled", "advanced",
    "lead_scheduling", "lead_scheduled", "mgr_scheduling", "mgr_scheduled",
}


@router.get("/api/recruiters")
def list_recruiters(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Roster con carga de trabajo: vacantes abiertas y candidatos activos por reclutador."""
    tenant_id = user["tenant_id"]
    open_vac = repo.list_vacancies(status="open", tenant_id=tenant_id)
    # D1: un solo conteo por estado para TODAS las vacantes abiertas (sin 1/vacante).
    counts = repo.count_candidates_by_status([v["id"] for v in open_vac])
    open_count: dict[str, int] = {}
    active_count: dict[str, int] = {}
    for v in open_vac:
        rid = v.get("recruiter_id")
        if not rid:
            continue
        open_count[rid] = open_count.get(rid, 0) + 1
        per = counts.get(v["id"], {})
        active = sum(n for status, n in per.items() if status in _ACTIVE_STATUSES)
        active_count[rid] = active_count.get(rid, 0) + active
    return [
        {**r, "open_vacancies": open_count.get(r["id"], 0), "active_candidates": active_count.get(r["id"], 0)}
        for r in repo.list_recruiters(tenant_id=tenant_id)
    ]


@router.post("/api/recruiters", status_code=201)
def create_recruiter(
    payload: RecruiterIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    recruiter = repo.create_recruiter({**payload.model_dump(), "tenant_id": user["tenant_id"]})
    _audit(user, "recruiter.create", entity_type="recruiter", entity_id=recruiter["id"], summary=recruiter.get("name", ""))
    return recruiter


@router.put("/api/recruiters/{recruiter_id}")
def update_recruiter(
    recruiter_id: str,
    payload: RecruiterIn,
    user: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    existing = repo.get_recruiter(recruiter_id)
    if not existing or existing.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Reclutador no encontrado")
    recruiter = repo.update_recruiter(recruiter_id, payload.model_dump())
    _audit(user, "recruiter.update", entity_type="recruiter", entity_id=recruiter_id, summary=recruiter.get("name", ""))
    return recruiter
