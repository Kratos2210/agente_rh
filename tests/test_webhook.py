"""Webhook de Telegram (roadmap paso 3): selección de modo, validación del secret y feed.

Helpers puros (sin infra) + la ruta POST /telegram/webhook con TestClient SIN lifespan
(así _state se controla a mano y no arranca bot/scheduler/DB).
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from api import main
from api import telegram_bot as tb
from src.config import Settings

client = TestClient(main.app)


def _settings(**kw) -> Settings:
    base = dict(telegram_bot_token="123:ABC", telegram_webhook_url="", telegram_webhook_secret="")
    base.update(kw)
    return Settings(**base)


# ── Helpers puros ─────────────────────────────────────────────────────────────

def test_webhook_enabled_requires_token_and_url():
    assert tb.webhook_enabled(_settings(telegram_webhook_url="https://api.x.com")) is True
    assert tb.webhook_enabled(_settings(telegram_webhook_url="")) is False
    assert tb.webhook_enabled(_settings(telegram_bot_token="", telegram_webhook_url="https://x")) is False


def test_webhook_url_joins_without_double_slash():
    assert tb.webhook_url(_settings(telegram_webhook_url="https://api.x.com/")) == "https://api.x.com/telegram/webhook"
    assert tb.webhook_url(_settings(telegram_webhook_url="https://api.x.com")) == "https://api.x.com/telegram/webhook"


def test_resolve_secret_explicit_wins_else_derived_and_stable():
    assert tb.resolve_webhook_secret(_settings(telegram_webhook_secret="mysecret")) == "mysecret"
    s = _settings(telegram_webhook_secret="")
    derived = tb.resolve_webhook_secret(s)
    assert derived and derived == tb.resolve_webhook_secret(s)  # determinístico
    # secretos distintos para tokens distintos
    assert derived != tb.resolve_webhook_secret(_settings(telegram_bot_token="999:ZZZ"))


def test_secret_matches_constant_time_logic():
    s = _settings(telegram_webhook_secret="s3cr3t")
    assert tb.secret_matches(s, "s3cr3t") is True
    assert tb.secret_matches(s, "wrong") is False
    assert tb.secret_matches(s, None) is False
    assert tb.secret_matches(s, "") is False


# ── process_webhook_update: encola el update deserializado ────────────────────

def test_process_webhook_update_enqueues():
    class _FakeApp:
        def __init__(self):
            self.bot = object()
            self.update_queue = asyncio.Queue()

    app = _FakeApp()
    data = {"update_id": 42, "message": {"message_id": 1, "date": 0,
            "chat": {"id": 5, "type": "private"}, "text": "hola"}}
    asyncio.run(tb.process_webhook_update(app, data))
    update = app.update_queue.get_nowait()
    assert update.update_id == 42
    assert update.message.text == "hola"


# ── Ruta HTTP ─────────────────────────────────────────────────────────────────

def _install_webhook_state(mode="webhook", secret="s3cr3t"):
    """Prepara main._state como si el lifespan hubiera arrancado en modo webhook."""
    q = asyncio.Queue()

    class _FakeApp:
        bot = object()
        update_queue = q

    main._state.clear()
    main._state["settings"] = _settings(
        telegram_webhook_url="https://api.x.com", telegram_webhook_secret=secret
    )
    main._state["tg_app"] = _FakeApp()
    main._state["telegram_mode"] = mode
    return q


def teardown_function():
    main._state.clear()


def test_webhook_route_404_when_not_webhook_mode():
    _install_webhook_state(mode="polling")
    r = client.post("/telegram/webhook", json={"update_id": 1})
    assert r.status_code == 404


def test_webhook_route_403_on_bad_secret():
    _install_webhook_state(secret="right")
    r = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 403


def test_webhook_route_403_without_secret_header():
    _install_webhook_state(secret="right")
    r = client.post("/telegram/webhook", json={"update_id": 1})
    assert r.status_code == 403


def test_webhook_route_200_feeds_queue_on_valid_secret():
    q = _install_webhook_state(secret="right")
    payload = {"update_id": 7, "message": {"message_id": 1, "date": 0,
               "chat": {"id": 5, "type": "private"}, "text": "hi"}}
    r = client.post(
        "/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "right"},
    )
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    update = q.get_nowait()
    assert update.update_id == 7
