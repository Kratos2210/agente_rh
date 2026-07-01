from __future__ import annotations

import os

from .config import Settings


def setup_tracing(settings: Settings) -> bool:
    """
    Activa el tracing de LangSmith si está habilitado en la configuración.

    LangChain detecta el tracing por variables de entorno. Esta función las exporta
    a partir de la config (que vino del .env) y devuelve True si quedó activo.

    Para usarlo: poné LANGSMITH_TRACING=true y tu LANGSMITH_API_KEY en el .env.
    """
    enabled = str(settings.langsmith_tracing).strip().lower() in {"1", "true", "yes"}

    if enabled and settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"  # compat con versiones previas
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        return True

    # Aseguramos que quede desactivado si no hay key.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    return False
