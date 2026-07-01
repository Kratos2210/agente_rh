"""Rate limiting en memoria, sin dependencias (auditoría R1/R2).

Dos piezas puras e inyectables (reloj/fecha como parámetro → testeables):

  - `SlidingWindowLimiter`: ventana deslizante por clave (p. ej. IP) para endpoints
    HTTP sensibles como el login (R1: fuerza bruta).
  - `TurnGovernor`: gobierna los turnos del bot por chat — cooldown entre mensajes +
    tope diario (R2: cada mensaje del candidato cuesta llamadas LLM; sin tope, un
    abuso quema el presupuesto y además reinicia el reloj de inactividad).

Ámbito: por proceso. Con una sola réplica (el despliegue actual) el límite es exacto;
con N réplicas cada una aplica el suyo (N× el límite como peor caso — suficiente como
primera barrera; un límite global compartido requeriría Redis, fuera del MVP).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date as _date
from datetime import datetime, timezone
from typing import Optional

#: Podar claves muertas cuando el mapa crece más allá de esto (evita fuga de memoria).
_PRUNE_THRESHOLD = 10_000


class SlidingWindowLimiter:
    """`allow(key)` → True si la clave hizo menos de `max_calls` en los últimos `per_seconds`."""

    def __init__(self, max_calls: int, per_seconds: float):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        cutoff = now - self.per_seconds
        with self._lock:
            if len(self._hits) > _PRUNE_THRESHOLD:
                self._hits = {k: q for k, q in self._hits.items() if q and q[-1] > cutoff}
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        """Limpia el estado (aislamiento entre tests)."""
        with self._lock:
            self._hits.clear()


# Veredictos de TurnGovernor.check().
TURN_OK = "ok"                # procesar el turno
TURN_COOLDOWN = "cooldown"    # demasiado seguido: ignorar en silencio
TURN_CAP_NOTICE = "cap_notice"  # tope diario recién alcanzado: avisar UNA vez
TURN_BLOCKED = "blocked"      # sobre el tope: ignorar en silencio


class TurnGovernor:
    """Cooldown por chat + tope diario de turnos del bot.

    `check(chat_id)` devuelve TURN_OK / TURN_COOLDOWN / TURN_CAP_NOTICE / TURN_BLOCKED.
    El aviso de tope se emite una sola vez por día (para no generar un loop de spam)."""

    def __init__(self, cooldown_seconds: float = 2.0, max_turns_per_day: int = 120):
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_turns_per_day = int(max_turns_per_day)
        self._last: dict[str, float] = {}
        self._counts: dict[str, int] = {}          # clave "chat|YYYY-MM-DD"
        self._day: Optional[_date] = None
        self._lock = threading.Lock()

    def check(self, chat_id: str, now: Optional[float] = None, today: Optional[_date] = None) -> str:
        now = time.monotonic() if now is None else now
        today = today or datetime.now(timezone.utc).date()
        key = f"{chat_id}|{today.isoformat()}"
        with self._lock:
            if self._day != today:  # día nuevo: los contadores de ayer ya no sirven
                self._counts = {k: v for k, v in self._counts.items() if k.endswith(today.isoformat())}
                self._day = today
            last = self._last.get(str(chat_id))
            if last is not None and (now - last) < self.cooldown_seconds:
                return TURN_COOLDOWN
            count = self._counts.get(key, 0)
            if self.max_turns_per_day and count >= self.max_turns_per_day:
                # Recién alcanzado (== tope) → aviso único; después, silencio.
                if count == self.max_turns_per_day:
                    self._counts[key] = count + 1
                    return TURN_CAP_NOTICE
                return TURN_BLOCKED
            self._last[str(chat_id)] = now
            self._counts[key] = count + 1
            return TURN_OK

    def reset(self) -> None:
        with self._lock:
            self._last.clear()
            self._counts.clear()
            self._day = None
