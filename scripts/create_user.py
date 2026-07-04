"""Crea un usuario del dashboard desde la CLI (roadmap v2, paso 4 — 2.º operador).

El endpoint `POST /api/users` cubre el alta desde el dashboard; este script es el
camino sin UI (bootstrap de un operador de solo-lectura, entorno nuevo, o rotación de
credenciales). La contraseña se guarda como hash bcrypt; nunca en claro.

    uv run python scripts/create_user.py --email ops@empresa.com --role viewer
    uv run python scripts/create_user.py --email ops@empresa.com --role viewer --password 's3creta!' --tenant default
    uv run python scripts/create_user.py --email jefe@empresa.com --role admin --name "Jefa RR.HH."

Si se omite --password se pide por consola (sin eco). --tenant es el slug del tenant
(default: 'default'). Requiere el bloque Supabase del .env. Idempotente por email:
si ya existe, no lo duplica (email es único global).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import get_logger  # noqa: E402

logger = get_logger("scripts.create_user")

MIN_PASSWORD_LEN = 8
ROLES = ("admin", "recruiter", "viewer")


def create(email: str, role: str, name: str, password: str, tenant_slug: str) -> int:
    from api.auth import hash_password
    from db import repositories as repo

    email = email.strip().lower()
    if "@" not in email:
        logger.error("Email inválido: %s", email)
        return 2
    if role not in ROLES:
        logger.error("Rol inválido '%s'; usa uno de %s", role, ROLES)
        return 2
    if len(password) < MIN_PASSWORD_LEN:
        logger.error("La contraseña debe tener al menos %d caracteres", MIN_PASSWORD_LEN)
        return 2
    if repo.get_user_by_email(email):
        logger.error("Ya existe un usuario con el email %s (email único global)", email)
        return 3

    tenant = repo.get_tenant_by_slug(tenant_slug)
    if not tenant:
        logger.error("No existe el tenant con slug '%s' (crea el tenant o usa 'default')", tenant_slug)
        return 4

    user = repo.create_user(
        {
            "tenant_id": tenant["id"],
            "email": email,
            "password_hash": hash_password(password),
            "name": name,
            "role": role,
        }
    )
    logger.info("Usuario creado: %s · rol=%s · tenant=%s · id=%s", email, role, tenant_slug, user["id"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea un usuario del dashboard")
    parser.add_argument("--email", required=True, help="Email (login)")
    parser.add_argument("--role", default="viewer", choices=ROLES, help="Rol (default: viewer)")
    parser.add_argument("--name", default="", help="Nombre para mostrar")
    parser.add_argument("--password", default="", help="Contraseña (si se omite, se pide por consola)")
    parser.add_argument("--tenant", default="default", help="Slug del tenant (default: default)")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    password = args.password or getpass.getpass("Contraseña del nuevo usuario: ")
    return create(args.email, args.role, args.name, password, args.tenant)


if __name__ == "__main__":
    raise SystemExit(main())
