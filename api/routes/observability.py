"""Endpoints de observabilidad (solo admin): auditoría, alertas operativas y outbox."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_role
from api.deps import _audit
from api.scheduler import _collect_ops_alerts
from db import repositories as repo

router = APIRouter()


@router.get("/api/audit")
def list_audit(user: dict[str, Any] = Depends(require_role("admin"))) -> list[dict[str, Any]]:
    """Registro de acciones del dashboard del tenant (quién/qué/cuándo)."""
    return repo.list_audit_log(user["tenant_id"])


@router.get("/api/ops/alerts")
def list_ops_alerts(user: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    """Alertas operativas del tenant (O2): dead-letters, reuniones sin enlace, coordinaciones
    estancadas y divergencias motor↔negocio — las mismas señales que el barrido de
    reconciliación, ahora visibles en el dashboard (antes solo iban a logs)."""
    return {"alerts": _collect_ops_alerts(user["tenant_id"])}


@router.get("/api/ops/quality")
def get_quality_metrics(user: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    """Signo vital de calidad del tenant (paso 4): tendencia diaria de fundamentación y
    relevancia de las respuestas del bot, producida por el barrido continuo `_quality_sweep`
    (juez LLM sobre trazas answer). Vacío hasta que se active `quality_alerts` + tracing."""
    rows = repo.list_quality_metrics(user["tenant_id"])
    return {"metrics": rows}


@router.get("/api/ops/http-metrics")
def get_http_metrics(user: dict[str, Any] = Depends(require_role("admin"))) -> dict[str, Any]:
    """Métricas HTTP del proceso (O3): conteo, errores y latencia por ruta.

    Ámbito: proceso completo (no por tenant) — es diagnóstico de infraestructura,
    solo admin. Con N réplicas cada una reporta lo suyo."""
    from api.httpmetrics import http_metrics

    return {"routes": http_metrics.snapshot()}


@router.get("/api/outbox")
def get_outbox_health(
    user: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Salud de los envíos salientes del tenant: conteo por estado + pendientes/dead-letters."""
    tenant_id = user["tenant_id"]
    counts = repo.count_outbox_by_status(tenant_id)
    items = repo.list_outbox(tenant_id, statuses=["pending", "failed"], limit=100)
    return {"counts": counts, "items": items}


@router.post("/api/outbox/{outbox_id}/retry")
def retry_outbox(
    outbox_id: str, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Reintenta un envío detenido (dead-letter o pendiente): lo marca vencido para el próximo drenaje."""
    from datetime import datetime, timezone

    row = repo.get_outbox(outbox_id)
    if not row or str(row.get("tenant_id")) != str(user["tenant_id"]):
        raise HTTPException(404, "Envío no encontrado")
    if row.get("status") == "sent":
        raise HTTPException(409, "El envío ya fue entregado.")
    repo.update_outbox(
        outbox_id,
        {
            "status": "pending",
            "next_attempt_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _audit(user, "outbox.retry", entity_type="outbox", entity_id=outbox_id,
           summary=str(row.get("kind", "")))
    return {"requeued": True}
