"""Endpoint de la vista Onboarding: el "cierre" del proceso (examen médico → contratado →
kit de onboarding). Listado propio porque el pipeline global solo trae vacantes ABIERTAS y una
vacante se cierra al llenarse — su contratado seguiría en onboarding y desaparecería de allí."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from api.deps import _candidate_row_from_embed, _page_params
from api.routes.candidates import _medical_exam_for_role
from db import repositories as repo

router = APIRouter()

# Horizonte de "próximos ingresos" en los KPIs (días).
_UPCOMING_DAYS = 7


def _kit_configured(vacancy: dict[str, Any]) -> bool:
    """Una vacante tiene kit si su `onboarding_kit` trae bienvenida o materiales (mismo criterio
    que el barrido del scheduler: sin eso no se envía nada)."""
    kit = (vacancy or {}).get("onboarding_kit") or {}
    return bool(kit.get("welcome") or kit.get("materials"))


def _closing_summary(candidates: list[dict[str, Any]], today: date) -> dict[str, int]:
    """KPIs del cierre, calculados sobre TODOS los candidatos del tenant (no la página).

    `candidates` son filas crudas (con `status`, `start_date`, `onboarding`). Puro/testeable.
    `kit_pendiente` cuenta todo contratado con fecha y sin envío — mismo criterio que la
    columna "Kit pendiente" del tablero (si la vacante no tiene kit, el banner de
    `vacancies_without_kit` ya lo señala; excluirlos aquí hacía que KPI y columna no cuadraran)."""
    upcoming = pending_kit = no_date = in_medical = 0
    for c in candidates:
        status = c.get("status")
        if status in ("medical_pending", "medical_scheduled"):
            in_medical += 1
            continue
        if status != "hired":
            continue
        sent = bool((c.get("onboarding") or {}).get("sent_at"))
        raw = str(c.get("start_date") or "").strip()
        if not raw:
            no_date += 1
            continue
        if not sent:
            pending_kit += 1
        try:
            start = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        if not sent and today <= start <= today + timedelta(days=_UPCOMING_DAYS):
            upcoming += 1
    return {
        "proximos_ingresos": upcoming,
        "kit_pendiente": pending_kit,
        "sin_fecha": no_date,
        "en_medico": in_medical,
    }


@router.get("/api/onboarding")
def list_closing(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Candidatos en el cierre del proceso del tenant (médico/contratado), paginado, con KPIs.

    Aislado por tenant: deriva TODAS las vacantes del tenant (incluidas cerradas) y filtra los
    candidatos a esas vacantes. `summary` se calcula sobre todo el conjunto; la página solo pagina
    los items. `vacancies_without_kit` señala vacantes con contratados pero sin kit (riesgo)."""
    from datetime import datetime, timezone

    vacancies = repo.list_vacancies(tenant_id=user["tenant_id"])
    titles = {v["id"]: v.get("title", "") for v in vacancies}
    with_kit = {v["id"] for v in vacancies if _kit_configured(v)}
    role = user.get("role", "")

    limit, offset = _page_params(limit, offset)
    rows, total = repo.list_closing_candidates(list(titles), search=q.strip(), limit=limit, offset=offset)
    items = []
    for c in rows:
        row = _candidate_row_from_embed(c)
        row["vacancy_title"] = titles.get(c.get("vacancy_id"), "")
        row["start_date"] = c.get("start_date")
        row["onboarding"] = c.get("onboarding")
        row["medical_exam"] = _medical_exam_for_role(c.get("medical_exam"), role)
        row["kit_configured"] = c.get("vacancy_id") in with_kit
        items.append(row)

    # Summary + vacantes-sin-kit sobre TODO el cierre del tenant (una consulta liviana de status).
    all_closing, _ = repo.list_closing_candidates(list(titles), limit=None)
    today = datetime.now(timezone.utc).date()
    summary = _closing_summary(all_closing, today)
    hired_vacancies = {c.get("vacancy_id") for c in all_closing if c.get("status") == "hired"}
    vacancies_without_kit = [
        {"id": vid, "title": titles.get(vid, "")}
        for vid in hired_vacancies
        if vid not in with_kit
    ]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": summary,
        "vacancies_without_kit": vacancies_without_kit,
    }
