"""Detección de modelos retirados por el proveedor (incidente 2026-07-18).

Cubre las dos capas: la marca en runtime desde el camino de error de MeteredLLM y la
verificación de arranque contra el catálogo `/models`, más la salida como alerta operativa.
"""

from __future__ import annotations

import pytest

from orquestacion import model_health as mh
from orquestacion.llm import MeteredLLM


@pytest.fixture(autouse=True)
def _clean_registry():
    mh.reset()
    yield
    mh.reset()


# ── clasificación de la excepción (pura) ──────────────────────────────────────

GROQ_404 = (
    "Error code: 404 - {'error': {'message': 'The model `qwen/qwen3-32b` does not exist "
    "or you do not have access to it.', 'type': 'invalid_request_error', "
    "'code': 'model_not_found'}}"
)


@pytest.mark.parametrize("text", [
    GROQ_404,
    "NotFoundError: The model `gpt-foo` does not exist",
    "openai.NotFoundError: no such model: bar",
    "This model has been deprecated",
    "model_not_found",
])
def test_detecta_modelo_inexistente(text):
    assert mh.is_model_not_found(Exception(text)) is True


@pytest.mark.parametrize("text", [
    "Request timed out",
    "Error code: 429 - rate limit exceeded, please retry",
    "Error code: 500 - internal server error",
    "Connection reset by peer",
    "Error code: 401 - invalid api key",
])
def test_no_marca_errores_transitorios(text):
    """Un timeout, un 429 o un 401 NO son deprecación: marcarlos daría alertas falsas."""
    assert mh.is_model_not_found(Exception(text)) is False


# ── registro ──────────────────────────────────────────────────────────────────

def test_marca_conserva_el_since_original():
    mh.mark_unavailable("m1", "primera", source="runtime")
    first = mh.unavailable_models()["m1"]["since"]
    mh.mark_unavailable("m1", "segunda", source="startup")
    assert mh.unavailable_models()["m1"]["since"] == first
    assert mh.unavailable_models()["m1"]["detail"] == "primera"


def test_llamada_exitosa_levanta_la_marca():
    mh.mark_unavailable("m1", "404")
    assert "m1" in mh.unavailable_models()
    mh.mark_available("m1")
    assert mh.unavailable_models() == {}


def test_modelo_vacio_no_ensucia_el_registro():
    mh.mark_unavailable("", "sin modelo")
    assert mh.unavailable_models() == {}


# ── capa runtime: MeteredLLM ──────────────────────────────────────────────────

class _BoomLLM:
    model = "qwen/qwen3-32b"

    def __init__(self, exc):
        self._exc = exc

    def complete(self, prompt):
        raise self._exc


class _OkLLM:
    model = "qwen/qwen3-32b"
    last_usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

    def complete(self, prompt):
        return "ok"


def test_metered_marca_el_modelo_ante_404_del_proveedor():
    llm = MeteredLLM(_BoomLLM(RuntimeError(GROQ_404)))
    with pytest.raises(RuntimeError):
        llm.for_stage("evaluate").complete("hola")
    assert "qwen/qwen3-32b" in mh.unavailable_models()
    assert mh.unavailable_models()["qwen/qwen3-32b"]["source"] == "runtime"


def test_metered_no_marca_ante_error_transitorio():
    llm = MeteredLLM(_BoomLLM(RuntimeError("Error code: 429 - rate limit")))
    with pytest.raises(RuntimeError):
        llm.for_stage("evaluate").complete("hola")
    assert mh.unavailable_models() == {}
    # el metering del error sigue intacto
    assert llm.drain()["evaluate"]["errors"] == 1


def test_metered_autorecupera_al_volver_a_responder():
    mh.mark_unavailable("qwen/qwen3-32b", "404 previo")
    MeteredLLM(_OkLLM()).for_stage("evaluate").complete("hola")
    assert mh.unavailable_models() == {}


# ── capa arranque: catálogo /models ───────────────────────────────────────────

class _S:
    openai_api_base = "https://api.groq.com/openai/v1"
    openai_api_key = "k"
    openai_model = "qwen/qwen3.6-27b"
    llm_cheap_model = "llama-3.1-8b-instant"


def test_arranque_marca_lo_que_no_esta_en_el_catalogo(monkeypatch):
    monkeypatch.setattr(mh, "fetch_available_models", lambda *a, **k: {"llama-3.1-8b-instant"})
    out = mh.check_configured_models(_S())
    assert out["checked"] is True
    assert out["missing"] == ["qwen/qwen3.6-27b"]
    assert mh.unavailable_models()["qwen/qwen3.6-27b"]["source"] == "startup"


def test_arranque_sin_catalogo_no_alerta(monkeypatch):
    """Proveedor sin /models o red caída: no poder verificar NO es motivo de alerta."""
    monkeypatch.setattr(mh, "fetch_available_models", lambda *a, **k: None)
    out = mh.check_configured_models(_S())
    assert out["checked"] is False
    assert mh.unavailable_models() == {}


def test_arranque_todo_ok_limpia_marcas_previas(monkeypatch):
    mh.mark_unavailable("qwen/qwen3.6-27b", "marca vieja")
    monkeypatch.setattr(
        mh, "fetch_available_models", lambda *a, **k: {"qwen/qwen3.6-27b", "llama-3.1-8b-instant"},
    )
    assert mh.check_configured_models(_S())["missing"] == []
    assert mh.unavailable_models() == {}


def test_fetch_devuelve_none_si_el_proveedor_falla(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "get", boom)
    assert mh.fetch_available_models("https://x/v1", "k") is None


# ── salida: alerta operativa ──────────────────────────────────────────────────

def test_sale_como_alerta_operativa(monkeypatch):
    from api import scheduler

    mh.mark_unavailable("qwen/qwen3-32b", GROQ_404, source="runtime")
    monkeypatch.setattr(scheduler.repo, "count_outbox_by_status", lambda *a, **k: {})
    monkeypatch.setattr(scheduler.repo, "list_meetings_without_link", lambda *a, **k: [])
    monkeypatch.setattr(scheduler.repo, "list_conversations_by_states", lambda *a, **k: [])

    alerts = scheduler._collect_ops_alerts()
    model_alerts = [a for a in alerts if a["type"] == "model_unavailable"]
    assert len(model_alerts) == 1
    assert model_alerts[0]["model"] == "qwen/qwen3-32b"
    assert "heurísticas" in model_alerts[0]["detail"]


def test_health_expone_la_degradacion_del_llm():
    """El health debe delatar el modelo caído (mismo criterio que `scheduler_degraded`):
    ops/k8s se enteran sin esperar al nightly de calidad."""
    from api.main import health  # se invoca directo: el lifespan no hace falta aquí

    assert health()["llm_degraded"] is False
    mh.mark_unavailable("qwen/qwen3-32b", "404")
    out = health()
    assert out["llm_degraded"] is True
    assert out["llm_models_unavailable"] == ["qwen/qwen3-32b"]
