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

from core.config import Settings, get_settings
from core.logging_config import get_logger

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


def accepted_jwt_secrets(settings: Settings) -> list[str]:
    """Secretos aceptados al VALIDAR un JWT: el actual + los retirados (rotación grácil, F5).

    Se firma siempre con `jwt_secret` (el primero); los de `jwt_secret_previous` (CSV) solo
    se aceptan para no invalidar los tokens vivos durante la ventana de rotación."""
    secrets = [settings.jwt_secret]
    for s in (settings.jwt_secret_previous or "").split(","):
        s = s.strip()
        if s and s not in secrets:
            secrets.append(s)
    return secrets


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Valida firma + expiración y devuelve los claims. Lanza jwt.PyJWTError si es inválido.

    Prueba el secreto actual y luego los retirados (`jwt_secret_previous`) para soportar la
    rotación sin cerrar sesiones. La expiración es definitiva (no depende del secreto): si un
    secreto valida la firma pero el token expiró, se propaga sin seguir probando."""
    last_err: jwt.PyJWTError = jwt.InvalidTokenError("token inválido")
    for secret in accepted_jwt_secrets(settings):
        try:
            return jwt.decode(token, secret, algorithms=[_ALGO])
        except jwt.ExpiredSignatureError:
            raise  # firma válida pero expiró: no tiene sentido probar otros secretos
        except jwt.PyJWTError as err:
            last_err = err
            continue
    raise last_err


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
#: Longitud mínima aceptable para la contraseña del admin inicial en producción.
MIN_ADMIN_PASSWORD_LEN = 12


def assert_secure_config(settings: Settings) -> None:
    """Rechaza secretos por defecto/débiles al arrancar en producción (audit P0 · F5).

    En producción (`ENVIRONMENT=production`) LANZA `RuntimeError` si:
      - `jwt_secret` es el default o mide <`MIN_JWT_SECRET_LEN`;
      - algún secreto RETIRADO de `jwt_secret_previous` es el default o es demasiado corto
        (un secreto de rotación débil también valida tokens → mismo riesgo);
      - `admin_password` es el default (`admin1234`) o mide <`MIN_ADMIN_PASSWORD_LEN`.
    En desarrollo solo advierte (no bloquea el arranque)."""
    default_jwt = Settings.model_fields["jwt_secret"].default
    default_pwd = Settings.model_fields["admin_password"].default
    problems: list[str] = []
    if settings.jwt_secret == default_jwt or len(settings.jwt_secret) < MIN_JWT_SECRET_LEN:
        problems.append(
            f"JWT_SECRET inseguro (usa el default o mide <{MIN_JWT_SECRET_LEN} bytes); "
            "define uno fuerte en .env"
        )
    for prev in (settings.jwt_secret_previous or "").split(","):
        prev = prev.strip()
        if prev and (prev == default_jwt or len(prev) < MIN_JWT_SECRET_LEN):
            problems.append(
                "JWT_SECRET_PREVIOUS contiene un secreto de rotación inseguro "
                f"(default o <{MIN_JWT_SECRET_LEN} bytes); quítalo o rótalo"
            )
            break
    if settings.admin_password == default_pwd:
        problems.append("ADMIN_PASSWORD sigue siendo el default (admin1234); cámbialo en .env")
    elif len(settings.admin_password) < MIN_ADMIN_PASSWORD_LEN:
        problems.append(
            f"ADMIN_PASSWORD es demasiado corta (<{MIN_ADMIN_PASSWORD_LEN} caracteres); usa una fuerte"
        )
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


# ── Revocación de sesión (auditoría S2) ──────────────────────────────────────────
#
# El JWT vive `jwt_expire_minutes` (12 h por defecto) y el logout solo borra el
# localStorage: sin este chequeo, desactivar un usuario NO cortaba su sesión viva.
# `get_current_user` consulta `users.active` con un caché TTL corto (una lectura
# por usuario cada `_REVOCATION_TTL_SECONDS`, no por request). Política:
#   - la DB confirma active=False → 401 (revocado);
#   - la DB no responde o el usuario no existe (tokens de test/bootstrap) → se
#     acepta el token hasta su expiración (fail-open: una caída de la DB no debe
#     dejar a todo el dashboard afuera; el flujo de revocación es DESACTIVAR).

_REVOCATION_TTL_SECONDS = 60
# {user_id: (revocado, expira_epoch)} — proceso-local, igual que el rate limiter.
_revocation_cache: dict[str, tuple[bool, float]] = {}


def _is_user_revoked(user_id: str) -> bool:
    """True solo si la DB confirma que el usuario existe y está inactivo (con caché TTL)."""
    import time

    if not user_id:
        return False
    cached = _revocation_cache.get(user_id)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    revoked = False
    try:
        from db import repositories as repo

        row = repo.get_user(user_id)
        revoked = bool(row) and not row.get("active", True)
    except Exception:  # noqa: BLE001 — sin DB no hay veredicto: el token sigue valiendo
        revoked = False
    _revocation_cache[user_id] = (revoked, now + _REVOCATION_TTL_SECONDS)
    return revoked


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
    if _is_user_revoked(str(claims.get("sub") or "")):
        raise HTTPException(401, "La sesión fue revocada")
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
