# MCP — Servidor Model Context Protocol

> Parte de [spec/](README.md) · Última revisión: 2026-07-06 · Código: `adaptadores_mcp/mcp.py`
> Spec normativo: `openspec/specs/mcp/spec.md`

## 1. Propósito y alcance

Expone el pipeline de selección a **clientes LLM externos** (Claude Code/Desktop u otro
orquestador) vía MCP streamable HTTP montado en `/mcp` de la MISMA app FastAPI: 5 tools de
consulta + 2 de mutación con confirmación en dos pasos. Config-gated: `MCP_ENABLED=false` por
defecto.

## 2. Decisiones de diseño

- **Capa de adaptación pura**: cada tool invoca LA MISMA función del endpoint FastAPI
  (`api/routes/*`) pasándole el user resuelto → hereda tenancy, enmascarado por rol y los
  listados sin N+1. Cero lógica duplicada; el MCP no puede hacer nada que la API no permita.
- **Mismo Bearer JWT del dashboard**: `MCPAuthMiddleware` (ASGI, envuelve el mount) valida
  firma+rotación+revocación y deja el user en un **contextvar** que llega a la tool (el session
  manager stateless arranca del task del request). RBAC dentro de la tool (`_require_user` —
  el `Depends` de FastAPI no corre al llamar la función directo).
- **Capability ≠ autoridad — mutaciones en dos fases**: el server es stateless, así que la
  confirmación es un **token HMAC-SHA256** con clave derivada de `jwt_secret|mcp-confirm`
  (un JWT de acceso jamás valida como confirm y viceversa). Llamada SIN `confirm_token` = solo
  preview (valida guards y devuelve efectos + token, TTL 120 s); repetir CON el token ejecuta
  el endpoint real. El payload firmado ata `tool|candidato|decisión|user|tenant|exp` — el token
  no sirve para otro candidato/acción/usuario/tenant.
- **Apagado por defecto**: superficie extra + PII + ahora muta; se enciende por decisión
  explícita (convención del proyecto).

## 3. Diseño e implementación

### Tools (7)

| Tool | Rol | Efecto |
|---|---|---|
| `list_vacancies` | viewer+ | Vacantes del tenant |
| `list_candidates` | viewer+ | Por vacante o pipeline global, con `q`/paginado |
| `get_candidate_detail` | viewer+ | Detalle completo (enmascara psych-exam a viewer) |
| `get_metrics` | viewer+ | Tokens/costos/latencias |
| `get_ops_alerts` | admin | Alertas operativas |
| `contact_candidate` | recruiter+ | **Dos fases**: preview → confirm (llama al endpoint de contacto) |
| `decide_candidate` | recruiter+ | **Dos fases**: preview → confirm (advance/reject) |

### Plumbing
- Mount streamable HTTP en `/mcp` (endpoint real `/mcp/` — Route "/" dentro del mount);
  **gotcha**: FastAPI NO ejecuta el lifespan de sub-apps montadas → el lifespan principal corre
  `session_manager.run()` explícito.
- Anti DNS-rebinding del SDK **desactivado** (valida Host, pensado para servers locales sin
  auth); mitigado por el JWT obligatorio por header.
- Auditoría: cada invocación → `audit_log` con action `mcp.<tool>` (y `mcp.<tool>.preview`);
  entity_type=candidate → cubierto por el scrub de retención.
- Dep `mcp` **pineada <2** (la 2.0 renombra FastMCP). Cliente de referencia:
  `scripts/mcp_client_demo.py` (gotcha del SDK: la lista llega en `structuredContent.result`).
- Conexión: `claude mcp add --transport http leia http://localhost:8000/mcp/ --header
  "Authorization: Bearer <token>"`.

## 4. Contratos e invariantes

- El MCP **nunca** bypasea la API: misma función, mismo user, mismos guards.
- Sin token válido → 401 antes de tocar cualquier tool; `MCP_ENABLED=false` → 404 en `/mcp`.
- Una preview **no muta nada**; solo la repetición con token válido y vigente ejecuta.
- Las `instructions` del server explican el flujo de dos fases al cliente LLM (el protocolo es
  parte del contrato).

## 5. Patrones reutilizables

- **MCP como adaptador de endpoints existentes**: no escribir lógica en las tools; resolver el
  user y delegar. La seguridad se hereda en vez de duplicarse.
- **Confirmación en dos pasos con HMAC stateless**: patrón para darle mutaciones a un agente
  LLM sin sesión del lado servidor — preview con efectos legibles + token atado y con TTL.
- **Auth por contextvar** cuando el framework de tools no propaga el request.
- **Derivar la clave de firma por propósito** (`|mcp-confirm`) para que tokens de dominios
  distintos no se crucen.

## 6. Pendientes conocidos

- Más mutaciones (agendar, feedback de etapa) si un orquestador externo lo pide — mismo patrón.
- Streaming de recursos (transcripciones largas) — hoy respuestas completas.

## 7. Trazabilidad

- Tests: `test_mcp.py` (19: gating, 401s, tenancy, RBAC, roundtrip del token, expiración,
  cross-todo, preview-no-muta, auditoría de ambas fases). Nota: `test_mcp_disabled_by_default`
  lee el `.env` real — falla si el entorno local tiene `MCP_ENABLED=true`.
- Demo viva: `scripts/mcp_client_demo.py`. Config: `MCP_ENABLED` en `.env.example`.
