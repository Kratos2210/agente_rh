"""Endpoints de vacantes (CRUD, candidatos por vacante, sync de postulantes, métricas)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user, require_role
from api.ratelimit import SlidingWindowLimiter
from api.deps import (
    _audit,
    _candidate_row_from_embed,
    _page_params,
    _require_vacancy_in_tenant,
    _with_cost,
)
from api.runtime import current_settings
from api.scheduler import _contact_candidate
from db import repositories as repo

router = APIRouter()

# R4 (auditoría): el sync importa postulantes y corre el gate de CV con LLM por cada
# uno — sin freno, un doble click (o un tenant abusivo) multiplica el costo. 2/min por
# tenant alcanza para el uso real (botón manual) y corta el abuso. Por proceso, como R1.
_sync_limiter = SlidingWindowLimiter(max_calls=2, per_seconds=60)


class QuestionIn(BaseModel):
    position: int = Field(ge=0)
    text: str
    criterion: str = ""
    weight: float = Field(default=1.0, ge=0)
    max_follow_ups: int = Field(default=1, ge=0)


class VacancyIn(BaseModel):
    title: str
    description: str = ""
    requirements: str = ""
    intro_message: str = ""
    details_message: str = ""
    company_info: str = ""
    semaphore_thresholds: dict[str, Any] = {"green_min": 75, "yellow_min": 50}
    recruiter_id: str | None = None             # RR.HH. asignado (Fase 1 + coordinación)
    lead_recruiter_id: str | None = None        # líder del proyecto (Fase 2)
    manager_recruiter_id: str | None = None     # gerencia (Fase 3)
    meeting_duration_minutes: int = Field(default=45, gt=0)  # duración de la entrevista
    # Datos del aviso de empleo (rediseño "hira"; columnas en 0012_vacancy_fields.sql).
    area: str = ""
    modality: str = "presencial"                # presencial | hibrido | remoto
    location: str = ""
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    benefits: list[str] = []
    portals: list[str] = []                     # ej. ["bumeran", "linkedin"]
    auto_agent: bool = True
    questions: list[QuestionIn] = []


@router.get("/api/vacancies")
def list_vacancies(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    # D1: 3 consultas fijas (reclutadores + vacantes + conteo por estado), sin 1/vacante.
    tenant_id = user["tenant_id"]
    recruiters = {r["id"]: r for r in repo.list_recruiters(tenant_id=tenant_id)}
    vacancies = repo.list_vacancies(tenant_id=tenant_id)
    counts = repo.count_candidates_by_status([v["id"] for v in vacancies])
    out = []
    for v in vacancies:
        per = counts.get(v["id"], {})
        out.append({
            **v,
            "candidate_count": sum(per.values()),
            "stage_counts": per,
            "recruiter": recruiters.get(v.get("recruiter_id")),
        })
    return out


@router.post("/api/vacancies", status_code=201)
def create_vacancy(
    payload: VacancyIn, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    data = payload.model_dump()
    questions = data.pop("questions", [])
    data["tenant_id"] = user["tenant_id"]
    vacancy = repo.create_vacancy(data)
    if questions:
        repo.replace_vacancy_questions(vacancy["id"], questions)
    _audit(user, "vacancy.create", entity_type="vacancy", entity_id=vacancy["id"], summary=vacancy.get("title", ""))
    return {**vacancy, "questions": repo.get_vacancy_questions(vacancy["id"])}


@router.get("/api/vacancies/{vacancy_id}")
def get_vacancy(
    vacancy_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    vacancy = _require_vacancy_in_tenant(vacancy_id, user)
    recruiter = repo.get_recruiter(vacancy["recruiter_id"]) if vacancy.get("recruiter_id") else None
    lead = repo.get_recruiter(vacancy["lead_recruiter_id"]) if vacancy.get("lead_recruiter_id") else None
    manager = repo.get_recruiter(vacancy["manager_recruiter_id"]) if vacancy.get("manager_recruiter_id") else None
    # Deep-link del bot para publicar en avisos: enruta el /start a ESTA vacante (A1).
    bot_username = (current_settings().telegram_bot_username or "").strip().lstrip("@")
    return {
        **vacancy,
        "questions": repo.get_vacancy_questions(vacancy_id),
        "recruiter": recruiter,
        "lead_recruiter": lead,
        "manager_recruiter": manager,
        "telegram_deep_link": (
            f"https://t.me/{bot_username}?start={vacancy_id}" if bot_username else ""
        ),
    }


@router.put("/api/vacancies/{vacancy_id}")
def update_vacancy(
    vacancy_id: str,
    payload: VacancyIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    _require_vacancy_in_tenant(vacancy_id, user)
    data = payload.model_dump()
    questions = data.pop("questions", [])
    vacancy = repo.update_vacancy(vacancy_id, data)
    repo.replace_vacancy_questions(vacancy_id, questions)
    _audit(user, "vacancy.update", entity_type="vacancy", entity_id=vacancy_id, summary=vacancy.get("title", ""))
    return {**vacancy, "questions": repo.get_vacancy_questions(vacancy_id)}


@router.get("/api/vacancies/{vacancy_id}/candidates")
def list_candidates(
    vacancy_id: str,
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Candidatos de la vacante, paginados (U1) y en 1 consulta con embeds (D1)."""
    _require_vacancy_in_tenant(vacancy_id, user)
    limit, offset = _page_params(limit, offset)
    rows, total = repo.list_candidate_rows(
        [vacancy_id], search=q.strip(), limit=limit, offset=offset
    )
    items = [_candidate_row_from_embed(c) for c in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/api/vacancies/{vacancy_id}/sync-applicants")
def sync_applicants_endpoint(
    vacancy_id: str, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    """Importa postulantes de la plataforma (simulada), los pre-filtra por CV y
    contacta a los aptos. Devuelve el reporte {imported, passed, rejected, contacted}."""
    _require_vacancy_in_tenant(vacancy_id, user)
    if not _sync_limiter.allow(f"sync:{user['tenant_id']}"):
        raise HTTPException(
            429, "Sincronización muy frecuente. Espera un minuto e inténtalo de nuevo."
        )
    from orquestacion.llm import MeteredLLM, build_default_llm, build_stage_overrides
    from agente.sourcing_service import sync_applicants
    from integrations.sourcing import get_connector

    settings = current_settings()
    llm = MeteredLLM(
        build_default_llm(),
        trace=settings.llm_trace_enabled,
        trace_max_chars=settings.llm_trace_max_chars,
        overrides=build_stage_overrides(settings),  # routing de costos (paso 5)
    )
    connector = get_connector(settings)
    vacancy = repo.get_vacancy(vacancy_id)

    # Contacto automático solo si está habilitado en config (default: contacto manual por botón).
    contact_fn = None
    if settings.auto_contact_on_pass:
        def contact_fn(candidate: dict) -> bool:  # noqa: E306
            return _contact_candidate(candidate, vacancy, settings).get("contacted", False)

    report = sync_applicants(
        vacancy_id,
        llm=llm,
        connector=connector,
        pass_min=settings.prescreen_pass_min,
        contact_fn=contact_fn,
    )
    return report.as_dict()


@router.get("/api/vacancies/{vacancy_id}/metrics")
def vacancy_metrics_endpoint(
    vacancy_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _require_vacancy_in_tenant(vacancy_id, user)
    return _with_cost(repo.vacancy_metrics(vacancy_id), user["tenant_id"])
