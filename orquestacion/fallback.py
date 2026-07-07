"""Proveedor LLM de respaldo con circuit breaker (auditoría v4, R3).

`FallbackLLM` envuelve al LLM principal (protocolo `complete(prompt) -> str`): si el
principal falla, la MISMA llamada se sirve con el proveedor de respaldo — el candidato no
ve el outage. El `CircuitBreaker` evita pagar el timeout de un principal caído: tras
`failures` fallos consecutivos abre el circuito (las llamadas van directo al respaldo) y
tras `cooldown_seconds` deja pasar UNA sonda (half-open) para detectar la recuperación.

Config-gated por `.env` (`LLM_FALLBACK_*`, vacíos = apagado): `wrap_with_fallback` devuelve
el LLM sin tocar cuando no hay respaldo configurado. El respaldo es de la INSTALACIÓN
(no por-tenant): también cubre a un tenant cuyo proveedor BYOK esté caído.

Atribución: `model`/`last_usage` reflejan al LLM que REALMENTE sirvió la última llamada,
para que `MeteredLLM` registre tokens/costos/trazas contra el modelo correcto.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from orquestacion.llm import LLM

logger = logging.getLogger(__name__)

_CLOSED = "closed"
_OPEN = "open"
_HALF_OPEN = "half-open"


class CircuitBreaker:
    """Breaker clásico de 3 estados, por instancia (el bot vive en un proceso).

    Reloj inyectable (`now`) para tests deterministas. No es thread-safe estricto: una
    carrera solo produce una sonda extra al principal, sin efecto funcional.
    """

    def __init__(
        self,
        failures: int = 3,
        cooldown_seconds: float = 60.0,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failures = max(1, int(failures))
        self.cooldown_seconds = float(cooldown_seconds)
        self._now = now
        self.state = _CLOSED
        self._consecutive = 0
        self._opened_at = 0.0

    def allow_primary(self) -> bool:
        """¿Se intenta el principal? Abierto → no (salvo cooldown vencido: half-open)."""
        if self.state == _CLOSED:
            return True
        if self._now() - self._opened_at >= self.cooldown_seconds:
            self.state = _HALF_OPEN
            return True
        return False

    def record_success(self) -> None:
        if self.state != _CLOSED:
            logger.info("Circuit breaker LLM: el proveedor principal se recuperó (cierra).")
        self.state = _CLOSED
        self._consecutive = 0

    def record_failure(self) -> None:
        self._consecutive += 1
        # En half-open la sonda falló → reabre de inmediato (reinicia el cooldown).
        if self.state == _HALF_OPEN or self._consecutive >= self.failures:
            if self.state != _OPEN:
                logger.warning(
                    "Circuit breaker LLM: circuito ABIERTO tras %s fallos consecutivos; "
                    "las llamadas van directo al respaldo por %.0f s.",
                    self._consecutive,
                    self.cooldown_seconds,
                )
            self.state = _OPEN
            self._opened_at = self._now()


class FallbackLLM:
    """Sirve con el principal; ante fallo (o circuito abierto), con el respaldo.

    Si ambos fallan propaga la excepción del respaldo: el call site conserva su
    fallback heurístico `low_confidence` de siempre (última línea de defensa).
    """

    def __init__(self, primary: LLM, fallback: LLM, breaker: CircuitBreaker) -> None:
        self._primary = primary
        self._fallback = fallback
        self._breaker = breaker
        # Quién sirvió la última llamada (arranca en el principal): MeteredLLM lee
        # `model`/`last_usage` de aquí para atribuir tokens/costos/trazas.
        self._served: LLM = primary
        # Metadata de tracing (LangSmith): MeteredLLM la actualiza en este dict y
        # complete() la copia al LLM que atiende (principal o respaldo).
        self.metadata: dict[str, str] = {}

    @property
    def model(self) -> str:
        return getattr(self._served, "model", "") or ""

    @property
    def last_usage(self) -> dict[str, int]:
        return getattr(self._served, "last_usage", None) or {}

    def _sync_meta(self, target: LLM) -> None:
        meta = getattr(target, "metadata", None)
        if self.metadata and isinstance(meta, dict):
            meta.update(self.metadata)

    def complete(self, prompt: str) -> str:
        if self._breaker.allow_primary():
            self._sync_meta(self._primary)
            try:
                out = self._primary.complete(prompt)
            except Exception as exc:  # noqa: BLE001 — failover al respaldo
                self._breaker.record_failure()
                logger.warning(
                    "LLM fallback de proveedor: el principal (%s) falló (%s); "
                    "sirviendo con el respaldo (%s).",
                    getattr(self._primary, "model", "?"),
                    type(exc).__name__,
                    getattr(self._fallback, "model", "?"),
                )
            else:
                self._breaker.record_success()
                self._served = self._primary
                return out
        self._sync_meta(self._fallback)
        out = self._fallback.complete(prompt)
        self._served = self._fallback
        return out


def fallback_configured(settings) -> bool:
    return bool((getattr(settings, "llm_fallback_model", "") or "").strip())


def wrap_with_fallback(llm: LLM, settings) -> LLM:
    """Envuelve `llm` con el respaldo del `.env`, o lo devuelve intacto si no hay config.

    El respaldo se construye con `build_default_llm(base_url=, api_key=)` — el mismo y
    único camino de cliente compatible-OpenAI del sistema. `base_url`/`api_key` vacíos
    heredan los del `.env` (respaldo = otro modelo del mismo proveedor).
    """
    if not fallback_configured(settings):
        return llm
    from orquestacion.llm import build_default_llm

    fallback = build_default_llm(
        model=settings.llm_fallback_model.strip(),
        base_url=(getattr(settings, "llm_fallback_base_url", "") or "").strip() or None,
        api_key=(getattr(settings, "llm_fallback_api_key", "") or "").strip() or None,
    )
    breaker = CircuitBreaker(
        failures=getattr(settings, "llm_breaker_failures", 3),
        cooldown_seconds=getattr(settings, "llm_breaker_cooldown_seconds", 60),
    )
    return FallbackLLM(llm, fallback, breaker)
