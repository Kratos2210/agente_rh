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

    def complete(self, prompt: str) -> str:
        resp = self._chat.invoke(prompt)
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
    """

    def __init__(self, inner: LLM, stage: str = "answer") -> None:
        self._inner = inner
        self.stage = stage
        # acumulado por stage: {stage: {input_tokens, output_tokens, total_tokens}}
        self._acc: dict[str, dict[str, int]] = {}

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "") or ""

    def for_stage(self, stage: str) -> "MeteredLLM":
        self.stage = stage
        return self

    def complete(self, prompt: str) -> str:
        out = self._inner.complete(prompt)
        usage = getattr(self._inner, "last_usage", None) or _ZERO_USAGE
        bucket = self._acc.setdefault(self.stage, dict(_ZERO_USAGE))
        for k in ("input_tokens", "output_tokens", "total_tokens"):
            bucket[k] += int(usage.get(k, 0) or 0)
        return out

    def drain(self) -> dict[str, dict[str, int]]:
        """Devuelve y limpia el acumulado por stage."""
        acc, self._acc = self._acc, {}
        return acc


def complete_staged(llm: LLM, prompt: str, stage: str) -> str:
    """Completa marcando la etapa para el metering (no-op si el LLM no lo soporta)."""
    setter = getattr(llm, "for_stage", None)
    if callable(setter):
        setter(stage)
    return llm.complete(prompt)


def build_default_llm() -> LangChainLLM:
    """LLM por defecto del runtime (temperatura baja, sin <think> en Qwen3).

    Construye el ChatOpenAI directamente (sin pasar por src.qa_chain) para no
    arrastrar torch/sentence-transformers en el arranque del bot: la entrevista no
    usa RAG. El stack RAG se carga solo si/ cuando se conecte la base de conocimiento.
    """
    from langchain_openai import ChatOpenAI

    from src.config import get_settings

    settings = get_settings()
    kwargs = dict(
        model=settings.openai_model,
        base_url=settings.openai_api_base,
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        temperature=0.2,
    )
    if "qwen3" in settings.openai_model.lower():
        kwargs["extra_body"] = {"reasoning_effort": "none"}
    return LangChainLLM(ChatOpenAI(**kwargs))


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
