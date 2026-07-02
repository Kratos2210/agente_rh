"""Dependencias/guards/helpers compartidos por los routers del dashboard.

Guards de tenant (F2), auditoría de acciones (#8) y helpers de listados (D1/U1).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from api.runtime import current_settings
from db import repositories as repo
from src.logging_config import get_logger

logger = get_logger("api.deps")


def _require_vacancy_in_tenant(vacancy_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Carga la vacante y verifica que pertenezca al tenant del usuario (si no, 404)."""
    vac = repo.get_vacancy(vacancy_id)
    if not vac or vac.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Vacante no encontrada")
    return vac


def _require_candidate_in_tenant(
    candidate_id: str, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Carga el candidato + su vacante y verifica el tenant (si no, 404). Devuelve (cand, vac)."""
    cand = repo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(404, "Candidato no encontrado")
    vac = repo.get_vacancy(cand.get("vacancy_id"))
    if not vac or vac.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Candidato no encontrado")
    return cand, vac


def _audit(user: dict[str, Any], action: str, *, entity_type: str = "", entity_id: str = "", summary: str = "") -> None:
    """Registra una acción del dashboard (quién/qué/cuándo). No rompe la acción si falla (audit #8)."""
    try:
        repo.add_audit_log(
            {
                "tenant_id": user.get("tenant_id"),
                "actor_user_id": user.get("id"),
                "actor_email": user.get("email") or "",
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "summary": summary,
            }
        )
    except Exception:  # noqa: BLE001 — la auditoría no debe tumbar la acción
        logger.exception("No se pudo registrar la auditoría (%s)", action)


def _candidate_row_from_embed(c: dict[str, Any]) -> dict[str, Any]:
    """Fila de candidato con semáforo/score y prescreen, para listas y pipeline.

    Consume el embed `conversations(scorecards)` de `repo.list_candidate_rows` (D1:
    cero consultas extra por candidato). Usa la conversación más reciente. PostgREST
    embebe como OBJETO las relaciones que detecta to-one (scorecards tiene unique de
    conversation_id) y como lista las to-many — se aceptan ambas formas."""
    raw = c.get("conversations") or []
    convs = [raw] if isinstance(raw, dict) else list(raw)
    convs.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    conv = convs[0] if convs else None
    cards = (conv or {}).get("scorecards")
    scorecard = cards if isinstance(cards, dict) else (cards[0] if cards else None)
    prescreen = c.get("prescreen") or {}
    return {
        "id": c["id"],
        "name": c["name"],
        "status": c["status"],
        "channel": c["channel"],
        "source": c.get("source", "telegram"),
        "created_at": c["created_at"],
        "vacancy_id": c.get("vacancy_id"),
        "conversation_id": conv["id"] if conv else None,
        "semaphore": scorecard["semaphore"] if scorecard else None,
        "total_score": scorecard["total_score"] if scorecard else None,
        "prescreen_score": prescreen.get("pre_score"),
        "prescreen_verdict": prescreen.get("verdict"),
    }


def _page_params(limit: int, offset: int) -> tuple[int, int]:
    """Sanea limit/offset de paginación (U1): 1 ≤ limit ≤ 500, offset ≥ 0."""
    return (max(1, min(limit, 500)), max(0, offset))


def _with_cost(metrics: dict[str, Any]) -> dict[str, Any]:
    """Añade la estimación de costo (tokens × precio configurado) a un dict de métricas."""
    price = float(getattr(current_settings(), "token_price_per_1k", 0.0) or 0.0)
    total = int((metrics.get("tokens") or {}).get("total", 0))
    metrics["est_cost"] = round(total / 1000 * price, 4) if price else 0.0
    return metrics
