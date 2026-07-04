"""Roadmap v2, paso 2 — lock distribuido por conversación (auditoria_v2 · Riesgo 3).

Cubre los helpers del lock combinado de `InterviewService` sin DB real:
  - `_advisory_key`: determinista y dentro del rango bigint con signo de Postgres.
  - sin `database_url` → cae al lock local (yield, sin tocar pool).
  - con un pool falso → toma/libera el advisory lock con la key correcta.
  - fallo al adquirir → degrada al lock local (yield, sin excepción).
"""

from __future__ import annotations

from agent.service import InterviewService, _advisory_key

_BIGINT_MIN = -(2**63)
_BIGINT_MAX = 2**63 - 1


def _service(**kw) -> InterviewService:
    return InterviewService(runner=object(), **kw)  # runner no lo usan los helpers del lock


class _FakeConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self


class _FakePool:
    def __init__(self, conn=None, fail_getconn=False):
        self.conn = conn or _FakeConn()
        self.fail_getconn = fail_getconn
        self.returned: list = []

    def getconn(self, timeout=None):
        if self.fail_getconn:
            raise RuntimeError("pool agotado")
        return self.conn

    def putconn(self, conn):
        self.returned.append(conn)


def test_advisory_key_deterministic_and_bigint():
    k1 = _advisory_key("telegram:123")
    k2 = _advisory_key("telegram:123")
    k3 = _advisory_key("telegram:999")
    assert k1 == k2
    assert k1 != k3
    for k in (k1, k3):
        assert _BIGINT_MIN <= k <= _BIGINT_MAX


def test_no_database_url_falls_back_to_local():
    svc = _service()  # sin database_url
    assert svc._get_lock_pool() is None
    entered = False
    with svc._conversation_lock("telegram:1"):
        entered = True
    assert entered  # cedió una vez, sin pool


def test_advisory_lock_acquired_and_released_with_fake_pool():
    svc = _service(database_url="postgres://x")
    conn = _FakeConn()
    svc._lock_pool = _FakePool(conn)  # inyecta pool falso (evita abrir uno real)
    key = _advisory_key("telegram:7")
    with svc._conversation_lock("telegram:7"):
        # dentro del bloque ya se tomó el lock
        assert ("select pg_advisory_lock(%s)", (key,)) in conn.calls
    # al salir se liberó y se devolvió la conexión al pool
    assert ("select pg_advisory_unlock(%s)", (key,)) in conn.calls
    assert svc._lock_pool.returned == [conn]


def test_acquire_failure_degrades_to_local():
    svc = _service(database_url="postgres://x")
    svc._lock_pool = _FakePool(fail_getconn=True)
    entered = False
    with svc._conversation_lock("telegram:9"):  # no debe lanzar
        entered = True
    assert entered
