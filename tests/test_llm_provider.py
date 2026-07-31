"""Proveedor LLM por-tenant (BYOK + hot-swap): cifrado de la key, resolución con caché
TTL, fingerprint, reconfigure del MeteredLLM y endpoints /api/settings/llm-provider
(GET enmascarado, PUT cifrado + siembra de precios, /test, /catalog, RBAC)."""

from __future__ import annotations

import api.auth as auth
import api.main as main
import api.routes.settings as settings_routes
from fastapi.testclient import TestClient
from core.config import get_settings
from orquestacion import providers
from orquestacion.llm import MeteredLLM

client = TestClient(main.app)


def _auth(role: str, tenant_id: str = "t1") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id=tenant_id, settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


class _FakeInner:
    def __init__(self, name: str):
        self.model = name
        self.last_usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        self.metadata: dict[str, str] = {}
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return f"{self.model}:ok"


def _cfg(**over) -> dict:
    base = {
        "provider": "groq", "base_url": "https://api.groq.com/openai/v1",
        "api_key": "gsk_secret_key_123456", "model": "qwen/qwen3-32b",
        "cheap_model": "", "cheap_stages": "",
    }
    base.update(over)
    return base


# ── Cifrado y enmascarado ─────────────────────────────────────────────────────

def test_encrypt_decrypt_roundtrip():
    token = providers.encrypt_api_key("gsk_secret_key_123456")
    assert token != "gsk_secret_key_123456"
    assert providers.decrypt_api_key(token) == "gsk_secret_key_123456"


def test_decrypt_fails_open_with_other_secret(monkeypatch):
    token = providers.encrypt_api_key("gsk_secret_key_123456")
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    rotated = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"otro-secreto|llm-provider").digest()))
    monkeypatch.setattr(providers, "_fernet", lambda: rotated)
    assert providers.decrypt_api_key(token) is None  # rotó el secreto → fail-open al .env


def test_mask_api_key():
    assert providers.mask_api_key("gsk_secret_key_123456") == "gsk_...3456"
    assert providers.mask_api_key("corta") == "••••"
    assert providers.mask_api_key("") == ""


# ── Fingerprint ───────────────────────────────────────────────────────────────

def test_fingerprint_env_and_sensitivity():
    assert providers.config_fingerprint(None) == "env"
    base = providers.config_fingerprint(_cfg())
    assert base == providers.config_fingerprint(_cfg())  # estable
    for field, value in [
        ("model", "otro"), ("base_url", "https://x/v1"), ("api_key", "gsk_otra"),
        ("cheap_model", "mini"), ("provider", "gemini"),
    ]:
        assert providers.config_fingerprint(_cfg(**{field: value})) != base
    assert "gsk_secret" not in base  # nunca la key en claro


# ── resolve_llm_config: caché TTL + fail-open ─────────────────────────────────

def _stored(enabled=True, **over) -> dict:
    row = {
        "enabled": enabled, "provider": "groq", "base_url": "",
        "api_key_encrypted": providers.encrypt_api_key("gsk_secret_key_123456"),
        "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": "schedule",
    }
    row.update(over)
    return row


def test_resolve_llm_config_reads_and_caches(monkeypatch):
    providers.invalidate_config_cache()
    calls = []

    def _get(key, default=None, tenant_id=None):
        calls.append((key, tenant_id))
        return _stored()

    import db.repositories as repo

    monkeypatch.setattr(repo, "get_app_setting", _get)
    cfg = providers.resolve_llm_config("tA")
    assert cfg["model"] == "qwen/qwen3-32b"
    assert cfg["api_key"] == "gsk_secret_key_123456"
    assert cfg["base_url"] == "https://api.groq.com/openai/v1"  # autocompletada del preset
    providers.resolve_llm_config("tA")  # segunda lectura: cacheada
    assert calls == [("llm_provider", "tA")]
    providers.invalidate_config_cache()


def test_resolve_llm_config_none_paths(monkeypatch):
    import db.repositories as repo

    providers.invalidate_config_cache()
    assert providers.resolve_llm_config(None) is None
    monkeypatch.setattr(repo, "get_app_setting", lambda *a, **k: _stored(enabled=False))
    assert providers.resolve_llm_config("t-off") is None

    def _boom(*a, **k):
        raise RuntimeError("db caída")

    monkeypatch.setattr(repo, "get_app_setting", _boom)
    assert providers.resolve_llm_config("t-err") is None  # fail-open al .env
    providers.invalidate_config_cache()


# ── MeteredLLM.reconfigure + refresh_metered_llm ──────────────────────────────

def test_reconfigure_preserves_turn_accumulator_and_swaps_active():
    a, b = _FakeInner("model-a"), _FakeInner("model-b")
    m = MeteredLLM(a)
    m.for_stage("evaluate").complete("x")
    m.reconfigure(b, {}, "fp-b")
    m.for_stage("classify").complete("y")
    acc = m.drain()
    assert set(acc) == {"evaluate", "classify"}  # el acumulado previo NO se perdió
    models = m.drain_models()
    assert models == {"evaluate": "model-a", "classify": "model-b"}
    assert m.config_fingerprint == "fp-b"


def test_refresh_noop_without_reconfigure_or_same_fingerprint(monkeypatch):
    providers.invalidate_config_cache()

    class _Plain:  # FakeLLM de otros tests: sin reconfigure
        def complete(self, p):
            return "ok"

    providers.refresh_metered_llm(_Plain(), "t1")  # no revienta

    m = MeteredLLM(_FakeInner("model-a"))
    monkeypatch.setattr(providers, "resolve_llm_config", lambda tid: None)
    inner_before = m._inner
    providers.refresh_metered_llm(m, "t1")  # fingerprint "env" == "env" → no-op
    assert m._inner is inner_before


def test_refresh_swaps_on_config_change(monkeypatch):
    m = MeteredLLM(_FakeInner("env-model"))
    m._inner.metadata["conversation_id"] = "c1"
    built = []

    def _build_pair(cfg, settings):
        inner = _FakeInner(cfg["model"] if cfg else "env-model")
        built.append(inner.model)
        return inner, {}

    monkeypatch.setattr(providers, "_build_pair", _build_pair)
    monkeypatch.setattr(providers, "resolve_llm_config", lambda tid: _cfg(model="model-nuevo"))
    providers.refresh_metered_llm(m, "t1")
    assert built == ["model-nuevo"]
    assert m._inner.model == "model-nuevo"
    assert m._inner.metadata["conversation_id"] == "c1"  # metadata de tracing preservada
    m.for_stage("evaluate").complete("x")
    assert m.drain_models()["evaluate"] == "model-nuevo"


def test_build_overrides_from_config_maps_stages(monkeypatch):
    monkeypatch.setattr(providers, "build_llm_from_config", lambda cfg, model=None: _FakeInner(model))
    out = providers.build_overrides_from_config(_cfg(cheap_model="mini", cheap_stages="classify, schedule"))
    assert set(out) == {"classify", "schedule"}
    assert out["classify"] is out["schedule"]  # instancia compartida
    assert providers.build_overrides_from_config(_cfg(cheap_model="")) == {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

def _patch_store(monkeypatch) -> dict:
    store: dict = {}
    monkeypatch.setattr(
        main.repo, "get_app_setting",
        lambda key, default=None, tenant_id=None: store.get((tenant_id, key), default),
    )
    monkeypatch.setattr(
        main.repo, "set_app_setting",
        lambda key, value, tenant_id=None: store.__setitem__((tenant_id, key), value),
    )
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)
    # Limiter fresco por test: el módulo-level de /test acumularía llamadas entre tests.
    monkeypatch.setattr(
        settings_routes, "_llm_test_limiter",
        settings_routes.SlidingWindowLimiter(max_calls=5, per_seconds=60),
    )
    providers.invalidate_config_cache()
    return store


def test_provider_endpoints_rbac_and_masked_default(monkeypatch):
    _patch_store(monkeypatch)
    assert client.get("/api/settings/llm-provider").status_code == 401
    body = {"enabled": False, "provider": "groq", "base_url": "", "api_key": "",
            "model": "", "cheap_model": "", "cheap_stages": "schedule"}
    assert client.put("/api/settings/llm-provider", json=body, headers=_auth("recruiter")).status_code == 403
    # El GET también es admin-only: expone base_url/modelo/key enmascarada.
    assert client.get("/api/settings/llm-provider", headers=_auth("viewer")).status_code == 403
    r = client.get("/api/settings/llm-provider", headers=_auth("admin"))
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False and data["api_key_masked"] == ""
    assert "api_key_encrypted" not in data


def test_provider_put_encrypts_and_seeds_pricing(monkeypatch):
    store = _patch_store(monkeypatch)
    body = {"enabled": True, "provider": "groq", "base_url": "",
            "api_key": "gsk_secret_key_123456", "model": "qwen/qwen3.6-27b",
            "cheap_model": "llama-3.1-8b-instant", "cheap_stages": "classify,schedule"}
    r = client.put("/api/settings/llm-provider", json=body, headers=_auth("admin", "T_A"))
    assert r.status_code == 200
    out = r.json()
    assert out["api_key_masked"] == "gsk_...3456"
    assert "api_key_encrypted" not in out
    saved = store[("T_A", "llm_provider")]
    assert "gsk_secret_key_123456" not in str(saved)  # nunca en claro en la DB
    assert providers.decrypt_api_key(saved["api_key_encrypted"]) == "gsk_secret_key_123456"
    assert saved["base_url"] == "https://api.groq.com/openai/v1"  # autocompletada
    # Siembra de precios: model + cheap_model quedan mapeados en llm_pricing.
    pricing = store[("T_A", "llm_pricing")]
    assert pricing["models"]["qwen/qwen3.6-27b"]["input_per_1m"] == 0.60
    assert pricing["models"]["llama-3.1-8b-instant"]["output_per_1m"] == 0.08


def test_provider_put_seed_does_not_overwrite_existing_price(monkeypatch):
    store = _patch_store(monkeypatch)
    store[("T_A", "llm_pricing")] = {
        "models": {"qwen/qwen3-32b": {"input_per_1m": 9.9, "output_per_1m": 9.9}},
        "default": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    }
    body = {"enabled": True, "provider": "groq", "base_url": "",
            "api_key": "gsk_x_1234567890", "model": "qwen/qwen3-32b",
            "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=body, headers=_auth("admin", "T_A")).status_code == 200
    assert store[("T_A", "llm_pricing")]["models"]["qwen/qwen3-32b"]["input_per_1m"] == 9.9


def test_provider_put_empty_key_keeps_previous(monkeypatch):
    store = _patch_store(monkeypatch)
    enc = providers.encrypt_api_key("gsk_secret_key_123456")
    store[("t1", "llm_provider")] = _stored(api_key_encrypted=enc)
    # Mismo proveedor/endpoint (base_url "" resuelve a la del catálogo) → conserva la key.
    body = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "",
            "model": "llama-3.3-70b-versatile", "cheap_model": "", "cheap_stages": ""}
    r = client.put("/api/settings/llm-provider", json=body, headers=_auth("admin"))
    assert r.status_code == 200
    assert store[("t1", "llm_provider")]["api_key_encrypted"] == enc
    assert r.json()["api_key_masked"] == "gsk_...3456"


def test_provider_put_endpoint_change_requires_new_key(monkeypatch):
    """Anti-exfiltración: cambiar proveedor o base_url conservando la key (api_key:"")
    mandaría el Bearer almacenado a un destino nuevo → 422 exige re-ingresarla."""
    store = _patch_store(monkeypatch)
    enc = providers.encrypt_api_key("gsk_secret_key_123456")
    store[("t1", "llm_provider")] = _stored(api_key_encrypted=enc)
    other_provider = {"enabled": True, "provider": "gemini", "base_url": "", "api_key": "",
                      "model": "gemini-2.5-flash", "cheap_model": "", "cheap_stages": ""}
    r = client.put("/api/settings/llm-provider", json=other_provider, headers=_auth("admin"))
    assert r.status_code == 422
    other_url = {"enabled": True, "provider": "groq", "base_url": "https://atacante.example/v1",
                 "api_key": "", "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=other_url, headers=_auth("admin")).status_code == 422
    assert store[("t1", "llm_provider")]["api_key_encrypted"] == enc  # nada cambió
    # Con key nueva el cambio de proveedor procede normalmente.
    with_key = {**other_provider, "api_key": "AIza_nueva_key_9876"}
    assert client.put("/api/settings/llm-provider", json=with_key, headers=_auth("admin")).status_code == 200


def test_provider_put_validations(monkeypatch):
    _patch_store(monkeypatch)
    bad_provider = {"enabled": False, "provider": "no-existe", "base_url": "", "api_key": "",
                    "model": "", "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=bad_provider, headers=_auth("admin")).status_code == 422
    no_url = {"enabled": True, "provider": "custom", "base_url": "", "api_key": "k-123456789",
              "model": "m", "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=no_url, headers=_auth("admin")).status_code == 422
    no_key = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "",
              "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=no_key, headers=_auth("admin")).status_code == 422
    no_model = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "k-123456789",
                "model": "", "cheap_model": "", "cheap_stages": ""}
    assert client.put("/api/settings/llm-provider", json=no_model, headers=_auth("admin")).status_code == 422


def test_provider_catalog_endpoint(monkeypatch):
    _patch_store(monkeypatch)
    r = client.get("/api/settings/llm-provider/catalog", headers=_auth("viewer"))
    assert r.status_code == 200
    provs = r.json()["providers"]
    assert {"groq", "gemini", "nvidia", "openai", "openrouter", "together", "custom"} <= set(provs)
    assert provs["gemini"]["base_url"].startswith("https://generativelanguage")


def test_provider_test_endpoint_ok_and_error(monkeypatch):
    _patch_store(monkeypatch)
    monkeypatch.setattr(settings_routes, "_build_test_llm", lambda cfg: _FakeInner(cfg["model"]))
    body = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "gsk_secret_key_123456",
            "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": ""}
    r = client.post("/api/settings/llm-provider/test", json=body, headers=_auth("admin"))
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True and out["latency_ms"] >= 0 and out["model"] == "qwen/qwen3-32b"

    class _Boom:
        def complete(self, p):
            raise RuntimeError("401 invalid api key gsk_secret_key_123456")

    monkeypatch.setattr(settings_routes, "_build_test_llm", lambda cfg: _Boom())
    r2 = client.post("/api/settings/llm-provider/test", json=body, headers=_auth("admin"))
    out2 = r2.json()
    assert out2["ok"] is False
    assert "gsk_secret_key_123456" not in out2["error"]  # la key nunca se filtra

    # Sin key (ni nueva ni almacenada) → 422.
    no_key = {**body, "api_key": ""}
    assert client.post("/api/settings/llm-provider/test", json=no_key, headers=_auth("admin")).status_code == 422


def test_provider_test_stored_key_only_against_same_endpoint(monkeypatch):
    """Anti-exfiltración en /test: la key almacenada no viaja a un endpoint distinto."""
    store = _patch_store(monkeypatch)
    monkeypatch.setattr(settings_routes, "_build_test_llm", lambda cfg: _FakeInner(cfg["model"]))
    store[("t1", "llm_provider")] = _stored()
    same = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "",
            "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": ""}
    assert client.post("/api/settings/llm-provider/test", json=same, headers=_auth("admin")).status_code == 200
    other_url = {**same, "base_url": "https://atacante.example/v1"}
    assert client.post("/api/settings/llm-provider/test", json=other_url, headers=_auth("admin")).status_code == 422
    other_provider = {**same, "provider": "gemini", "model": "gemini-2.5-flash"}
    assert client.post("/api/settings/llm-provider/test", json=other_provider, headers=_auth("admin")).status_code == 422


def test_provider_test_rate_limited_per_tenant(monkeypatch):
    _patch_store(monkeypatch)
    monkeypatch.setattr(settings_routes, "_build_test_llm", lambda cfg: _FakeInner(cfg["model"]))
    body = {"enabled": True, "provider": "groq", "base_url": "", "api_key": "gsk_k_123456789",
            "model": "qwen/qwen3-32b", "cheap_model": "", "cheap_stages": ""}
    for _ in range(5):
        assert client.post("/api/settings/llm-provider/test", json=body, headers=_auth("admin")).status_code == 200
    assert client.post("/api/settings/llm-provider/test", json=body, headers=_auth("admin")).status_code == 429
    # Otro tenant tiene su propia ventana.
    assert client.post("/api/settings/llm-provider/test", json=body,
                       headers=_auth("admin", "t2")).status_code == 200


# ── Anti-SSRF: endpoint público en producción ─────────────────────────────────

class _FakeSettings:
    def __init__(self, production: bool, allow_private: bool = False):
        self.is_production = production
        self.allow_private_llm_endpoints = allow_private


def test_is_private_host():
    assert providers.is_private_host("127.0.0.1") is True
    assert providers.is_private_host("localhost") is True
    assert providers.is_private_host("10.0.0.5") is True
    assert providers.is_private_host("169.254.169.254") is True  # metadata cloud
    assert providers.is_private_host("8.8.8.8") is False
    assert providers.is_private_host("host-inexistente-xyz.invalid") is True  # no resoluble = conservador


def test_assert_public_llm_endpoint_gates_only_production():
    # Dev: todo pasa (Ollama local).
    providers.assert_public_llm_endpoint("http://localhost:11434/v1", _FakeSettings(False))
    # Producción: privado/loopback → ValueError; público pasa; flag lo abre.
    import pytest

    with pytest.raises(ValueError):
        providers.assert_public_llm_endpoint("http://localhost:11434/v1", _FakeSettings(True))
    with pytest.raises(ValueError):
        providers.assert_public_llm_endpoint("http://169.254.169.254/latest", _FakeSettings(True))
    with pytest.raises(ValueError):
        providers.assert_public_llm_endpoint("", _FakeSettings(True))
    providers.assert_public_llm_endpoint("https://8.8.8.8/v1", _FakeSettings(True))
    providers.assert_public_llm_endpoint("http://localhost:11434/v1", _FakeSettings(True, allow_private=True))
