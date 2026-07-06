"""Endpoint de costos LLM (solo admin): trazabilidad por vacante, día y candidato.

Agrega en Python las filas de `llm_usage` del período (PostgREST no hace GROUP BY y el
volumen es de cientos-miles de filas/mes por tenant — trivial en memoria, mismo patrón
que `_tenant_month_costs` del scheduler). El "día" se bucketiza en la zona horaria del
tenant (`scheduling.timezone`, fallback UTC-5) para que la trazabilidad coincida con la
operación del negocio, no con UTC.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_role
from api.deps import compute_cost
from api.runtime import _DEFAULT_LLM_PRICING, _DEFAULT_SCHEDULING, _now_local, _parse_dt
from db import repositories as repo
from db.repositories import TURN_STAGE

router = APIRouter()

MAX_DAYS = 365


def _add_model(models: dict[str, dict[str, int]], model: str, inp: int, out: int) -> None:
    """Acumula input/output por modelo en el bucket dado (formato de `compute_cost`)."""
    m = models.setdefault(model, {"input": 0, "output": 0})
    m["input"] += inp
    m["output"] += out


def _cost_report(
    rows: list[dict[str, Any]],
    vacancies: list[dict[str, Any]],
    names: dict[str, str],
    pricing: dict[str, Any],
    tz,
    since_day,
    days: int,
) -> dict[str, Any]:
    """PURA: agrega las filas de usage por día / vacante / candidato y les aplica costo.

    - Excluye `stage == TURN_STAGE` (filas solo-latencia, 0 tokens) y filas de vacantes
      fuera del set dado (= aislamiento por tenant, decidido por el caller).
    - `daily` cubre TODOS los días del período (relleno a cero → eje continuo).
    - Vacantes y candidatos ordenados por costo desc; candidato None = "proceso general"
      (etapas sin candidato, p. ej. batch de sourcing).
    """
    vac_by_id = {v["id"]: v for v in vacancies}
    day_keys = [(since_day + timedelta(days=i)).isoformat() for i in range(days)]
    daily: dict[str, dict[str, Any]] = {d: {"tokens": 0, "models": {}} for d in day_keys}
    totals = {"total": 0, "input": 0, "output": 0}
    totals_models: dict[str, dict[str, int]] = {}
    calls = 0
    vac_acc: dict[str, dict[str, Any]] = {}

    for r in rows:
        if r.get("stage") == TURN_STAGE:
            continue
        vid = r.get("vacancy_id")
        if vid not in vac_by_id:
            continue
        inp = int(r.get("input_tokens") or 0)
        out = int(r.get("output_tokens") or 0)
        tot = int(r.get("total_tokens") or 0) or (inp + out)
        model = str(r.get("model") or "")
        created = str(r.get("created_at") or "")
        day = _parse_dt(created).astimezone(tz).date().isoformat()

        calls += int(r.get("calls") or 0)
        totals["total"] += tot
        totals["input"] += inp
        totals["output"] += out
        _add_model(totals_models, model, inp, out)

        if day in daily:  # guard: una fila fuera del eje (borde de tz) no revienta
            daily[day]["tokens"] += tot
            _add_model(daily[day]["models"], model, inp, out)

        va = vac_acc.setdefault(
            vid, {"tokens": {"total": 0, "input": 0, "output": 0}, "models": {}, "cands": {}}
        )
        va["tokens"]["total"] += tot
        va["tokens"]["input"] += inp
        va["tokens"]["output"] += out
        _add_model(va["models"], model, inp, out)

        ca = va["cands"].setdefault(
            r.get("candidate_id"), {"tokens": 0, "models": {}, "last_at": ""}
        )
        ca["tokens"] += tot
        _add_model(ca["models"], model, inp, out)
        if created > ca["last_at"]:
            ca["last_at"] = created

    total_cost = compute_cost(totals_models, pricing)
    out_vacancies = []
    for vid, va in vac_acc.items():
        vac = vac_by_id[vid]
        vcost = compute_cost(va["models"], pricing)
        candidates = []
        for cid, ca in va["cands"].items():
            candidates.append(
                {
                    "candidate_id": cid,
                    "name": names.get(cid, "") if cid else "",
                    "tokens": ca["tokens"],
                    "cost": compute_cost(ca["models"], pricing)["total"],
                    "last_at": ca["last_at"],
                }
            )
        candidates.sort(key=lambda c: c["cost"], reverse=True)
        out_vacancies.append(
            {
                "vacancy_id": vid,
                "title": vac.get("title", ""),
                "status": vac.get("status", ""),
                "tokens": va["tokens"],
                "cost": vcost["total"],
                "cost_by_model": vcost["by_model"],
                "candidates": candidates,
            }
        )
    out_vacancies.sort(key=lambda v: v["cost"], reverse=True)

    return {
        "totals": {
            "tokens": totals,
            "calls": calls,
            "cost": total_cost["total"],
            "cost_by_model": total_cost["by_model"],
        },
        "daily": [
            {
                "day": d,
                "tokens": daily[d]["tokens"],
                "cost": compute_cost(daily[d]["models"], pricing)["total"],
            }
            for d in day_keys
        ],
        "vacancies": out_vacancies,
    }


@router.get("/api/costs")
def get_costs(
    days: int = 30, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Reporte de consumo/costo LLM del tenant: totales del período, serie diaria y
    desglose por vacante con drill-down por candidato. `days` acotado a 1..365."""
    days = max(1, min(MAX_DAYS, int(days)))
    tenant_id = user["tenant_id"]

    scheduling = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, tenant_id) or {}
    tzname = scheduling.get("timezone") or "America/Lima"
    now_local = _now_local(tzname)
    tz = now_local.tzinfo
    since_day = (now_local - timedelta(days=days - 1)).date()
    since_start = datetime.combine(since_day, time.min).replace(tzinfo=tz)
    since_iso = since_start.astimezone(timezone.utc).isoformat()

    vacancies = repo.list_vacancies(tenant_id=tenant_id)
    vac_ids = {v["id"] for v in vacancies}
    rows = repo.usage_rows_detailed_since(since_iso)
    pricing = repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, tenant_id)
    names = repo.candidate_names(
        [r.get("candidate_id") for r in rows if r.get("candidate_id") and r.get("vacancy_id") in vac_ids]
    )

    report = _cost_report(rows, vacancies, names, pricing, tz, since_day, days)
    report.update({"days": days, "since": since_day.isoformat(), "timezone": tzname})
    return report
