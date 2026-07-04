"""F5 (auditoría de integraciones) — endurecimiento de secretos + rotación de JWT.

Cubre lo que hasta ahora no tenía tests:
  - `assert_secure_config`: bloquea secretos por defecto/débiles en producción (jwt,
    jwt de rotación, admin_password) y NO bloquea en desarrollo.
  - Rotación grácil de JWT: un token firmado con un secreto retirado sigue siendo válido
    mientras ese secreto esté en `jwt_secret_previous`; deja de serlo cuando se quita.

Todo con `Settings` construido explícitamente (sin DB, sin .env real).
"""

from __future__ import annotations

import api.auth as auth
import jwt
import pytest
from core.config import Settings

_STRONG = "S" * 40  # secreto fuerte (>= 32 bytes, no default)


def _settings(**overrides) -> Settings:
    """Settings de prueba con secretos fuertes por defecto (overridea lo que se indique)."""
    base = {
        "environment": "production",
        "jwt_secret": _STRONG,
        "admin_password": "una-clave-fuerte-123",
    }
    base.update(overrides)
    return Settings(**base)


# ── assert_secure_config ───────────────────────────────────────────────────────

def test_prod_ok_with_strong_secrets():
    auth.assert_secure_config(_settings())  # no lanza


def test_prod_rejects_default_jwt_secret():
    default_jwt = Settings.model_fields["jwt_secret"].default
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.assert_secure_config(_settings(jwt_secret=default_jwt))


def test_prod_rejects_short_jwt_secret():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.assert_secure_config(_settings(jwt_secret="corto"))


def test_prod_rejects_default_admin_password():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        auth.assert_secure_config(_settings(admin_password="admin1234"))


def test_prod_rejects_short_admin_password():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        auth.assert_secure_config(_settings(admin_password="corta"))


def test_prod_rejects_weak_rotation_secret():
    """Un secreto de rotación débil también valida tokens → debe rechazarse."""
    with pytest.raises(RuntimeError, match="JWT_SECRET_PREVIOUS"):
        auth.assert_secure_config(_settings(jwt_secret_previous="corto"))


def test_prod_accepts_strong_rotation_secret():
    auth.assert_secure_config(_settings(jwt_secret_previous="P" * 40))  # no lanza


def test_dev_only_warns(caplog):
    """En desarrollo, secretos débiles advierten pero NO bloquean el arranque."""
    s = _settings(environment="development", jwt_secret="corto", admin_password="admin1234")
    auth.assert_secure_config(s)  # no lanza


# ── Rotación grácil del secreto JWT ─────────────────────────────────────────────

def test_token_from_current_secret_decodes():
    s = _settings()
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role="admin", tenant_id="t1", settings=s
    )
    claims = auth.decode_access_token(tok, s)
    assert claims["sub"] == "u1" and claims["tenant_id"] == "t1"


def test_token_from_retired_secret_still_valid_during_window():
    """Se firma con el secreto viejo; tras rotar, sigue validando porque está en PREVIOUS."""
    old = "O" * 40
    signed_old = _settings(jwt_secret=old)
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role="recruiter", tenant_id="t1", settings=signed_old
    )
    # Rotamos: el actual es _STRONG y el viejo pasa a PREVIOUS → el token viejo sigue vivo.
    rotated = _settings(jwt_secret=_STRONG, jwt_secret_previous=old)
    assert auth.decode_access_token(tok, rotated)["sub"] == "u1"


def test_token_from_unknown_secret_is_rejected():
    """Cerrada la ventana (secreto viejo fuera de PREVIOUS), el token deja de validar."""
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role="viewer", tenant_id="t1",
        settings=_settings(jwt_secret="O" * 40),
    )
    with pytest.raises(jwt.PyJWTError):
        auth.decode_access_token(tok, _settings(jwt_secret=_STRONG))  # sin PREVIOUS


def test_accepted_secrets_dedup_and_order():
    s = _settings(jwt_secret=_STRONG, jwt_secret_previous=f" {_STRONG} , X40 , X40 ")
    assert auth.accepted_jwt_secrets(s) == [_STRONG, "X40"]  # actual primero, sin duplicar
