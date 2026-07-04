"""Gestión de usuarios del dashboard (admin-only, aislada por tenant).

Habilita el 2.º operador (roadmap v2 · paso 4 — operación a prueba de ausencias):
un admin puede crear un usuario de solo-lectura (viewer) con acceso a
`/observabilidad`, listarlos y activarlos/desactivarlos. La desactivación corta
la sesión viva vía el chequeo de revocación (auditoría S2). La contraseña se guarda
como hash bcrypt y NUNCA se devuelve.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import ROLES, get_current_user, hash_password, require_role
from api.deps import _audit
from db import repositories as repo

router = APIRouter()

MIN_PASSWORD_LEN = 8


class UserIn(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "viewer"


class UserPatch(BaseModel):
    active: Optional[bool] = None
    role: Optional[str] = None
    name: Optional[str] = None
    password: Optional[str] = None


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Vista segura de un usuario: sin el hash de la contraseña."""
    return {k: v for k, v in row.items() if k != "password_hash"}


@router.get("/api/users")
def list_users(user: dict[str, Any] = Depends(require_role("admin"))) -> list[dict[str, Any]]:
    """Usuarios del tenant del admin (sin hash de contraseña)."""
    return [_public_user(u) for u in repo.list_users(tenant_id=user["tenant_id"])]


@router.post("/api/users", status_code=201)
def create_user(
    payload: UserIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Crea un usuario en el tenant del admin. El email es único (global)."""
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(422, "Email inválido")
    if payload.role not in ROLES:
        raise HTTPException(422, f"Rol inválido; usa uno de {ROLES}")
    if len(payload.password) < MIN_PASSWORD_LEN:
        raise HTTPException(422, f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")
    if repo.get_user_by_email(email):
        raise HTTPException(409, "Ya existe un usuario con ese email")
    created = repo.create_user(
        {
            "tenant_id": user["tenant_id"],
            "email": email,
            "password_hash": hash_password(payload.password),
            "name": payload.name,
            "role": payload.role,
        }
    )
    _audit(user, "user.create", entity_type="user", entity_id=created["id"], summary=f"{email} ({payload.role})")
    return _public_user(created)


@router.patch("/api/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserPatch,
    user: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Activa/desactiva, cambia rol/nombre o resetea la contraseña de un usuario del tenant.

    Guarda contra auto-bloqueo: un admin no puede desactivarse ni quitarse el rol admin a
    sí mismo (evita quedarse sin acceso administrativo)."""
    target = repo.get_user(user_id)
    if not target or target.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Usuario no encontrado")

    fields: dict[str, Any] = {}
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(422, f"Rol inválido; usa uno de {ROLES}")
        fields["role"] = payload.role
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.active is not None:
        fields["active"] = payload.active
    if payload.password is not None:
        if len(payload.password) < MIN_PASSWORD_LEN:
            raise HTTPException(422, f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")
        fields["password_hash"] = hash_password(payload.password)

    # Anti auto-bloqueo: no permitir que el admin actual se desactive o se degrade.
    if str(user_id) == str(user["id"]):
        if fields.get("active") is False:
            raise HTTPException(400, "No puedes desactivar tu propio usuario")
        if "role" in fields and fields["role"] != "admin":
            raise HTTPException(400, "No puedes quitarte el rol admin a ti mismo")

    if not fields:
        raise HTTPException(422, "Nada que actualizar")

    updated = repo.update_user(user_id, fields)
    changed = ",".join(k for k in fields if k != "password_hash") or "password"
    _audit(user, "user.update", entity_type="user", entity_id=user_id, summary=f"{target.get('email','')} [{changed}]")
    return _public_user(updated)
