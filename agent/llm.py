"""Abstracción mínima de LLM para el motor de entrevista.

El motor depende de un callable `complete(prompt) -> str`, no de LangChain
directamente. Así los tests inyectan un fake determinista y el runtime usa
`build_llm` de src.qa_chain (compatible-OpenAI: Groq/Qwen3, AI Gateway, etc.).
"""

from __future__ import annotations

import json
import re
from typing import Protocol


class LLM(Protocol):
    def complete(self, prompt: str) -> str: ...


_ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class LangChainLLM:
    """Adapta un ChatOpenAI de LangChain al protocolo LLM.

    Captura el uso de tokens de cada `invoke` en `last_usage` (graceful: si el
    proveedor no lo reporta, queda en ceros), para que el servicio lo persista.
    """

    def __init__(self, chat_model) -> None:
        self._chat = chat_model
        self.model = getattr(chat_model, "model_name", None) or getattr(chat_model, "model", "")
        self.last_usage: dict[str, int] = dict(_ZERO_USAGE)
        # Metadata de tracing (LangSmith): tenant/conversación/etapa. Si está vacía no
        # se pasa config y el invoke queda idéntico al histórico.
        self.metadata: dict[str, str] = {}

    def complete(self, prompt: str) -> str:
        config = {"metadata": dict(self.metadata)} if self.metadata else None
        resp = self._chat.invoke(prompt, config=config)
        meta = getattr(resp, "usage_metadata", None) or {}
        self.last_usage = {
            "input_tokens": int(meta.get("input_tokens", 0) or 0),
            "output_tokens": int(meta.get("output_tokens", 0) or 0),
            "total_tokens": int(meta.get("total_tokens", 0) or 0),
        }
        content = resp.content
        return content if isinstance(content, str) else str(content)


class MeteredLLM:
    """Envuelve un LLM y acumula el uso de tokens por etapa entre `reset()` y `reset()`.

    Cada call site evalúa con un `stage` (prescreen|classify|evaluate|scorecard|
    revalidate|answer). El servicio lee `drain()` tras procesar el turno y lo persiste.
    Además de tokens acumula `calls`, `errors` (excepciones del proveedor = fallback en
    el caller) y `duration_ms` por etapa (observabilidad del pipeline, auditoría O1).

    Con `trace=True` (O-1) además guarda POR LLAMADA el prompt y la respuesta cruda
    (capados a `trace_max_chars`) en un buffer que el servicio drena con
    `drain_traces()` y persiste en `llm_traces` — replay/debug de evaluaciones.
    """

    def __init__(
        self,
        inner: LLM,
        stage: str = "answer",
        *,
        trace: bool = False,
        trace_max_chars: int = 8000,
        overrides: dict[str, LLM] | None = None,
    ) -> None:
        self._inner = inner
        self.stage = stage
        self.trace = trace
        self.trace_max_chars = trace_max_chars
        # Routing de costos (paso 5): etapa→LLM alternativo (modelo barato). El resto usa `inner`.
        self._overrides = overrides or {}
        # acumulado por stage: {stage: {input/output/total_tokens, calls, errors, duration_ms}}
        self._acc: dict[str, dict[str, int]] = {}
        # modelo REALMENTE usado por etapa (con routing puede diferir de `inner`).
        self._models: dict[str, str] = {}
        # trazas por llamada: [{stage, model, prompt, response, error, duration_ms}]
        self._traces: list[dict] = []

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "") or ""

    def _active(self) -> LLM:
        """El LLM que atiende la etapa actual (barato si está ruteada, si no el principal)."""
        return self._overrides.get(self.stage, self._inner)

    def for_stage(self, stage: str) -> "MeteredLLM":
        self.stage = stage
        return self

    def _bucket(self) -> dict[str, int]:
        return self._acc.setdefault(
            self.stage, {**_ZERO_USAGE, "calls": 0, "errors": 0, "duration_ms": 0}
        )

    def set_context(self, **ctx) -> None:
        """Metadata de tracing (LangSmith): se propaga al LLM principal y a los overrides
        (routing), para que la metadata llegue a cualquier modelo que atienda una etapa."""
        for inner in (self._inner, *self._overrides.values()):
            self._tag_meta(inner, **ctx)

    @staticmethod
    def _tag_meta(inner: LLM, **ctx) -> None:
        meta = getattr(inner, "metadata", None)
        if isinstance(meta, dict):
            meta.update({k: str(v) for k, v in ctx.items() if v})

    def _cap(self, text: str) -> str:
        return text if len(text) <= self.trace_max_chars else text[: self.trace_max_chars]

    def _add_trace(self, model: str, prompt: str, response: str | None, error: str | None, ms: int) -> None:
        if not self.trace:
            return
        self._traces.append({
            "stage": self.stage,
            "model": model,
            "prompt": self._cap(prompt),
            "response": self._cap(response) if response is not None else None,
            "error": error,
            "duration_ms": ms,
        })

    def complete(self, prompt: str) -> str:
        import time

        inner = self._active()
        model = getattr(inner, "model", "") or self.model
        self._models[self.stage] = model
        # Etiqueta la etapa en la metadata de tracing del LLM que atiende (LangSmith).
        self._tag_meta(inner, stage=self.stage)
        t0 = time.perf_counter()
        try:
            out = inner.complete(prompt)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            bucket = self._bucket()
            bucket["calls"] = bucket.get("calls", 0) + 1
            bucket["errors"] = bucket.get("errors", 0) + 1
            bucket["duration_ms"] = bucket.get("duration_ms", 0) + ms
            self._add_trace(model, prompt, None, repr(exc), ms)
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        usage = getattr(inner, "last_usage", None) or _ZERO_USAGE
        bucket = self._bucket()
        for k in ("input_tokens", "output_tokens", "total_tokens"):
            bucket[k] += int(usage.get(k, 0) or 0)
        bucket["calls"] = bucket.get("calls", 0) + 1
        bucket["duration_ms"] = bucket.get("duration_ms", 0) + ms
        self._add_trace(model, prompt, out, None, ms)
        return out

    def drain(self) -> dict[str, dict[str, int]]:
        """Devuelve y limpia el acumulado por stage."""
        acc, self._acc = self._acc, {}
        return acc

    def drain_models(self) -> dict[str, str]:
        """Devuelve y limpia el modelo usado por etapa (routing de costos)."""
        models, self._models = self._models, {}
        return models

    def drain_traces(self) -> list[dict]:
        """Devuelve y limpia las trazas por llamada (vacío si `trace=False`)."""
        traces, self._traces = self._traces, []
        return traces


def complete_staged(llm: LLM, prompt: str, stage: str) -> str:
    """Completa marcando la etapa para el metering (no-op si el LLM no lo soporta)."""
    setter = getattr(llm, "for_stage", None)
    if callable(setter):
        setter(stage)
    return llm.complete(prompt)


def build_default_llm(model: str | None = None) -> LangChainLLM:
    """LLM del runtime (temperatura baja, sin <think> en Qwen3).

    Construye el ChatOpenAI directamente (sin pasar por src.qa_chain) para no
    arrastrar torch/sentence-transformers en el arranque del bot: la entrevista no
    usa RAG. El stack RAG se carga solo si/ cuando se conecte la base de conocimiento.

    `model` permite construir un LLM con OTRO modelo del MISMO proveedor (misma base_url/
    api_key) — lo usa el routing de costos para el modelo barato de las etapas simples.
    """
    from langchain_openai import ChatOpenAI

    from src.config import get_settings

    settings = get_settings()
    model_name = model or settings.openai_model
    kwargs = dict(
        model=model_name,
        base_url=settings.openai_api_base,
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        temperature=0.2,
    )
    if "qwen3" in model_name.lower():
        kwargs["extra_body"] = {"reasoning_effort": "none"}
    return LangChainLLM(ChatOpenAI(**kwargs))


def build_stage_overrides(settings) -> dict[str, LLM]:
    """Mapa etapa→LLM barato para el routing de costos (paso 5), o {} si no aplica.

    Si `llm_cheap_model` está configurado, construye UN LLM barato (mismo proveedor) y lo
    asocia a cada etapa de `llm_cheap_stages`. Se comparte una sola instancia entre las
    etapas ruteadas (mismo modelo). Vacío = sin routing (todo con el modelo principal)."""
    cheap = (getattr(settings, "llm_cheap_model", "") or "").strip()
    if not cheap:
        return {}
    stages = [s.strip() for s in (getattr(settings, "llm_cheap_stages", "") or "").split(",") if s.strip()]
    if not stages:
        return {}
    cheap_llm = build_default_llm(model=cheap)
    return {stage: cheap_llm for stage in stages}


def parse_json_object(raw: str) -> dict:
    """Extrae el primer objeto JSON de una respuesta del LLM (tolerante a markdown).

    Reproduce el patrón robusto de src/classifier.py. Lanza ValueError si no hay
    JSON parseable, para que el llamador decida el fallback.
    """
    match = re.search(r"\{.*\}", raw, re.S)
    payload = match.group(0) if match else raw
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("la respuesta del LLM no es un objeto JSON")
    return data
