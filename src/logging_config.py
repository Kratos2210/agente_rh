"""Logging estructurado y centralizado para el backend.

Antes el código usaba `print` sueltos (vectorstore) y varios `except` mudos, lo
que hacía imposible depurar en producción (Docker/servidor). Esto da un único
punto de configuración: formato consistente, nivel desde la variable de entorno
`LOG_LEVEL` (default INFO) y un helper `get_logger(name)` que el resto del código
usa sin reconfigurar nada.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False

_FORMAT = "%(levelname)s | %(asctime)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """Configura el root logger una sola vez (idempotente).

    Llamar en el arranque de la app (lifespan). El nivel sale de `LOG_LEVEL`
    (DEBUG/INFO/WARNING/ERROR); cualquier valor inválido cae a INFO.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger nombrado, asegurando que el logging esté configurado."""
    setup_logging()
    return logging.getLogger(name)
