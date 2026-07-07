"""Series de tiempo de observabilidad (solo admin): tendencia de operación LLM, calidad y HTTP.

Los datos ya se persisten (`llm_usage`, `quality_metrics`, `http_metrics_snapshots`); este
endpoint los agrega POR DÍA para graficar la tendencia, no solo la foto actual. Las funciones
de agregación son puras (testeables sin DB); el "día" se bucketiza en la zona horaria del
tenant (como la página Costos), para que el eje coincida con la operación del negocio.

Ámbitos: `llm` y `quality` son POR TENANT; `http` es de proceso (diagnóstico de infra: los
snapshots no llevan tenant). Los contadores HTTP son ACUMULADOS desde el arranque del proceso
→ el volumen por día se deriva como delta reset-aware entre snapshots consecutivos.
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

MAX_DAYS = 90


def _day_keys(since_day, days: int) -> list[str]:
    """Eje continuo de días [since_day, since_day+days) como ISO YYYY-MM-DD."""
    return [(since_day + timedelta(days=i)).isoformat() for i in range(days)]


def llm_daily_series(
    rows: list[dict[str, Any]],
    vac_ids: set[str],
    tz,
    day_keys: list[str],
    pricing: dict[str, Any],
) -> list[dict[str, Any]]:
    """PURA: agrega `llm_usage` por día → throughput (calls/errors), tokens, costo y latencia.

    Aísla por tenant (solo filas cuya vacante está en `vac_ids`) y excluye la etapa sintética
    `turn` (solo-latencia, 0 tokens): estas métricas son de LLAMADAS al LLM. `avg_ms` por día =
    duration_ms / calls; el costo aplica el pricing del tenant sobre los tokens por modelo."""
    keyset = set(day_keys)
    acc: dict[str, dict[str, Any]] = {
        d: {"calls": 0, "errors": 0, "tokens": 0, "duration_ms": 0, "models": {}} for d in day_keys
    }
    for r in rows:
        if r.get("stage") == TURN_STAGE:
            continue
        if r.get("vacancy_id") not in vac_ids:
            continue
        day = _parse_dt(r.get("created_at")).astimezone(tz).date().isoformat()
        if day not in keyset:
            continue
        b = acc[day]
        b["calls"] += int(r.get("calls") or 0)
        b["errors"] += int(r.get("errors") or 0)
        b["tokens"] += int(r.get("total_tokens") or 0)
        b["duration_ms"] += int(r.get("duration_ms") or 0)
        m = b["models"].setdefault(str(r.get("model") or ""), {"input": 0, "output": 0})
        m["input"] += int(r.get("input_tokens") or 0)
        m["output"] += int(r.get("output_tokens") or 0)
    out = []
    for d in day_keys:
        b = acc[d]
        calls = b["calls"]
        out.append({
            "day": d,
            "calls": calls,
            "errors": b["errors"],
            "tokens": b["tokens"],
            "cost": compute_cost(b["models"], pricing)["total"],
            "avg_ms": round(b["duration_ms"] / calls) if calls else 0,
        })
    return out


def quality_series(rows: list[dict[str, Any]], day_keys: list[str]) -> dict[str, list[dict[str, Any]]]:
    """PURA: agrupa `quality_metrics` por métrica → puntos {day, rate, sample_size, threshold}.

    NO rellena días faltantes: la calidad es esporádica (una fila el día que corre el barrido)
    y un 0 % sería una alucinación de dato. Solo puntos reales dentro de la ventana, ascendente."""
    keyset = set(day_keys)
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        day = str(r.get("day") or "")
        if day not in keyset:
            continue
        by_metric.setdefault(str(r.get("metric") or "?"), []).append({
            "day": day,
            "rate": float(r.get("rate") or 0),
            "sample_size": int(r.get("sample_size") or 0),
            "threshold": float(r.get("threshold") or 0),
        })
    for pts in by_metric.values():
        pts.sort(key=lambda p: p["day"])
    return by_metric


def http_delta_series(snapshots: list[dict[str, Any]], tz, day_keys: list[str]) -> list[dict[str, Any]]:
    """PURA: deriva volumen por día desde snapshots HTTP acumulados (reset-aware).

    Por ruta, ordena por `taken_at` y resta contadores consecutivos: el delta es el tráfico
    del intervalo, atribuido al día del snapshot MÁS NUEVO del par. Un delta negativo indica
    reinicio del proceso (los contadores volvieron a 0) → se usa el contador nuevo tal cual.
    El PRIMER snapshot de cada ruta es solo línea base (no hay intervalo previo). `peak_p95_ms`
    por día = el peor p95 observado (gauge, no acumulado)."""
    keyset = set(day_keys)
    acc: dict[str, dict[str, int]] = {
        d: {"requests": 0, "errors": 0, "client_errors": 0, "peak_p95_ms": 0} for d in day_keys
    }
    by_route: dict[str, list[dict[str, Any]]] = {}
    for s in snapshots:
        by_route.setdefault(str(s.get("route") or "?"), []).append(s)
    for snaps in by_route.values():
        snaps.sort(key=lambda s: str(s.get("taken_at") or ""))
        prev = None
        for s in snaps:
            day = _parse_dt(s.get("taken_at")).astimezone(tz).date().isoformat()
            cur = {k: int(s.get(k) or 0) for k in ("count", "errors", "client_errors")}
            if prev is not None and day in keyset:
                b = acc[day]
                for src, dst in (("count", "requests"), ("errors", "errors"), ("client_errors", "client_errors")):
                    delta = cur[src] - prev[src]
                    b[dst] += delta if delta >= 0 else cur[src]  # reset → contador nuevo
                b["peak_p95_ms"] = max(b["peak_p95_ms"], int(s.get("p95_ms") or 0))
            prev = cur
    return [{"day": d, **acc[d]} for d in day_keys]


@router.get("/api/ops/timeseries")
def get_timeseries(
    days: int = 14, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Series de tiempo para el dashboard: operación LLM (calls/errors/tokens/costo/latencia)
    y calidad POR TENANT + rendimiento HTTP del proceso. `days` acotado a 1..90."""
    days = max(1, min(MAX_DAYS, int(days)))
    tenant_id = user["tenant_id"]

    scheduling = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, tenant_id) or {}
    tzname = scheduling.get("timezone") or "America/Lima"
    tz = _now_local(tzname).tzinfo
    since_day = (_now_local(tzname) - timedelta(days=days - 1)).date()
    since_iso = datetime.combine(since_day, time.min).replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
    day_keys = _day_keys(since_day, days)

    vac_ids = {v["id"] for v in repo.list_vacancies(tenant_id=tenant_id)}
    pricing = repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, tenant_id)
    llm = llm_daily_series(repo.usage_timeseries_rows_since(since_iso), vac_ids, tz, day_keys, pricing)
    quality = quality_series(repo.list_quality_metrics_since(tenant_id, since_day.isoformat()), day_keys)
    http = http_delta_series(repo.http_snapshots_since(since_iso), tz, day_keys)

    return {
        "days": days,
        "since": since_day.isoformat(),
        "timezone": tzname,
        "day_keys": day_keys,
        "llm": llm,
        "quality": quality,
        "http": http,
    }
