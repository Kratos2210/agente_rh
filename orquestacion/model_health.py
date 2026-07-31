"""Salud del modelo LLM: detección de modelos retirados por el proveedor.

Motivación (incidente 2026-07-18): Groq retiró `qwen/qwen3-32b` sin aviso. Cada llamada
pasó a responder 404 `model_not_found` y el pipeline **degradó en silencio** a sus
fallbacks (score neutro + `low_confidence`, clasificador heurístico). El diseño fail-safe
funcionó — nadie perdió una entrevista — pero la degradación solo se supo 24 h después,
cuando el nightly de calidad falló. En producción eso son 24 h de scorecards neutros.

Dos capas, deliberadamente distintas:

  1. **Arranque** (`check_configured_models`): consulta el catálogo `/models` del proveedor
     y avisa si el modelo configurado no figura. Feedback inmediato al desplegar; no cubre
     al proveedor que retira el modelo con el proceso ya corriendo (el caso real).
  2. **Runtime** (`note_exception` desde `MeteredLLM.complete`): marca el modelo en cuanto
     el proveedor responde "no existe". Es la capa que habría cazado el incidente en
     minutos. Cubre también los modelos BYOK por-tenant, que el arranque no conoce.

Ambas escriben en un registro de proceso que `_collect_ops_alerts` publica como alerta
`model_unavailable` → aparece en `/observabilidad` y, con `sla_alerts` activo, se empuja
por correo. Una llamada exitosa con el mismo modelo limpia la marca (auto-recuperación:
un 404 transitorio o un modelo que vuelve no dejan la alerta pegada).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

# Firmas de "el modelo no existe" en proveedores compatible-OpenAI. Deliberadamente NO
# incluye un 404 pelado: un 404 de ruta mal armada no es un modelo retirado.
_NOT_FOUND_MARKERS = (
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "model not found",
    "is not a valid model",
    "has been deprecated",
    "has been decommissioned",
)

# {modelo: {"since": iso8601, "detail": str, "source": "startup"|"runtime"}}
_unavailable: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def is_model_not_found(exc: BaseException | str) -> bool:
    """¿La excepción del proveedor dice que el modelo no existe? (puro, testeable)."""
    text = (exc if isinstance(exc, str) else repr(exc)).lower()
    return any(marker in text for marker in _NOT_FOUND_MARKERS)


def mark_unavailable(model: str, detail: str, source: str = "runtime") -> None:
    """Registra que `model` no está disponible. Idempotente: conserva el `since` original
    para que la alerta muestre desde cuándo dura la degradación."""
    model = (model or "").strip()
    if not model:
        return
    with _lock:
        if model in _unavailable:
            return
        _unavailable[model] = {
            "since": datetime.now(timezone.utc).isoformat(),
            "detail": detail[:500],
            "source": source,
        }
    logger.error(
        "Modelo LLM no disponible en el proveedor: %s (%s) — el pipeline está degradando "
        "a heurísticas. Revisar OPENAI_MODEL / el proveedor del tenant.", model, source,
    )


def mark_available(model: str) -> None:
    """Una llamada exitosa limpia la marca (auto-recuperación)."""
    model = (model or "").strip()
    if not model:
        return
    with _lock:
        if _unavailable.pop(model, None) is not None:
            logger.info("Modelo LLM %s volvió a responder: alerta levantada.", model)


def note_exception(model: str, exc: BaseException) -> None:
    """Hook del camino de error de `MeteredLLM.complete`: marca solo si la excepción es
    'modelo inexistente' (un timeout o un 429 no son deprecación)."""
    if is_model_not_found(exc):
        mark_unavailable(model, repr(exc), source="runtime")


def unavailable_models() -> dict[str, dict[str, Any]]:
    """Copia del registro (para las alertas operativas)."""
    with _lock:
        return {k: dict(v) for k, v in _unavailable.items()}


def reset() -> None:
    """Limpia el registro (tests)."""
    with _lock:
        _unavailable.clear()


def fetch_available_models(base_url: str, api_key: str, timeout: float = 10.0) -> set[str] | None:
    """Catálogo `/models` del proveedor compatible-OpenAI, o None si no se pudo consultar.

    None ≠ vacío: un proveedor sin ese endpoint (o una red caída al arrancar) devuelve
    None y NO genera alerta — no queremos alertar por no poder verificar."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        import httpx

        resp = httpx.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )
        resp.raise_for_status()
        ids = {str(m.get("id")) for m in (resp.json() or {}).get("data", []) if m.get("id")}
        return ids or None
    except Exception:  # noqa: BLE001 — verificar es best-effort, jamás rompe el arranque
        logger.warning("No se pudo consultar el catálogo de modelos de %s (se sigue sin verificar).", base)
        return None


def check_configured_models(settings) -> dict[str, Any]:
    """Verificación de arranque de los modelos del `.env` (principal + barato del routing).

    No cubre los modelos BYOK por-tenant (viven en `app_settings.llm_provider`, con su
    propia key): esos los caza la capa de runtime en la primera llamada."""
    models = [
        m for m in (
            getattr(settings, "openai_model", "") or "",
            getattr(settings, "llm_cheap_model", "") or "",
        ) if m.strip()
    ]
    if not models:
        return {"checked": False, "missing": []}
    available = fetch_available_models(
        getattr(settings, "openai_api_base", ""), getattr(settings, "openai_api_key", ""),
    )
    if available is None:
        return {"checked": False, "missing": []}
    missing = sorted({m for m in models if m not in available})
    for m in missing:
        mark_unavailable(m, "No figura en el catálogo del proveedor al arrancar.", source="startup")
    if not missing:
        for m in models:
            mark_available(m)
    return {"checked": True, "missing": missing, "available_count": len(available)}
