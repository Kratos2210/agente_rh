"""Audit F1 — el token del bot de Telegram no debe filtrarse en logs ni en la DB.

Dos capas de defensa:
  1. `setup_logging` sube httpx/httpcore a WARNING → no loguean la URL (con token) en INFO.
  2. `post_telegram` re-lanza un error saneado: las excepciones de httpx llevan la URL —y el
     token— en su mensaje, y ese texto acabaría en `logger.exception` y en `outbox.last_error`.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from notifications import candidate
from src.config import Settings
from src.logging_config import setup_logging

TOKEN = "8203186985:AAGGaFAKEsecretTOKENvalue_1234567890"
S = Settings(telegram_bot_token=TOKEN)


def test_setup_logging_silences_httpx():
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_redact_token_scrubs_url():
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    out = candidate.redact_token(f"Client error '400' for url '{url}'")
    assert TOKEN not in out
    assert "bot<REDACTED>" in out


def test_post_telegram_error_does_not_leak_token(monkeypatch):
    """Un fallo HTTP se re-lanza sin el token en el mensaje ni en la causa."""

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        response = httpx.Response(400, request=request)
        return response

    monkeypatch.setattr(candidate.httpx, "post", fake_post)

    with pytest.raises(RuntimeError) as exc:
        candidate.post_telegram(S, "123", "hola")

    err = exc.value
    assert TOKEN not in str(err)
    # `from None` corta la cadena de la causa y marca __suppress_context__, así que la
    # excepción original (con el token en su URL) NO se renderiza en el traceback.
    assert err.__cause__ is None
    assert err.__suppress_context__ is True


def test_send_text_swallows_and_does_not_leak(monkeypatch, caplog):
    """send_text no lanza y lo que loguea no contiene el token."""

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(400, request=request)

    monkeypatch.setattr(candidate.httpx, "post", fake_post)

    with caplog.at_level(logging.DEBUG):
        ok = candidate.send_text(S, "telegram", "123", "hola")

    assert ok is False
    assert TOKEN not in caplog.text
