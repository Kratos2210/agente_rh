"""Servidor MCP (Model Context Protocol) — herramientas READ-ONLY del agente.

Expone en `/mcp` (streamable HTTP, mismo proceso uvicorn) un contrato de consulta para
clientes LLM externos (Claude Code/Desktop u otro orquestador compatible): vacantes,
candidatos, métricas y alertas operativas. Config-gated por `MCP_ENABLED` (default off).

Principios (auditoría de integraciones):
  - Capa de adaptación, no lógica nueva: cada tool invoca la MISMA función del endpoint
    FastAPI correspondiente (`api/routes/*`), heredando tenancy, enmascarado por rol y
    los listados sin N+1.
  - Capability ≠ autoridad: v1 es solo lectura; las mutaciones (contactar/decidir)
    quedan como extensión futura detrás de roles + confirmación.
  - Mismo perímetro de auth que el dashboard: Bearer JWT propio (`api.auth`), con
    revocación y tenant obligatorio; sin token → 401 antes de tocar el protocolo.
  - Trazabilidad: cada invocación se registra en `audit_log` (action `mcp.<tool>`).

Nota de transporte: el session manager del SDK arranca el server MCP con
`task_group.start()` DESDE el task del request (modo stateless), así que los
contextvars seteados por el middleware de auth llegan a la ejecución de la tool.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

from src.config import get_settings
from src.logging_config import get_logger

logger = get_logger("api.mcp")

# Usuario autenticado del request MCP en curso (lo setea MCPAuthMiddleware).
_current_user: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mcp_user", default=None
)


def _require_user(min_role: str = "viewer") -> dict[str, Any]:
    """Usuario del request actual, exigiendo el rol mínimo (jerárquico)."""
    from api.auth import role_allows

    user = _current_user.get()
    if not user:  # el middleware ya corta sin token; esto es defensa en profundidad
        raise PermissionError("No autenticado")
    if not role_allows(user.get("role", ""), min_role):
        raise PermissionError(f"Rol insuficiente: esta herramienta requiere '{min_role}'")
    return user


def _audit_tool(
    user: dict[str, Any], tool: str, *, entity_type: str = "mcp", entity_id: str = ""
) -> None:
    """Registra la invocación en la bitácora (fail-safe, mismo helper del dashboard).

    Para consultas de un candidato se usa entity_type/id del candidato, así la purga
    de PII (S4, `scrub_audit_for_entity`) también cubre estos registros."""
    from api.deps import _audit

    _audit(user, f"mcp.{tool}", entity_type=entity_type, entity_id=entity_id)


def build_mcp_server():
    """Construye el FastMCP con las herramientas v1 (solo lectura)."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        "leia-talento",
        instructions=(
            "Herramientas de consulta del agente de selección de talento (LeIA). "
            "Todas las respuestas están acotadas al tenant del token y al rol del "
            "usuario. Solo lectura: para contactar o decidir sobre candidatos usa "
            "el dashboard."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # La protección anti DNS-rebinding del SDK valida el header Host (pensada para
        # servidores locales SIN auth). Aquí cada request exige un JWT firmado en
        # Authorization — un browser "rebindeado" no puede adjuntarlo — y el Host varía
        # por despliegue (localhost, dominio, proxy), así que se desactiva.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def list_vacancies() -> list[dict[str, Any]]:
        """Lista las vacantes del tenant con su embudo (importados/aptos/etc.) y reclutador."""
        from api.routes.vacancies import list_vacancies as impl

        user = _require_user("viewer")
        _audit_tool(user, "list_vacancies")
        return impl(user=user)

    @mcp.tool()
    def list_candidates(
        vacancy_id: str = "", q: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Lista candidatos con semáforo y estado. Con `vacancy_id` filtra esa vacante;
        sin él, el pipeline global del tenant. `q` busca por nombre; paginado."""
        user = _require_user("viewer")
        _audit_tool(user, "list_candidates")
        if vacancy_id:
            from api.routes.vacancies import list_candidates as impl

            return impl(vacancy_id, q=q, limit=limit, offset=offset, user=user)
        from api.routes.candidates import list_all_candidates as impl

        return impl(q=q, limit=limit, offset=offset, user=user)

    @mcp.tool()
    def get_candidate_detail(candidate_id: str) -> dict[str, Any]:
        """Detalle completo de un candidato: scorecard por criterio, transcripción,
        reuniones, feedback por etapa y transiciones (examen psicológico según rol)."""
        from api.routes.candidates import get_candidate_detail as impl

        user = _require_user("viewer")
        _audit_tool(user, "get_candidate_detail", entity_type="candidate", entity_id=candidate_id)
        return impl(candidate_id, user=user)

    @mcp.tool()
    def get_metrics() -> dict[str, Any]:
        """Métricas globales del tenant: embudo de candidatos y consumo LLM
        (tokens/llamadas/errores/latencia por etapa) con costo estimado."""
        from api.routes.candidates import global_metrics_endpoint as impl

        user = _require_user("viewer")
        _audit_tool(user, "get_metrics")
        return impl(user=user)

    @mcp.tool()
    def get_ops_alerts() -> dict[str, Any]:
        """Alertas operativas del tenant (dead-letters, reuniones sin Meet, coordinaciones
        estancadas, divergencias, entregas fallidas). Requiere rol admin."""
        from api.routes.observability import list_ops_alerts as impl

        user = _require_user("admin")
        _audit_tool(user, "get_ops_alerts")
        return impl(user=user)

    return mcp


class MCPAuthMiddleware:
    """ASGI middleware del mount `/mcp`: exige el Bearer JWT del dashboard.

    Valida firma/expiración (con rotación grácil), revocación y tenant, y deja el
    usuario en el contextvar para las tools. Sin credenciales válidas → 401 sin
    tocar el protocolo MCP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        user = self._resolve_user(scope)
        if user is None:
            await self._unauthorized(send)
            return
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)

    @staticmethod
    def _resolve_user(scope) -> dict[str, Any] | None:
        import jwt as pyjwt

        from api.auth import _is_user_revoked, decode_access_token

        auth_header = ""
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == "authorization":
                auth_header = value.decode("latin-1")
                break
        if not auth_header.lower().startswith("bearer "):
            return None
        try:
            claims = decode_access_token(auth_header[7:].strip(), get_settings())
        except pyjwt.PyJWTError:
            return None
        if not claims.get("tenant_id"):
            return None
        if _is_user_revoked(str(claims.get("sub") or "")):
            return None
        return {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "role": claims.get("role"),
            "tenant_id": claims.get("tenant_id"),
        }

    @staticmethod
    async def _unauthorized(send) -> None:
        body = json.dumps({"detail": "No autenticado"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def mount_mcp(app):
    """Monta el servidor MCP (con auth) en `/mcp` y devuelve el FastMCP.

    El caller debe correr `server.session_manager.run()` en su lifespan: el mount es
    una sub-app Starlette cuyo lifespan FastAPI NO ejecuta (gotcha conocido)."""
    server = build_mcp_server()
    app.mount("/mcp", MCPAuthMiddleware(server.streamable_http_app()))
    logger.info("Servidor MCP montado en /mcp (5 herramientas read-only)")
    return server
