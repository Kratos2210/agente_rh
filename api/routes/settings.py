"""Endpoints de configuración por-tenant (agendamiento, auto-contacto, inactividad,
retención). Lecturas para cualquier usuario autenticado; mutaciones solo admin."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from api.auth import get_current_user, require_role
from api.deps import _audit
from api.runtime import (
    _DEFAULT_AUTO_CONTACT,
    _DEFAULT_INACTIVITY,
    _DEFAULT_LLM_BUDGET,
    _DEFAULT_LLM_PRICING,
    _DEFAULT_LLM_PROVIDER,
    _DEFAULT_MEDICAL,
    _DEFAULT_QUALITY_ALERTS,
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


class QualityAlertsIn(BaseModel):
    """Medición continua de calidad del tenant (paso 4): juzga trazas answer 1×/día."""
    enabled: bool = False
    sample: int = Field(default=20, ge=1, le=200)         # trazas a muestrear por día
    min_rate: float = Field(default=0.9, ge=0.0, le=1.0)  # umbral de fundamentación
    notify_email: str = ""


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


class MedicalExamSettingsIn(BaseModel):
    """Examen médico pre-contratación (auditoría v3): activo, aprobar gerencia pasa a
    medical_pending en vez de contratar directo."""
    enabled: bool = False


@router.get("/api/settings/medical-exam")
def get_medical_exam_settings(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("medical_exam", _DEFAULT_MEDICAL, user["tenant_id"])


@router.put("/api/settings/medical-exam")
def put_medical_exam_settings(
    payload: MedicalExamSettingsIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("medical_exam", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="medical_exam")
    return repo.get_app_setting("medical_exam", _DEFAULT_MEDICAL, user["tenant_id"])


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


# ── Proveedor LLM por-tenant (BYOK + hot-swap) ────────────────────────────────────

class LlmProviderIn(BaseModel):
    """Proveedor LLM del tenant. `api_key` vacía en el PUT = conservar la almacenada.
    Con `enabled` apagado todo sigue saliendo del `.env` (retrocompat)."""
    enabled: bool = False
    provider: str = "groq"
    base_url: str = ""                 # requerida solo con provider="custom"
    api_key: str = ""                  # "" = mantener la key cifrada previa
    model: str = ""
    cheap_model: str = ""              # opcional: modelo barato para etapas simples
    cheap_stages: str = "schedule"     # CSV de etapas ruteadas al modelo barato

    @field_validator("provider")
    @classmethod
    def _provider_valid(cls, v: str) -> str:
        from orquestacion.providers import PROVIDERS

        if v not in PROVIDERS:
            raise ValueError(f"provider debe ser uno de: {', '.join(PROVIDERS)}")
        return v


def _llm_provider_masked(stored: dict[str, Any]) -> dict[str, Any]:
    """Vista del setting para el GET/PUT: sin `api_key_encrypted`, con `api_key_masked`."""
    from orquestacion.providers import decrypt_api_key, mask_api_key

    out = {**_DEFAULT_LLM_PROVIDER, **{k: v for k, v in stored.items() if k != "api_key_encrypted"}}
    key = decrypt_api_key(stored.get("api_key_encrypted") or "")
    out["api_key_masked"] = mask_api_key(key) if key else ""
    return out


def _seed_llm_pricing(tenant_id: str, provider: str, models: list[str]) -> None:
    """Siembra `llm_pricing` con los precios sugeridos del catálogo para los modelos
    elegidos, SIN pisar filas existentes → el costo queda mapeado al cambiar de proveedor."""
    from orquestacion.providers import suggested_prices_for

    suggested = suggested_prices_for(provider, models)
    if not suggested:
        return
    pricing = repo.get_app_setting("llm_pricing", _DEFAULT_LLM_PRICING, tenant_id) or {}
    rows = dict(pricing.get("models") or {})
    added = {mid: price for mid, price in suggested.items() if mid not in rows}
    if added:
        rows.update(added)
        repo.set_app_setting("llm_pricing", {**pricing, "models": rows}, tenant_id)


@router.get("/api/settings/llm-provider")
def get_llm_provider(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    stored = repo.get_app_setting("llm_provider", None, user["tenant_id"]) or {}
    return _llm_provider_masked(stored)


@router.get("/api/settings/llm-provider/catalog")
def get_llm_provider_catalog(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Catálogo de proveedores (base URLs + modelos sugeridos con precio) para el dashboard."""
    from orquestacion.providers import PROVIDERS

    return {"providers": PROVIDERS}


@router.put("/api/settings/llm-provider")
def put_llm_provider(
    payload: LlmProviderIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    from orquestacion import providers

    prev = repo.get_app_setting("llm_provider", None, user["tenant_id"]) or {}
    base_url = payload.base_url.strip() or providers.provider_base_url(payload.provider)
    if payload.enabled and not base_url:
        raise HTTPException(422, "Proveedor personalizado: indica la base URL (compatible OpenAI)")
    new_key = payload.api_key.strip()
    encrypted = providers.encrypt_api_key(new_key) if new_key else (prev.get("api_key_encrypted") or "")
    if payload.enabled and not encrypted:
        raise HTTPException(422, "Ingresa la API key del proveedor")
    model = payload.model.strip()
    if payload.enabled and not model:
        raise HTTPException(422, "Indica el modelo a usar")
    stored = {
        "enabled": payload.enabled,
        "provider": payload.provider,
        "base_url": base_url,
        "api_key_encrypted": encrypted,
        "model": model,
        "cheap_model": payload.cheap_model.strip(),
        "cheap_stages": payload.cheap_stages.strip(),
    }
    repo.set_app_setting("llm_provider", stored, user["tenant_id"])
    _seed_llm_pricing(user["tenant_id"], payload.provider, [stored["model"], stored["cheap_model"]])
    providers.invalidate_config_cache(user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="llm_provider")
    return _llm_provider_masked(stored)


def _build_test_llm(cfg: dict[str, Any]):
    """Builder del LLM efímero de /test (nombre propio para monkeypatch en tests)."""
    from orquestacion.providers import build_llm_from_config

    return build_llm_from_config(cfg)


def _scrub_secret(text: str, secret: str) -> str:
    return text.replace(secret, "•••") if secret else text


@router.post("/api/settings/llm-provider/test")
def test_llm_provider(
    payload: LlmProviderIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Prueba de conexión SIN persistir: completion mínima con el proveedor del body
    (key vacía = usa la almacenada). Nunca devuelve la key (ni en errores)."""
    import time

    from orquestacion import providers

    base_url = payload.base_url.strip() or providers.provider_base_url(payload.provider)
    api_key = payload.api_key.strip()
    if not api_key:
        prev = repo.get_app_setting("llm_provider", None, user["tenant_id"]) or {}
        api_key = providers.decrypt_api_key(prev.get("api_key_encrypted") or "") or ""
    model = payload.model.strip()
    if not (base_url and api_key and model):
        raise HTTPException(422, "Completa proveedor, modelo y API key para probar la conexión")
    cfg = {"provider": payload.provider, "base_url": base_url, "api_key": api_key, "model": model}
    t0 = time.perf_counter()
    try:
        out = _build_test_llm(cfg).complete("Responde exactamente: OK")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": True, "latency_ms": latency_ms, "model": model,
                "sample": (out or "").strip()[:80]}
    except Exception as exc:  # noqa: BLE001 — el error del proveedor ES el resultado del test
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "model": model,
                "error": _scrub_secret(str(exc), api_key)[:300]}


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


@router.get("/api/settings/quality-alerts")
def get_quality_alerts(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("quality_alerts", _DEFAULT_QUALITY_ALERTS, user["tenant_id"])


@router.put("/api/settings/quality-alerts")
def put_quality_alerts(
    payload: QualityAlertsIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("quality_alerts", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="quality_alerts")
    return repo.get_app_setting("quality_alerts", _DEFAULT_QUALITY_ALERTS, user["tenant_id"])
