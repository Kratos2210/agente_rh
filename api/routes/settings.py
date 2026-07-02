"""Endpoints de configuración por-tenant (agendamiento, auto-contacto, inactividad,
retención). Lecturas para cualquier usuario autenticado; mutaciones solo admin."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from api.auth import get_current_user, require_role
from api.deps import _audit
from api.runtime import (
    _DEFAULT_AUTO_CONTACT,
    _DEFAULT_INACTIVITY,
    _DEFAULT_LLM_BUDGET,
    _DEFAULT_LLM_PRICING,
    _DEFAULT_RETENTION,
    _DEFAULT_SCHEDULING,
    _DEFAULT_SLA_ALERTS,
)
from db import repositories as repo

router = APIRouter()

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: str) -> str:
    """Valida un string "HH:MM" (24h). Lanza ValueError si no cumple (→ 422 de FastAPI)."""
    if not _HHMM_RE.match(str(value).strip()):
        raise ValueError(f"Hora inválida '{value}': usa formato HH:MM (24h)")
    return value


class SchedulingIn(BaseModel):
    enabled: bool = True
    provider: str = "simulated"             # "simulated" | "google"
    slot_minutes: int = Field(default=45, gt=0)
    work_days: list[int] = [1, 2, 3, 4, 5]  # ISO: 1=lunes .. 7=domingo
    work_start: str = "09:00"               # ventana única (compat hacia atrás)
    work_end: str = "18:00"
    work_windows: list[list[str]] = [["09:00", "18:00"]]  # franjas [inicio, fin] del auto-contacto
    timezone: str = "America/Lima"
    horizon_days: int = Field(default=7, ge=1)
    options: int = Field(default=3, ge=1, le=5)

    @field_validator("provider")
    @classmethod
    def _provider_valid(cls, v: str) -> str:
        if v not in ("simulated", "google"):
            raise ValueError("provider debe ser 'simulated' o 'google'")
        return v

    @field_validator("work_days")
    @classmethod
    def _work_days_valid(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("work_days: cada día debe estar entre 1 (lunes) y 7 (domingo)")
        return v

    @field_validator("work_start", "work_end")
    @classmethod
    def _times_valid(cls, v: str) -> str:
        return _validate_hhmm(v)

    @field_validator("work_windows")
    @classmethod
    def _windows_valid(cls, v: list[list[str]]) -> list[list[str]]:
        for w in v:
            if len(w) != 2:
                raise ValueError("work_windows: cada franja debe ser [inicio, fin]")
            _validate_hhmm(w[0])
            _validate_hhmm(w[1])
        return v


class AutoContactIn(BaseModel):
    enabled: bool = False
    times: list[str] = ["11:00", "15:00"]   # horas "HH:MM" a las que se contacta
    timezone: str = "America/Lima"

    @field_validator("times")
    @classmethod
    def _times_valid(cls, v: list[str]) -> list[str]:
        return [_validate_hhmm(t) for t in v]


class InactivityIn(BaseModel):
    enabled: bool = True
    reminder_minutes: int = Field(default=2, ge=1)   # silencio antes de recordar / reintentar
    max_reminders: int = Field(default=2, ge=0)      # recordatorios antes de cerrar "No respondió"


class RetentionIn(BaseModel):
    enabled: bool = False
    days: int = Field(default=180, ge=0)


class ModelPriceIn(BaseModel):
    """Precio por millón de tokens (USD) de un modelo."""
    input_per_1m: float = Field(default=0.0, ge=0)
    output_per_1m: float = Field(default=0.0, ge=0)


class LlmPricingIn(BaseModel):
    """Precios LLM del tenant (O-2): por modelo + default para modelos sin fila propia."""
    models: dict[str, ModelPriceIn] = {}
    default: ModelPriceIn = ModelPriceIn()


class LlmBudgetIn(BaseModel):
    """Presupuesto LLM mensual del tenant (O-2): alerta al alcanzar `alert_pct`%."""
    enabled: bool = False
    monthly_usd: float = Field(default=0.0, ge=0)
    alert_pct: int = Field(default=80, ge=1, le=100)
    notify_email: str = ""


class SlaAlertsIn(BaseModel):
    """SLAs push del tenant (O-4): correo al incumplirse una condición (1×/condición/día)."""
    enabled: bool = False
    notify_email: str = ""
    ops_alerts: bool = True                       # empuja las alertas operativas
    turn_p95_ms: int = Field(default=0, ge=0)     # umbral p95 del turno (últimas 24 h; 0 = off)


@router.get("/api/settings/scheduling")
def get_scheduling(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"])


@router.put("/api/settings/scheduling")
def put_scheduling(
    payload: SchedulingIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("scheduling", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="scheduling")
    return repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"])


@router.get("/api/settings/auto-contact")
def get_auto_contact(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("auto_contact", _DEFAULT_AUTO_CONTACT, user["tenant_id"])


@router.put("/api/settings/auto-contact")
def put_auto_contact(
    payload: AutoContactIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("auto_contact", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="auto_contact")
    return repo.get_app_setting("auto_contact", _DEFAULT_AUTO_CONTACT, user["tenant_id"])


@router.get("/api/settings/inactivity")
def get_inactivity(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("inactivity", _DEFAULT_INACTIVITY, user["tenant_id"])


@router.put("/api/settings/inactivity")
def put_inactivity(
    payload: InactivityIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("inactivity", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="inactivity")
    return repo.get_app_setting("inactivity", _DEFAULT_INACTIVITY, user["tenant_id"])


@router.get("/api/settings/retention")
def get_retention(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("retention", _DEFAULT_RETENTION, user["tenant_id"])


@router.put("/api/settings/retention")
def put_retention(
    payload: RetentionIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("retention", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="retention")
    return repo.get_app_setting("retention", _DEFAULT_RETENTION, user["tenant_id"])


@router.get("/api/settings/llm-pricing")
def get_llm_pricing(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, user["tenant_id"])


@router.put("/api/settings/llm-pricing")
def put_llm_pricing(
    payload: LlmPricingIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("llm_pricing", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="llm_pricing")
    return repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, user["tenant_id"])


@router.get("/api/settings/llm-budget")
def get_llm_budget(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("llm_budget", _DEFAULT_LLM_BUDGET, user["tenant_id"])


@router.put("/api/settings/llm-budget")
def put_llm_budget(
    payload: LlmBudgetIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("llm_budget", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="llm_budget")
    return repo.get_app_setting("llm_budget", _DEFAULT_LLM_BUDGET, user["tenant_id"])


@router.get("/api/settings/sla-alerts")
def get_sla_alerts(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("sla_alerts", _DEFAULT_SLA_ALERTS, user["tenant_id"])


@router.put("/api/settings/sla-alerts")
def put_sla_alerts(
    payload: SlaAlertsIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("sla_alerts", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="sla_alerts")
    return repo.get_app_setting("sla_alerts", _DEFAULT_SLA_ALERTS, user["tenant_id"])
