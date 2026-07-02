"""F2 (auditoría de integraciones) — cobertura de guards de tenant en la API.

Test de *lint estructural*: recorre TODAS las rutas de la app e impone, sin
excepciones silenciosas, dos invariantes de aislamiento multi-empresa:

  1. **Autenticación**: toda ruta `/api/*` (salvo la allowlist pública) resuelve un
     usuario del token (`get_current_user` / `require_role`).
  2. **Guard de tenant por recurso**: toda ruta que carga un recurso por un id de la
     URL (`{vacancy_id}`, `{candidate_id}`, `{recruiter_id}`, `{outbox_id}`) pasa por
     un guard de tenant (`_require_*_in_tenant`) o compara `tenant_id` explícitamente.

Así, si mañana alguien agrega un endpoint que carga un recurso por id y olvida el
guard, este test falla en CI — cerrando la brecha "un solo endpoint que olvide el
guard → fuga cross-tenant" señalada en la auditoría (F2). No depende de la DB.
"""

from __future__ import annotations

import inspect

import api.main as main
from fastapi.routing import APIRoute

# Rutas que legítimamente NO exigen autenticación (contacto inicial del cliente).
_PUBLIC: set[tuple[str, str]] = {
    ("/api/health", "GET"),
    ("/api/auth/login", "POST"),
}

# Nombres de path-param que identifican un recurso de negocio cargado por id: cada
# ruta que reciba uno DEBE aislarlo por tenant antes de exponerlo/mutarlo.
_RESOURCE_PARAMS = {"vacancy_id", "candidate_id", "recruiter_id", "outbox_id"}

# Señales (en el código fuente del handler) de que la ruta aísla por tenant: o pasa
# por un guard dedicado, o compara el `tenant_id` del recurso contra el del usuario.
_TENANT_GUARD_MARKERS = (
    "_require_vacancy_in_tenant",
    "_require_candidate_in_tenant",
    "tenant_id",
)

# Señales de que la ruta resuelve al usuario autenticado del token.
_AUTH_MARKERS = ("get_current_user", "require_role")


def _walk_routes(routes) -> list[APIRoute]:
    """Aplana el árbol de rutas: FastAPI puede registrar los `include_router` como
    routers anidados (no como APIRoute planas), así que se recorre recursivamente."""
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
            continue
        # `include_router` puede quedar como wrapper (p. ej. `_IncludedRouter`) cuyo
        # APIRouter vive en `original_router`; otros anidan directamente en `routes`.
        inner = getattr(r, "original_router", None) or r
        sub = getattr(inner, "routes", None)
        if sub:
            out.extend(_walk_routes(sub))
    return out


def _api_routes() -> list[APIRoute]:
    """Rutas APIRoute bajo /api (excluye estáticas / internas de FastAPI)."""
    return [r for r in _walk_routes(main.app.routes) if r.path.startswith("/api/")]


def _methods(route: APIRoute) -> set[str]:
    """Métodos HTTP relevantes de la ruta (ignora HEAD/OPTIONS automáticos)."""
    return {m for m in route.methods if m not in ("HEAD", "OPTIONS")}


def test_all_api_routes_require_authentication():
    """Toda ruta /api (salvo la allowlist pública) resuelve el usuario del token."""
    offenders: list[str] = []
    for route in _api_routes():
        src = inspect.getsource(route.endpoint)
        for method in _methods(route):
            if (route.path, method) in _PUBLIC:
                continue
            if not any(marker in src for marker in _AUTH_MARKERS):
                offenders.append(f"{method} {route.path}")
    assert not offenders, (
        "Estas rutas /api no exigen autenticación (falta get_current_user / "
        f"require_role): {sorted(offenders)}"
    )


def test_resource_routes_enforce_tenant_guard():
    """Toda ruta que carga un recurso por id de la URL lo aísla por tenant."""
    offenders: list[str] = []
    for route in _api_routes():
        param_names = {p.name for p in route.dependant.path_params}
        if not (param_names & _RESOURCE_PARAMS):
            continue
        src = inspect.getsource(route.endpoint)
        if not any(marker in src for marker in _TENANT_GUARD_MARKERS):
            for method in _methods(route):
                offenders.append(f"{method} {route.path}")
    assert not offenders, (
        "Estas rutas cargan un recurso por id SIN guard de tenant "
        f"(_require_*_in_tenant o chequeo de tenant_id): {sorted(offenders)}"
    )


def test_guard_test_actually_sees_routes():
    """Salvaguarda: el introspector encuentra rutas (si no, los tests de arriba
    pasarían vacíos y no protegerían nada)."""
    routes = _api_routes()
    assert len(routes) >= 20
    # Y al menos una ruta con recurso por id (si no, el segundo test es vacío).
    with_resource = [
        r
        for r in routes
        if {p.name for p in r.dependant.path_params} & _RESOURCE_PARAMS
    ]
    assert len(with_resource) >= 5
