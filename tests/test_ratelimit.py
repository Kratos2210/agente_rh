"""Rate limiting (auditoría R1/R2/R3) + observabilidad del LLM (O1).

  - SlidingWindowLimiter / TurnGovernor: puros, con reloj/fecha inyectados.
  - Login: 429 tras 5 intentos/minuto por IP.
  - Psych-exam: 409 al reenviar las MISMAS credenciales (idempotencia).
  - MeteredLLM: acumula calls/errors/duration_ms por etapa (fallbacks visibles).
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

import api.auth as auth
import api.main as main
from api.ratelimit import (
    TURN_BLOCKED,
    TURN_CAP_NOTICE,
    TURN_COOLDOWN,
    TURN_OK,
    SlidingWindowLimiter,
    TurnGovernor,
)
from core.config import get_settings

client = TestClient(main.app)


# ── SlidingWindowLimiter (puro) ────────────────────────────────────────────────────

def test_limiter_blocks_after_max_and_recovers():
    lim = SlidingWindowLimiter(max_calls=3, per_seconds=60)
    assert all(lim.allow("ip1", now=t) for t in (0.0, 1.0, 2.0))
    assert lim.allow("ip1", now=3.0) is False          # 4ª dentro de la ventana
    assert lim.allow("ip2", now=3.0) is True           # otra clave no se ve afectada
    assert lim.allow("ip1", now=61.5) is True          # la ventana ya venció


# ── TurnGovernor (puro) ─────────────────────────────────────────────────────────────

def test_governor_cooldown_between_messages():
    gov = TurnGovernor(cooldown_seconds=2.0, max_turns_per_day=100)
    d = date(2026, 7, 1)
    assert gov.check("c1", now=0.0, today=d) == TURN_OK
    assert gov.check("c1", now=0.5, today=d) == TURN_COOLDOWN   # ráfaga: ignorar
    assert gov.check("c1", now=2.5, today=d) == TURN_OK


def test_governor_daily_cap_notice_once_then_silence():
    gov = TurnGovernor(cooldown_seconds=0.0, max_turns_per_day=2)
    d = date(2026, 7, 1)
    assert gov.check("c1", now=0.0, today=d) == TURN_OK
    assert gov.check("c1", now=1.0, today=d) == TURN_OK
    assert gov.check("c1", now=2.0, today=d) == TURN_CAP_NOTICE  # tope: aviso ÚNICO
    assert gov.check("c1", now=3.0, today=d) == TURN_BLOCKED     # después, silencio
    # Día nuevo: el contador se reinicia.
    assert gov.check("c1", now=4.0, today=date(2026, 7, 2)) == TURN_OK


# ── Login: 5 intentos/minuto por IP (R1) ───────────────────────────────────────────

def test_login_rate_limited_after_five_attempts(monkeypatch):
    main._login_limiter.reset()
    monkeypatch.setattr(main, "authenticate", lambda e, p: None)  # siempre credencial mala
    body = {"email": "a@b.com", "password": "x"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=body).status_code == 401
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 429
    main._login_limiter.reset()  # no contaminar otros tests


# ── Psych-exam: reenvío idéntico → 409 (R3) ─────────────────────────────────────────

def _auth_header(role: str = "recruiter") -> dict[str, str]:
    tok = auth.create_access_token(
        user_id="u1", email="a@b.com", role=role, tenant_id="t1", settings=get_settings()
    )
    return {"Authorization": f"Bearer {tok}"}


def test_psych_exam_resend_same_credentials_is_409(monkeypatch):
    exam = {"link": "https://test.com", "code": "C1", "key": "K1"}
    cand = {"id": "cand1", "vacancy_id": "v1", "name": "Luis", "psych_exam": exam}
    monkeypatch.setattr(main.repo, "get_candidate", lambda cid: cand)
    monkeypatch.setattr(main.repo, "get_vacancy", lambda vid: {"id": "v1", "tenant_id": "t1"})
    r = client.post("/api/candidates/cand1/psych-exam", json=exam, headers=_auth_header())
    assert r.status_code == 409

    # Credenciales NUEVAS sí se permiten (reemplazo legítimo).
    sent = {}
    monkeypatch.setattr(main.outbox, "deliver_psych_exam", lambda *a, **k: sent.update(ok=True) or True)
    monkeypatch.setattr(main.repo, "update_candidate", lambda cid, p: p)
    monkeypatch.setattr(main.repo, "add_audit_log", lambda row: row)
    r = client.post(
        "/api/candidates/cand1/psych-exam",
        json={**exam, "code": "C2"},
        headers=_auth_header(),
    )
    assert r.status_code == 200 and sent.get("ok")


# ── MeteredLLM: latencia + errores por etapa (O1) ───────────────────────────────────

class _OkLLM:
    last_usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    def complete(self, prompt: str) -> str:
        return "ok"


class _BoomLLM:
    def complete(self, prompt: str) -> str:
        raise RuntimeError("proveedor caído")


def test_metered_llm_tracks_calls_and_duration():
    from orquestacion.llm import MeteredLLM

    llm = MeteredLLM(_OkLLM(), stage="evaluate")
    llm.complete("p1")
    llm.complete("p2")
    acc = llm.drain()
    b = acc["evaluate"]
    assert b["calls"] == 2 and b["errors"] == 0
    assert b["total_tokens"] == 30 and b["duration_ms"] >= 0


def test_metered_llm_counts_errors_and_reraises():
    import pytest

    from orquestacion.llm import MeteredLLM

    llm = MeteredLLM(_BoomLLM(), stage="classify")
    with pytest.raises(RuntimeError):
        llm.complete("p")
    b = llm.drain()["classify"]
    assert b["calls"] == 1 and b["errors"] == 1
    assert b["total_tokens"] == 0  # sin tokens, pero el fallo queda registrado
