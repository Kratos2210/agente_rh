"""Cliente Supabase + helpers de conexión Postgres.

El cliente (service role) se usa para toda la persistencia de negocio (vacantes,
candidatos, conversaciones, respuestas, scorecards). La connection string de
Postgres se usa por separado para el checkpointer durable de LangGraph.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from core.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Cliente Supabase con la service-role key (cacheado por proceso)."""
    settings: Settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY en el .env. "
            "Completalos antes de usar la capa de datos."
        )
    return create_client(settings.supabase_url, settings.supabase_service_key)


def get_database_url() -> str:
    """Connection string Postgres para el checkpointer de LangGraph."""
    settings: Settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "Falta DATABASE_URL en el .env (Supabase → Database → Connection string)."
        )
    return settings.database_url
