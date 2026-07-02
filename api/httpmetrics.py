"""Métricas HTTP en memoria (auditoría O3): conteo, errores y latencia por ruta.

Acumulador puro y thread-safe, sin dependencias (mismo espíritu que `api/ratelimit.py`).
Lo alimenta un middleware en `api/main.py` con la PLANTILLA de la ruta (p. ej.
`/api/candidates/{candidate_id}`), no la URL cruda — así la cardinalidad queda acotada
y no se acumulan claves con IDs. Ámbito: por proceso (una réplica = exacto). Para un
error tracking real (Sentry) queda el gancho documentado en la auditoría.

Percentiles (O-3): histograma de buckets fijos por ruta (memoria O(1), sin guardar
muestras). p95/p99 se estiman con el techo del bucket donde cae el cuantil — precisión
suficiente para el dashboard; el bucket de desborde reporta `max_ms`.
"""

from __future__ import annotations

import bisect
import threading

# Techos de los buckets de latencia (ms); el último bucket acumula todo lo que exceda.
_BUCKETS_MS: tuple[int, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


def percentile_from_buckets(buckets: list[int], q: float, max_ms: float) -> int:
    """Estima el percentil `q` (0-100) desde el histograma (techo del bucket del cuantil).

    Puro para poder testearlo directo. El bucket de desborde (sin techo) responde `max_ms`.
    """
    count = sum(buckets)
    if count <= 0:
        return 0
    rank = max(1, int(-(-q * count // 100)))  # ceil(q/100 * count), nearest-rank
    cum = 0
    for i, n in enumerate(buckets):
        cum += n
        if cum >= rank:
            return int(_BUCKETS_MS[i]) if i < len(_BUCKETS_MS) else round(max_ms)
    return round(max_ms)


class HttpMetrics:
    """`record(method, path, status, ms)` acumula; `snapshot()` resume por ruta."""

    def __init__(self) -> None:
        self._routes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def record(self, method: str, path: str, status: int, duration_ms: float) -> None:
        key = f"{method} {path}"
        with self._lock:
            agg = self._routes.setdefault(
                key,
                {
                    "count": 0, "errors": 0, "client_errors": 0, "total_ms": 0.0, "max_ms": 0.0,
                    "buckets": [0] * (len(_BUCKETS_MS) + 1),
                },
            )
            agg["count"] += 1
            if status >= 500:
                agg["errors"] += 1
            elif status >= 400:
                agg["client_errors"] += 1
            agg["total_ms"] += max(0.0, duration_ms)
            agg["max_ms"] = max(agg["max_ms"], duration_ms)
            agg["buckets"][bisect.bisect_left(_BUCKETS_MS, max(0.0, duration_ms))] += 1

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
                    "p95_ms": percentile_from_buckets(agg["buckets"], 95, agg["max_ms"]),
                    "p99_ms": percentile_from_buckets(agg["buckets"], 99, agg["max_ms"]),
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
