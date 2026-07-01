"""Autenticación y autorización del dashboard (Fase 0 — cimientos SaaS).

Auth self-contained, sin infraestructura extra: JWT (PyJWT) firmado con
`settings.jwt_secret` + hash **bcrypt** de contraseñas. Los usuarios viven en la
tabla `users` (email + rol + tenant). Cada request del dashboard lleva
`Authorization: Bearer <token>`; las dependencias de FastAPI resuelven el usuario,
su rol y su **tenant**, y aíslan los datos por empresa.

Roles (jerárquicos): viewer < recruiter < admin.
  - viewer:    solo lectura.
  - recruiter: lectura + acciones operativas (contactar, decidir, sincronizar, vacantes).
  - admin:     todo + roster de reclutadores, configuración y usuarios.

Las funciones puras (hash/verify, encode/decode del token, jerarquía de roles) no
tocan la base de datos: son unitariamente testeables.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import Settings, get_settings
from src.logging_config import get_logger

logger = get_logger("api.auth")

ROLES = ("admin", "recruiter", "viewer")
# Jerarquía: un rol de rango mayor incluye los permisos de los menores.
_ROLE_RANK = {"viewer": 0, "recruiter": 1, "admin": 2}
_ALGO = "HS256"


# ── Contraseñas (bcrypt) ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash bcrypt (con salt) de una contraseña en claro."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """True si la contraseña coincide con el hash. No lanza ante hashes corruptos."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── JWT ──────────────────────────────────────────────────────────────────────────

def create_access_token(
    *,
    user_id: str,
    email: str,
    role: str,
    tenant_id: str,
    settings: Settings,
    now: Optional[datetime] = None,
) -> str:
    """Firma un JWT con la identidad + rol + tenant y su expiración."""
    now = now or datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Valida firma + expiración y devuelve los claims. Lanza jwt.PyJWTError si es inválido."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])


# ── RBAC (jerarquía de roles) — pura ──────────────────────────────────────────────

def role_allows(user_role: str, required_role: str) -> bool:
    """True si `user_role` cumple (>=) el rol requerido según la jerarquía."""
    return _ROLE_RANK.get(user_role, -1) >= _ROLE_RANK.get(required_role, 99)


# ── Autenticación contra la base de datos ─────────────────────────────────────────

def authenticate(email: str, password: str) -> Optional[dict[str, Any]]:
    """Devuelve el usuario si email+contraseña son válidos y está activo; si no, None."""
    from db import repositories as repo

    user = repo.get_user_by_email(email)
    if not user or not user.get("active", True):
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


#: Longitud mínima aceptable para el secreto JWT en producción (32 bytes).
MIN_JWT_SECRET_LEN = 32


def assert_secure_config(settings: Settings) -> None:
    """Rechaza secretos por defecto/débiles al arrancar en producción (audit P0).

    En producción (`ENVIRONMENT=production`) LANZA `RuntimeError` si el `jwt_secret` sigue
    siendo el default o es más corto que `MIN_JWT_SECRET_LEN`, o si `admin_password` sigue
    siendo el default (`admin1234`). En desarrollo solo advierte (no bloquea el arranque)."""
    default_jwt = Settings.model_fields["jwt_secret"].default
    default_pwd = Settings.model_fields["admin_password"].default
    problems: list[str] = []
    if settings.jwt_secret == default_jwt or len(settings.jwt_secret) < MIN_JWT_SECRET_LEN:
        problems.append(
            f"JWT_SECRET inseguro (usa el default o mide <{MIN_JWT_SECRET_LEN} bytes); "
            "define uno fuerte en .env"
        )
    if settings.admin_password == default_pwd:
        problems.append("ADMIN_PASSWORD sigue siendo el default (admin1234); cámbialo en .env")
    if not problems:
        return
    if settings.is_production:
        raise RuntimeError(
            "Configuración insegura en producción: " + " | ".join(problems)
        )
    for p in problems:
        logger.warning("Config insegura (dev, no bloquea): %s", p)


def ensure_default_admin(settings: Settings) -> None:
    """Crea el admin inicial (del .env) si la tabla users está vacía. Idempotente."""
    from db import repositories as repo

    if repo.count_users() > 0:
        return
    tenant = repo.get_tenant_by_slug("default") or repo.create_tenant("Empresa demo", "default")
    repo.create_user(
        {
            "tenant_id": tenant["id"],
            "email": settings.admin_email,
            "password_hash": hash_password(settings.admin_password),
            "name": settings.admin_name,
            "role": "admin",
        }
    )


# ── Dependencias de FastAPI ───────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Resuelve el usuario autenticado desde el Bearer token. 401 si falta o es inválido."""
    if creds is None or not creds.credentials:
        raise HTTPException(401, "No autenticado")
    try:
        claims = decode_access_token(creds.credentials, settings)
    except jwt.PyJWTError:
        raise HTTPException(401, "Token inválido o expirado")
    if not claims.get("tenant_id"):
        raise HTTPException(401, "Token sin tenant")
    return {
        "id": claims.get("sub"),
        "email": claims.get("email"),
        "role": claims.get("role"),
        "tenant_id": claims.get("tenant_id"),
    }


def require_role(required: str) -> Callable[..., dict[str, Any]]:
    """Dependencia que exige un rol mínimo (jerárquico). 403 si el usuario no llega."""

    def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if not role_allows(user.get("role", ""), required):
            raise HTTPException(403, "No tienes permisos para esta acción")
        return user

    return _dep
