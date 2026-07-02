"""Estado compartido del proceso + helpers de tiempo + defaults de configuración.

Módulo hoja (no importa nada de `api/*`): lo consumen el ensamblaje (`api/main.py`),
el scheduler (`api/scheduler.py`) y los routers (`api/routes/*`) sin ciclos.

`_state` es el diccionario global del proceso (settings, event loop, refs al bot y
al servicio de entrevista). Lo llena el lifespan en `api/main.py`; el bot y el
scheduler lo comparten por referencia.
"""

from __future__ import annotations

from typing import Any

from src.config import Settings, get_settings

# Estado global del proceso (settings, loop, refs al bot). Se llena en el lifespan.
_state: dict[str, Any] = {}


def current_settings() -> Settings:
    """Settings vigentes del proceso (las del lifespan, o frescas si aún no arrancó)."""
    return _state.get("settings") or get_settings()


def init_sentry(settings: Settings) -> bool:
    """Error tracking config-gated (O-6): inicializa Sentry solo si hay `SENTRY_DSN`.

    Devuelve True si quedó activo. Best-effort: un DSN inválido o el SDK ausente
    no deben impedir el arranque (queda el logging normal)."""
    if not str(settings.sentry_dsn or "").strip():
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=float(settings.sentry_traces_sample_rate or 0.0),
            # PII fuera de los eventos (Ley 29733): Sentry no debe recibir cuerpos
            # de requests ni datos del candidato, solo stacktraces.
            send_default_pii=False,
        )
        return True
    except Exception:  # noqa: BLE001
        from src.logging_config import get_logger

        get_logger("api.runtime").exception("Sentry no pudo inicializarse (se sigue sin él)")
        return False


def _now_iso() -> str:
    """Timestamp UTC ISO (usado al registrar el envío del examen psicológico)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _now_local(tzname: str):
    """Hora actual en la zona dada; cae a UTC-5 (Lima, sin DST) si la zona no resuelve."""
    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tzname))
    except Exception:  # noqa: BLE001 — sin tzdata, Lima es fijo UTC-5
        return datetime.now(timezone(timedelta(hours=-5)))


def _parse_dt(value: Any):
    """Parsea un timestamptz de Supabase a datetime con tz (cae a 'ahora' si no resuelve)."""
    from datetime import datetime, timezone

    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


# ── Defaults de app_settings (por-tenant; un tenant sin fila cae a estos) ─────────

_DEFAULT_SCHEDULING = {
    "enabled": True, "provider": "simulated", "slot_minutes": 45,
    "work_days": [1, 2, 3, 4, 5], "work_start": "09:00", "work_end": "18:00",
    "work_windows": [["09:00", "18:00"]],
    "timezone": "America/Lima", "horizon_days": 7, "options": 3,
}

_DEFAULT_AUTO_CONTACT = {"enabled": False, "times": ["11:00", "15:00"], "timezone": "America/Lima"}

# Inactividad: recordar a los N minutos de silencio y cerrar tras M recordatorios.
_DEFAULT_INACTIVITY = {"enabled": True, "reminder_minutes": 2, "max_reminders": 2}

# Retención de datos (Ley 29733): anonimiza la PII de candidatos descartados con más de N días.
_DEFAULT_RETENTION = {"enabled": False, "days": 180}

# Costos LLM (O-2): precio por millón de tokens POR MODELO (cada tenant configura los
# suyos desde el dashboard). "default" aplica a modelos sin fila propia. Todo en 0 =
# sin costo estimado (cae al escalar legado `token_price_per_1k` si está seteado).
_DEFAULT_LLM_PRICING = {"models": {}, "default": {"input_per_1m": 0.0, "output_per_1m": 0.0}}

# Presupuesto LLM mensual (O-2): al alcanzar `alert_pct`% del monto, alerta una vez por
# tenant/mes (ops alert en el dashboard + correo vía outbox si hay `notify_email`).
_DEFAULT_LLM_BUDGET = {"enabled": False, "monthly_usd": 0.0, "alert_pct": 80, "notify_email": ""}

# SLAs push (O-4): correo al incumplirse una condición, UNA vez por condición/día.
# `ops_alerts` empuja las alertas operativas (dead-letter, reuniones sin link, etc.);
# `turn_p95_ms` es el umbral de latencia p95 del turno del candidato (últimas 24 h, 0 = off).
_DEFAULT_SLA_ALERTS = {"enabled": False, "notify_email": "", "ops_alerts": True, "turn_p95_ms": 0}
# Estados terminales-descartados cuya PII se anonimiza tras el período de retención.
_RETENTION_STATUSES = ["rejected", "declined", "no_response", "prescreen_rejected", "no_show"]
