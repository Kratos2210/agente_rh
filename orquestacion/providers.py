"""Proveedor LLM configurable por-tenant (BYOK) con hot-swap.

El tenant elige proveedor + modelo + API key desde /configuracion (app_setting
`llm_provider`). Todos los proveedores del catálogo exponen endpoint compatible-OpenAI,
así que el camino de código es uno solo: `ChatOpenAI(base_url=, api_key=)` vía
`build_default_llm`. Con el setting ausente/apagado TODO sale del `.env` como siempre
(retrocompat total: tests, scripts, demo).

Seguridad: la API key se persiste cifrada (Fernet) con clave derivada de `jwt_secret`
(patrón de adaptadores_mcp/mcp.py). Caveat documentado: rotar `JWT_SECRET` invalida la
key almacenada — el decrypt falla con warning y se cae al `.env` (fail-open); basta
re-ingresar la key en el dashboard.

Hot-swap: `resolve_llm_config` cachea el setting por tenant con TTL 60 s (patrón
`_is_user_revoked` de api/auth.py) y `refresh_metered_llm` reconstruye el LLM del bot
solo cuando cambia el fingerprint del config → cambio efectivo en ≤60 s sin reiniciar.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Catálogo de proveedores (endpoint compatible-OpenAI + modelos sugeridos) ──────
# Los precios son SUGERIDOS (USD por millón de tokens) y solo se usan para sembrar
# `llm_pricing` al guardar — el tenant puede editarlos en Costos. Los modelos son un
# datalist: el usuario puede escribir cualquier id que el proveedor acepte.

PROVIDERS: dict[str, dict[str, Any]] = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            # Precios verificados 2026-07-31. `qwen/qwen3-32b` y `moonshotai/kimi-k2-instruct`
            # se quitaron: Groq los retiró (ver docs/adr-seleccion-modelo.md).
            {"id": "qwen/qwen3.6-27b", "input_per_1m": 0.60, "output_per_1m": 3.00},
            {"id": "llama-3.1-8b-instant", "input_per_1m": 0.05, "output_per_1m": 0.08},
            {"id": "llama-3.3-70b-versatile", "input_per_1m": 0.59, "output_per_1m": 0.79},
            {"id": "openai/gpt-oss-20b", "input_per_1m": 0.075, "output_per_1m": 0.30},
            {"id": "openai/gpt-oss-120b", "input_per_1m": 0.15, "output_per_1m": 0.60},
        ],
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [
            {"id": "gemini-2.5-flash", "input_per_1m": 0.30, "output_per_1m": 2.50},
            {"id": "gemini-2.5-flash-lite", "input_per_1m": 0.10, "output_per_1m": 0.40},
            {"id": "gemini-2.5-pro", "input_per_1m": 1.25, "output_per_1m": 10.00},
        ],
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        # Los endpoints "Free" de build.nvidia.com no facturan por token → precio 0.
        "models": [
            {"id": "meta/llama-3.3-70b-instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "meta/llama-3.1-405b-instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "nvidia/llama-3.1-nemotron-70b-instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "qwen/qwen2.5-coder-32b-instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
        ],
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-5-mini", "input_per_1m": 0.25, "output_per_1m": 2.00},
            {"id": "gpt-5", "input_per_1m": 1.25, "output_per_1m": 10.00},
            {"id": "gpt-4o-mini", "input_per_1m": 0.15, "output_per_1m": 0.60},
            {"id": "gpt-4.1-mini", "input_per_1m": 0.40, "output_per_1m": 1.60},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "meta-llama/llama-3.3-70b-instruct", "input_per_1m": 0.10, "output_per_1m": 0.25},
            {"id": "qwen/qwen3-32b", "input_per_1m": 0.10, "output_per_1m": 0.30},
            {"id": "deepseek/deepseek-chat-v3-0324", "input_per_1m": 0.25, "output_per_1m": 0.85},
        ],
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "models": [
            {"id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "input_per_1m": 0.88, "output_per_1m": 0.88},
            {"id": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", "input_per_1m": 0.18, "output_per_1m": 0.18},
            {"id": "Qwen/Qwen2.5-72B-Instruct-Turbo", "input_per_1m": 1.20, "output_per_1m": 1.20},
        ],
    },
    "ollama": {
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434/v1",
        # Modelos locales: no facturan por token → precio 0. Editable si Ollama corre en otro host.
        "models": [
            {"id": "llama3.1", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "llama3.2", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "qwen2.5", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "mistral", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "gemma2", "input_per_1m": 0.0, "output_per_1m": 0.0},
        ],
    },
    "huggingface": {
        "label": "Hugging Face",
        "base_url": "https://router.huggingface.co/v1",
        # El costo depende del proveedor al que enrute HF (Inference Providers) → 0 por defecto;
        # edítalo en Costos según el provider real que uses.
        "models": [
            {"id": "meta-llama/Llama-3.3-70B-Instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "meta-llama/Llama-3.1-8B-Instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "Qwen/Qwen2.5-72B-Instruct", "input_per_1m": 0.0, "output_per_1m": 0.0},
            {"id": "mistralai/Mistral-7B-Instruct-v0.3", "input_per_1m": 0.0, "output_per_1m": 0.0},
        ],
    },
    "custom": {
        "label": "Personalizado (compatible OpenAI)",
        "base_url": "",
        "models": [],
    },
}

SETTING_KEY = "llm_provider"

# Fingerprint del comportamiento por defecto (LLM del .env). Es el valor inicial de
# `MeteredLLM.config_fingerprint`: sin config en DB nunca se reconstruye nada.
ENV_FINGERPRINT = "env"


def provider_base_url(provider: str) -> str:
    return str(PROVIDERS.get(provider, {}).get("base_url", "") or "")


def is_private_host(hostname: str) -> bool:
    """True si el host resuelve a alguna IP privada/loopback/link-local/reservada
    (o no resuelve — conservador). IPs literales no requieren red."""
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def assert_public_llm_endpoint(base_url: str, settings) -> None:
    """Anti-SSRF: en producción el endpoint del proveedor debe ser público — un admin de
    tenant no debe poder sondear la red interna/metadata (169.254.169.254) vía base_url.
    En dev no aplica (Ollama local); en prod self-hosted se abre con
    ALLOW_PRIVATE_LLM_ENDPOINTS=true."""
    if not getattr(settings, "is_production", False):
        return
    if getattr(settings, "allow_private_llm_endpoints", False):
        return
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").strip()
    if not host or is_private_host(host):
        raise ValueError(
            "El endpoint del proveedor apunta a una red privada o no resoluble; en "
            "producción solo se permiten endpoints públicos "
            "(ALLOW_PRIVATE_LLM_ENDPOINTS=true para self-hosted)."
        )


def suggested_prices_for(provider: str, models: list[str]) -> dict[str, dict[str, float]]:
    """Filas de precio del catálogo para los modelos dados (siembra de `llm_pricing`)."""
    catalog = {m["id"]: m for m in PROVIDERS.get(provider, {}).get("models", [])}
    out: dict[str, dict[str, float]] = {}
    for mid in models:
        row = catalog.get((mid or "").strip())
        if row:
            out[row["id"]] = {
                "input_per_1m": float(row["input_per_1m"]),
                "output_per_1m": float(row["output_per_1m"]),
            }
    return out


# ── Cifrado de la API key (Fernet, clave derivada de jwt_secret) ──────────────────

def _fernet():
    from cryptography.fernet import Fernet

    from core.config import get_settings

    digest = hashlib.sha256(f"{get_settings().jwt_secret}|llm-provider".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(token: str) -> str | None:
    """Descifra la key almacenada. None si falla (p.ej. rotó JWT_SECRET) → fail-open
    al `.env`; el admin re-ingresa la key en el dashboard."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001
        logger.warning(
            "No se pudo descifrar la API key del proveedor LLM (¿rotó JWT_SECRET?); "
            "se usa el LLM del .env. Re-ingresa la key en Configuración."
        )
        return None


def mask_api_key(api_key: str) -> str:
    """Vista enmascarada para el GET: prefijo + últimos 4 (nunca la key completa)."""
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}...{key[-4:]}"


# ── Resolución del config por tenant (caché TTL, fail-open al .env) ───────────────

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[dict[str, Any] | None, float]] = {}
_cache_lock = threading.Lock()


def invalidate_config_cache(tenant_id: str | None = None) -> None:
    """El PUT de settings invalida su proceso; otros procesos convergen por TTL."""
    with _cache_lock:
        if tenant_id is None:
            _cache.clear()
        else:
            _cache.pop(tenant_id, None)


def resolve_llm_config(tenant_id: str | None) -> dict[str, Any] | None:
    """Config vigente del proveedor LLM del tenant, o None = usar el `.env`.

    None también ante: setting ausente/apagado, key indescifrable, campos incompletos
    o DB caída (fail-open: la entrevista nunca se cae por config)."""
    if not tenant_id:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(tenant_id)
        if hit and hit[1] > now:
            return hit[0]
    cfg = _load_config(tenant_id)
    with _cache_lock:
        _cache[tenant_id] = (cfg, now + _CACHE_TTL_SECONDS)
    return cfg


def _load_config(tenant_id: str) -> dict[str, Any] | None:
    try:
        from db import repositories as repo

        raw = repo.get_app_setting(SETTING_KEY, None, tenant_id)
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo leer llm_provider del tenant %s; se usa el .env", tenant_id)
        return None
    if not raw or not raw.get("enabled"):
        return None
    api_key = decrypt_api_key(raw.get("api_key_encrypted") or "")
    if not api_key:
        return None
    provider = str(raw.get("provider") or "custom")
    base_url = (raw.get("base_url") or "").strip() or provider_base_url(provider)
    model = (raw.get("model") or "").strip()
    if not base_url or not model:
        return None
    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "cheap_model": (raw.get("cheap_model") or "").strip(),
        "cheap_stages": str(raw.get("cheap_stages") or ""),
    }


def config_fingerprint(cfg: dict[str, Any] | None) -> str:
    """Identidad del config para decidir si reconstruir el LLM (nunca la key en claro)."""
    if not cfg:
        return ENV_FINGERPRINT
    key_digest = hashlib.sha256((cfg.get("api_key") or "").encode()).hexdigest()
    blob = "|".join([
        cfg.get("provider", ""), cfg.get("base_url", ""), cfg.get("model", ""),
        cfg.get("cheap_model", ""), cfg.get("cheap_stages", ""), key_digest,
    ])
    return hashlib.sha256(blob.encode()).hexdigest()


# ── Construcción de LLMs desde el config ──────────────────────────────────────────

def build_llm_from_config(cfg: dict[str, Any], model: str | None = None):
    from orquestacion.llm import build_default_llm

    return build_default_llm(
        model or cfg["model"], base_url=cfg["base_url"], api_key=cfg["api_key"]
    )


def build_overrides_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Espejo de `build_stage_overrides` con el cheap_model/cheap_stages del config."""
    cheap = (cfg.get("cheap_model") or "").strip()
    if not cheap:
        return {}
    stages = [s.strip() for s in (cfg.get("cheap_stages") or "").split(",") if s.strip()]
    if not stages:
        return {}
    cheap_llm = build_llm_from_config(cfg, model=cheap)
    return {stage: cheap_llm for stage in stages}


def _build_pair(cfg: dict[str, Any] | None, settings):
    """(inner, overrides) según el config, o desde el `.env` si config es None.

    El LLM principal se envuelve con el proveedor de RESPALDO de la instalación
    (`LLM_FALLBACK_*`, no-op sin config): cubre tanto el `.env` como el BYOK caído.
    Los overrides baratos no llevan respaldo (su call site ya degrada al heurístico)."""
    from orquestacion.fallback import wrap_with_fallback
    from orquestacion.llm import build_default_llm, build_stage_overrides

    if cfg is None:
        inner, overrides = build_default_llm(), build_stage_overrides(settings)
    else:
        inner, overrides = build_llm_from_config(cfg), build_overrides_from_config(cfg)
    return wrap_with_fallback(inner, settings), overrides


def build_tenant_metered_llm(tenant_id: str | None, settings):
    """MeteredLLM listo para call sites por-request (p.ej. sync-applicants)."""
    from orquestacion.llm import MeteredLLM, build_stage_max_tokens

    cfg = resolve_llm_config(tenant_id)
    inner, overrides = _build_pair(cfg, settings)
    metered = MeteredLLM(
        inner,
        trace=settings.llm_trace_enabled,
        trace_max_chars=settings.llm_trace_max_chars,
        overrides=overrides,
        max_tokens_by_stage=build_stage_max_tokens(settings),  # techo de gasto (R6)
    )
    metered.config_fingerprint = config_fingerprint(cfg)
    return metered


def refresh_metered_llm(metered, tenant_id: str | None) -> None:
    """Hot-swap del LLM del bot: si el config del tenant cambió (fingerprint), reconstruye
    inner+overrides preservando la metadata de tracing. No-op con LLMs sin `reconfigure`
    (fakes de tests) o si nada cambió (un lookup cacheado por turno)."""
    reconfigure = getattr(metered, "reconfigure", None)
    if not callable(reconfigure):
        return
    cfg = resolve_llm_config(tenant_id)
    fingerprint = config_fingerprint(cfg)
    if getattr(metered, "config_fingerprint", None) == fingerprint:
        return
    from core.config import get_settings

    inner, overrides = _build_pair(cfg, get_settings())
    old_meta = getattr(getattr(metered, "_inner", None), "metadata", None)
    if isinstance(old_meta, dict) and old_meta:
        for llm in (inner, *overrides.values()):
            meta = getattr(llm, "metadata", None)
            if isinstance(meta, dict):
                meta.update(old_meta)
    reconfigure(inner, overrides, fingerprint)
    logger.info(
        "LLM del tenant %s reconfigurado en caliente → %s @ %s",
        tenant_id,
        (cfg or {}).get("model", "(.env)"),
        (cfg or {}).get("provider", "env"),
    )
