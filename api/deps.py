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


def compute_cost(by_model: dict[str, dict[str, int]], pricing: dict[str, Any]) -> dict[str, Any]:
    """Costo estimado a partir de tokens por modelo × precios por millón (O-2). Puro.

    `pricing` = {"models": {model: {input_per_1m, output_per_1m}}, "default": {...}};
    un modelo sin fila propia usa "default". Devuelve {"total", "by_model"} en USD."""
    models = pricing.get("models") or {}
    default = pricing.get("default") or {}
    per_model: dict[str, float] = {}
    total = 0.0
    for model, toks in (by_model or {}).items():
        p = models.get(model) or default
        cost = (
            int(toks.get("input", 0) or 0) / 1_000_000 * float(p.get("input_per_1m", 0) or 0)
            + int(toks.get("output", 0) or 0) / 1_000_000 * float(p.get("output_per_1m", 0) or 0)
        )
        if cost > 0:
            per_model[model] = round(cost, 4)
        total += cost
    return {"total": round(total, 4), "by_model": per_model}


def _with_cost(metrics: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
    """Añade la estimación de costo a un dict de métricas (O-2): tokens por modelo ×
    precios por-tenant (`app_settings.llm_pricing`). Retro-compat: sin precios por
    modelo configurados cae al escalar global `token_price_per_1k` (legado)."""
    from api.runtime import _DEFAULT_LLM_PRICING

    pricing = (
        repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, tenant_id)
        if tenant_id else _DEFAULT_LLM_PRICING
    ) or _DEFAULT_LLM_PRICING
    tokens = metrics.get("tokens") or {}
    cost = compute_cost(tokens.get("by_model") or {}, pricing)
    if cost["total"] <= 0:
        price = float(getattr(current_settings(), "token_price_per_1k", 0.0) or 0.0)
        total = int(tokens.get("total", 0))
        cost = {"total": round(total / 1000 * price, 4) if price else 0.0, "by_model": {}}
    metrics["est_cost"] = cost["total"]
    metrics["cost_by_model"] = cost["by_model"]
    return metrics
