"""Métricas HTTP en memoria (auditoría O3): conteo, errores y latencia por ruta.

Acumulador puro y thread-safe, sin dependencias (mismo espíritu que `api/ratelimit.py`).
Lo alimenta un middleware en `api/main.py` con la PLANTILLA de la ruta (p. ej.
`/api/candidates/{candidate_id}`), no la URL cruda — así la cardinalidad queda acotada
y no se acumulan claves con IDs. Ámbito: por proceso (una réplica = exacto). Para un
error tracking real (Sentry) queda el gancho documentado en la auditoría.
"""

from __future__ import annotations

import threading


class HttpMetrics:
    """`record(method, path, status, ms)` acumula; `snapshot()` resume por ruta."""

    def __init__(self) -> None:
        self._routes: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    def record(self, method: str, path: str, status: int, duration_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            agg = self._routes.setdefault(
                key, {"count": 0, "errors": 0, "client_errors": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            agg["count"] += 1
            if status >= 500:
                agg["errors"] += 1
            elif status >= 400:
                agg["client_errors"] += 1
            agg["total_ms"] += max(0.0, duration_ms)
            agg["max_ms"] = max(agg["max_ms"], duration_ms)

    def snapshot(self) -> list[dict]:
        """Resumen por ruta, de la más golpeada a la menos (para el dashboard)."""
        with self._lock:
            rows = [
                {
                    "route": key,
                    "count": int(agg["count"]),
                    "errors": int(agg["errors"]),
                    "client_errors": int(agg["client_errors"]),
                    "avg_ms": round(agg["total_ms"] / agg["count"]) if agg["count"] else 0,
                    "max_ms": round(agg["max_ms"]),
                }
                for key, agg in self._routes.items()
            ]
        rows.sort(key=lambda r: r["count"], reverse=True)
        return rows

    def reset(self) -> None:
        """Limpia el estado (aislamiento entre tests)."""
        with self._lock:
            self._routes.clear()


# Singleton del proceso (lo comparten el middleware y el endpoint de observabilidad).
http_metrics = HttpMetrics()
