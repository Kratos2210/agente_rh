"""Servidor MCP (Model Context Protocol) — herramientas de consulta + mutación del agente.

Expone en `/mcp` (streamable HTTP, mismo proceso uvicorn) un contrato para clientes LLM
externos (Claude Code/Desktop u otro orquestador compatible): consulta de vacantes,
candidatos, métricas y alertas, y dos mutaciones (contactar / decidir) protegidas por
confirmación. Config-gated por `MCP_ENABLED` (default off).

Principios (auditoría de integraciones):
  - Capa de adaptación, no lógica nueva: cada tool invoca la MISMA función del endpoint
    FastAPI correspondiente (`api/routes/*`), heredando tenancy, enmascarado por rol y
    los listados sin N+1.
  - Minimización de PII (Ley 29733): un cliente MCP externo reenvía las respuestas al
    proveedor del LLM, así que las tools de candidatos ENMASCARAN la PII antes de salir
    (nombre→seudónimo; CV/teléfono/correo/transcripción fuera). Se conserva el valor
    operativo (semáforo, scores, estado, métricas) y se opera por candidate_id. El
    dashboard no se toca: sigue con la PII completa en la infra propia.
  - Capability ≠ autoridad: las mutaciones (contactar/decidir) exigen rol `recruiter` y
    **confirmación en dos pasos**: la primera llamada NO muta — devuelve un preview con
    los efectos y un `confirm_token` firmado (HMAC, TTL 120 s, ligado a tool + candidato
    + acción + usuario + tenant); solo la repetición con ese token ejecuta. El token usa
    una clave DERIVADA del jwt_secret, así jamás valida como JWT de acceso ni al revés.
  - Mismo perímetro de auth que el dashboard: Bearer JWT propio (`api.auth`), con
    revocación y tenant obligatorio; sin token → 401 antes de tocar el protocolo.
  - Trazabilidad: cada invocación se registra en `audit_log` (action `mcp.<tool>`;
    los previews como `mcp.<tool>.preview`).

Nota de transporte: el session manager del SDK arranca el server MCP con
`task_group.start()` DESDE el task del request (modo stateless), así que los
contextvars seteados por el middleware de auth llegan a la ejecución de la tool.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import time
from typing import Any

from core.config import get_settings
from core.logging_config import get_logger

logger = get_logger("adaptadores_mcp.mcp")

# TTL del token de confirmación de mutaciones (dos pasos): corto a propósito — el humano
# que aprueba el preview debe confirmar en caliente, no reusar tokens viejos.
CONFIRM_TTL_SECONDS = 120

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


def _confirm_key() -> bytes:
    """Clave HMAC DERIVADA del jwt_secret (sufijo fijo): un confirm-token firmado con
    esta clave nunca valida como JWT de acceso, y un JWT robado nunca sirve de confirm."""
    return hashlib.sha256(f"{get_settings().jwt_secret}|mcp-confirm".encode()).digest()


def _issue_confirm_token(
    user: dict[str, Any], tool: str, candidate_id: str, decision: str = "", *, now: float | None = None
) -> str:
    """Token de confirmación: payload firmado ligado a (tool, candidato, acción, usuario,
    tenant) con expiración. `now` inyectable para tests."""
    exp = int(now if now is not None else time.time()) + CONFIRM_TTL_SECONDS
    raw = "|".join(
        [tool, candidate_id, decision, str(user.get("id")), str(user.get("tenant_id")), str(exp)]
    ).encode()
    sig = hmac.new(_confirm_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode() + "." + base64.urlsafe_b64encode(sig).decode()


def _verify_confirm_token(
    token: str,
    user: dict[str, Any],
    tool: str,
    candidate_id: str,
    decision: str = "",
    *,
    now: float | None = None,
) -> bool:
    """Valida firma, vigencia y que el token corresponda EXACTAMENTE a esta invocación
    (misma tool, mismo candidato, misma acción, mismo usuario y tenant)."""
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_b64.encode())
        sig = base64.urlsafe_b64decode(sig_b64.encode())
    except Exception:
        return False
    if not hmac.compare_digest(sig, hmac.new(_confirm_key(), raw, hashlib.sha256).digest()):
        return False
    parts = raw.decode(errors="replace").split("|")
    if len(parts) != 6:
        return False
    t_tool, t_cid, t_dec, t_uid, t_tid, t_exp = parts
    if (t_tool, t_cid, t_dec) != (tool, candidate_id, decision):
        return False
    if t_uid != str(user.get("id")) or t_tid != str(user.get("tenant_id")):
        return False
    try:
        expires = int(t_exp)
    except ValueError:
        return False
    return (now if now is not None else time.time()) <= expires


def _audit_tool(
    user: dict[str, Any], tool: str, *, entity_type: str = "mcp", entity_id: str = ""
) -> None:
    """Registra la invocación en la bitácora (fail-safe, mismo helper del dashboard).

    Para consultas de un candidato se usa entity_type/id del candidato, así la purga
    de PII (S4, `scrub_audit_for_entity`) también cubre estos registros."""
    from api.deps import _audit

    _audit(user, f"mcp.{tool}", entity_type=entity_type, entity_id=entity_id)


# ── Minimización de PII para clientes externos (perfil "sin PII", plan B) ────────
# Las tools de candidatos reusan los endpoints del dashboard, que devuelven PII
# (nombre, CV, teléfono, transcripción). Un cliente MCP externo (p. ej. Claude Code)
# reenvía esas respuestas al proveedor del LLM → aquí se ENMASCARA la PII ANTES de
# que salga del proceso. Se conserva el valor operativo (semáforo, scores, estado,
# métricas, decisiones por id) y la identidad se reemplaza por un seudónimo estable
# derivado del id. El dashboard NO se toca: sigue viendo la PII completa localmente.

_CONTACT_KEY_MARKERS = ("phone", "email", "telegram", "chat_id", "channel_user", "calendar")


def _pseudonym(candidate_id: Any) -> str:
    """Seudónimo estable y referenciable derivado del id (que ya se expone)."""
    cid = str(candidate_id or "")
    return f"Candidato #{cid[:8]}" if cid else "Candidato"


def _scrub_contact_keys(obj: dict[str, Any]) -> dict[str, Any]:
    """Copia del dict sin claves de contacto directo (teléfono/email/chat/agenda)."""
    return {
        k: v for k, v in obj.items()
        if not any(marker in k.lower() for marker in _CONTACT_KEY_MARKERS)
    }


def _mask_candidate_row(row: Any) -> Any:
    """Fila de candidato con el nombre reemplazado por seudónimo (resto es operativo)."""
    if not isinstance(row, dict):
        return row
    r = dict(row)
    if "name" in r:
        r["name"] = _pseudonym(r.get("id"))
    return r


def _mask_candidates_result(result: Any) -> Any:
    """Enmascara la lista de candidatos (formato {items:[...]} o lista plana)."""
    if isinstance(result, dict) and "items" in result:
        return {**result, "items": [_mask_candidate_row(x) for x in result["items"]]}
    if isinstance(result, list):
        return [_mask_candidate_row(x) for x in result]
    return result


def _mask_candidate_detail(detail: Any) -> Any:
    """Detalle del candidato sin PII: nombre→seudónimo, CV/documentos/transcripción
    fuera. Conserva scorecard (scores + justificación), reuniones sin teléfonos/correos,
    feedback por etapa, examen psicológico (ya enmascarado por rol) y transiciones."""
    if not isinstance(detail, dict):
        return detail
    d = dict(detail)
    cand = dict(d.get("candidate") or {})
    cid = cand.get("id")
    if "name" in cand:
        cand["name"] = _pseudonym(cid)
    cand["cv_profile_present"] = bool(cand.get("cv_profile"))
    cand.pop("cv_profile", None)  # email/teléfono/resumen del CV
    # Prescreen: el `summary` del gate de CV cita el nombre y detalles del CV → fuera.
    # Se conserva lo operativo (puntaje + verdict del gate).
    pres = cand.get("prescreen")
    if isinstance(pres, dict):
        cand["prescreen"] = {k: pres[k] for k in ("pre_score", "verdict") if k in pres}
    docs = cand.get("documents")
    if isinstance(docs, list):  # nombres de archivo pueden contener el nombre real
        cand["documents"] = [
            {"type": x.get("type"), "stored": x.get("stored")}
            for x in docs if isinstance(x, dict)
        ]
    d["candidate"] = _scrub_contact_keys(cand)
    # Transcripción = palabras del propio candidato → puede contener PII. Fuera; se
    # conserva solo el conteo (señal operativa de avance de la entrevista).
    tr = d.get("transcript")
    d["transcript_messages"] = len(tr) if isinstance(tr, list) else 0
    d.pop("transcript", None)
    meetings = d.get("meetings")
    if isinstance(meetings, list):
        d["meetings"] = [_scrub_contact_keys(m) if isinstance(m, dict) else m for m in meetings]
    return d


def _mask_vacancy(vacancy: Any) -> Any:
    """Vacante con el contacto del reclutador (staff) minimizado; el resto intacto."""
    if not isinstance(vacancy, dict):
        return vacancy
    r = dict(vacancy)
    rec = r.get("recruiter")
    if isinstance(rec, dict):
        r["recruiter"] = _scrub_contact_keys(rec)
    return r


def build_mcp_server():
    """Construye el FastMCP: 5 herramientas de lectura + 2 mutaciones con confirmación."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        "leia-talento",
        instructions=(
            "Herramientas del agente de selección de talento (LeIA). Todas las "
            "respuestas están acotadas al tenant del token y al rol del usuario. "
            "Las herramientas de consulta son de solo lectura. Las mutaciones "
            "(contact_candidate, decide_candidate) usan confirmación en dos pasos: "
            "llámalas primero SIN confirm_token para obtener un preview de los "
            "efectos y un token; muestra el preview al usuario humano y, solo si lo "
            "aprueba, repite la llamada con el confirm_token (expira en 120 s). "
            "PRIVACIDAD (Ley 29733): las respuestas de candidatos vienen SIN PII — el "
            "nombre aparece como seudónimo 'Candidato #<id>' y el CV, teléfono, correo "
            "y la transcripción NO se exponen. Trabaja siempre por candidate_id."
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
        """Lista las vacantes del tenant con su embudo (importados/aptos/etc.) y reclutador.
        El contacto del reclutador (teléfono/correo) viene minimizado."""
        from api.routes.vacancies import list_vacancies as impl

        user = _require_user("viewer")
        _audit_tool(user, "list_vacancies")
        return [_mask_vacancy(v) for v in impl(user=user)]

    @mcp.tool()
    def list_candidates(
        vacancy_id: str = "", q: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Lista candidatos con semáforo y estado. Con `vacancy_id` filtra esa vacante;
        sin él, el pipeline global del tenant. `q` busca por nombre; paginado. El nombre
        de cada candidato viene enmascarado como seudónimo (identifícalos por su id)."""
        user = _require_user("viewer")
        _audit_tool(user, "list_candidates")
        if vacancy_id:
            from api.routes.vacancies import list_candidates as impl

            return _mask_candidates_result(impl(vacancy_id, q=q, limit=limit, offset=offset, user=user))
        from api.routes.candidates import list_all_candidates as impl

        return _mask_candidates_result(impl(q=q, limit=limit, offset=offset, user=user))

    @mcp.tool()
    def get_candidate_detail(candidate_id: str) -> dict[str, Any]:
        """Detalle de un candidato SIN PII: scorecard por criterio (scores +
        justificación), reuniones (sin teléfonos/correos), feedback por etapa y
        transiciones. El nombre viene como seudónimo; el CV, los datos de contacto y
        la transcripción NO se exponen (solo el conteo de mensajes)."""
        from api.routes.candidates import get_candidate_detail as impl

        user = _require_user("viewer")
        _audit_tool(user, "get_candidate_detail", entity_type="candidate", entity_id=candidate_id)
        return _mask_candidate_detail(impl(candidate_id, user=user))

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

    # ── Mutaciones (dos pasos: preview + confirm_token) ──────────────────────────

    @mcp.tool()
    def contact_candidate(candidate_id: str, confirm_token: str = "") -> dict[str, Any]:
        """MUTACIÓN · Envía el primer contacto por Telegram a un candidato apto
        (`prescreen_passed`), igual que el botón "Contactar" del dashboard. Dos pasos:
        sin `confirm_token` NO cambia nada — devuelve un preview con los efectos y un
        token; repite la llamada con ese token (expira en 120 s) para ejecutar.
        Requiere rol recruiter."""
        from api.deps import _require_candidate_in_tenant
        from api.routes.candidates import contact_candidate as impl

        user = _require_user("recruiter")
        candidate, _vacancy = _require_candidate_in_tenant(candidate_id, user)
        if not confirm_token:
            _audit_tool(
                user, "contact_candidate.preview", entity_type="candidate", entity_id=candidate_id
            )
            if candidate.get("status") != "prescreen_passed":
                raise ValueError(
                    "El candidato ya fue contactado o no está apto para contactar "
                    f"(estado actual: {candidate.get('status')})."
                )
            return {
                "requires_confirmation": True,
                "action": "contact",
                "candidate": {
                    "id": candidate_id,
                    "name": _pseudonym(candidate_id),
                    "status": candidate.get("status"),
                },
                "effects": (
                    "Se enviará el saludo inicial con botones Acepto/No interesado por "
                    "Telegram y el candidato pasará a 'invited'."
                ),
                "confirm_token": _issue_confirm_token(user, "contact_candidate", candidate_id),
                "expires_in_seconds": CONFIRM_TTL_SECONDS,
            }
        if not _verify_confirm_token(confirm_token, user, "contact_candidate", candidate_id):
            raise ValueError("confirm_token inválido o vencido: pide el preview de nuevo.")
        _audit_tool(user, "contact_candidate", entity_type="candidate", entity_id=candidate_id)
        return impl(candidate_id, user=user)

    @mcp.tool()
    def decide_candidate(
        candidate_id: str, decision: str, confirm_token: str = ""
    ) -> dict[str, Any]:
        """MUTACIÓN · Decide sobre un candidato evaluado: 'advance' (avanza; si el
        agendamiento del tenant está activo, abre la coordinación de horario por
        Telegram) o 'reject' (rechaza y notifica). Dos pasos: sin `confirm_token` NO
        cambia nada — devuelve un preview con los efectos y un token; repite la llamada
        con ese token (expira en 120 s) para ejecutar. Requiere rol recruiter."""
        from api.deps import _require_candidate_in_tenant
        from api.routes.candidates import DecisionIn
        from api.routes.candidates import decide_candidate as impl
        from api.runtime import _DEFAULT_SCHEDULING
        from db import repositories as repo

        user = _require_user("recruiter")
        if decision not in ("advance", "reject"):
            raise ValueError("decision debe ser 'advance' o 'reject'")
        candidate, _vacancy = _require_candidate_in_tenant(candidate_id, user)
        if not confirm_token:
            _audit_tool(
                user, "decide_candidate.preview", entity_type="candidate", entity_id=candidate_id
            )
            if decision == "reject":
                effects = "El candidato pasará a 'rejected' y se le notificará por Telegram."
            else:
                sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"]) or {}
                effects = (
                    "Se abrirá por Telegram la coordinación del horario de la entrevista con RR.HH."
                    if sched.get("enabled")
                    else "El candidato pasará a 'advanced' y se le notificará por Telegram."
                )
            return {
                "requires_confirmation": True,
                "action": decision,
                "candidate": {
                    "id": candidate_id,
                    "name": _pseudonym(candidate_id),
                    "status": candidate.get("status"),
                },
                "effects": effects,
                "confirm_token": _issue_confirm_token(
                    user, "decide_candidate", candidate_id, decision
                ),
                "expires_in_seconds": CONFIRM_TTL_SECONDS,
            }
        if not _verify_confirm_token(confirm_token, user, "decide_candidate", candidate_id, decision):
            raise ValueError("confirm_token inválido o vencido: pide el preview de nuevo.")
        _audit_tool(user, "decide_candidate", entity_type="candidate", entity_id=candidate_id)
        return impl(candidate_id, DecisionIn(decision=decision), user=user)

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
    logger.info(
        "Servidor MCP montado en /mcp (7 herramientas: 5 lectura + 2 mutación con confirmación)"
    )
    return server
