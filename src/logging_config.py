"""Logging estructurado y centralizado para el backend.

Antes el código usaba `print` sueltos (vectorstore) y varios `except` mudos, lo
que hacía imposible depurar en producción (Docker/servidor). Esto da un único
punto de configuración: formato consistente, nivel desde la variable de entorno
`LOG_LEVEL` (default INFO) y un helper `get_logger(name)` que el resto del código
usa sin reconfigurar nada.

O-6: con `LOG_JSON=true` cada línea sale como JSON (ts/level/logger/message/
request_id) — apto para agregadores (Loki, CloudWatch, Datadog). El `request_id`
viaja en un contextvar que setea el middleware de la API por request (header
`X-Request-ID`, propagado o generado); fuera de un request queda "-".
"""

from __future__ import annotations

import contextvars
import json
import logging
import os

_CONFIGURED = False

_FORMAT = "%(levelname)s | %(asctime)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Request-id del request HTTP en curso (lo setea el middleware; "-" fuera de un request).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(rid: str) -> None:
    request_id_var.set(rid or "-")


def get_request_id() -> str:
    return request_id_var.get()


class _RequestIdFilter(logging.Filter):
    """Inyecta `record.request_id` desde el contextvar (para formatters/handlers)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento: ts, level, logger, message, request_id (+ excepción)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _json_mode() -> bool:
    return os.getenv("LOG_JSON", "").strip().lower() in {"1", "true", "yes"}


def setup_logging() -> None:
    """Configura el root logger una sola vez (idempotente).

    Llamar en el arranque de la app (lifespan). El nivel sale de `LOG_LEVEL`
    (DEBUG/INFO/WARNING/ERROR); cualquier valor inválido cae a INFO. Con
    `LOG_JSON=true` el handler emite JSON estructurado (O-6).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(_RequestIdFilter())
        if _json_mode():
            handler.setFormatter(JsonFormatter())
    # Seguridad (audit F1): httpx/httpcore loguean cada request en INFO, incluida la
    # URL. Las llamadas a la API de Telegram llevan el token del bot EN la URL
    # (api.telegram.org/bot<TOKEN>/...), así que a nivel INFO el token quedaba escrito
    # en los logs. Se sube su nivel a WARNING para no filtrar la credencial.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger nombrado, asegurando que el logging esté configurado."""
    setup_logging()
    return logging.getLogger(name)
