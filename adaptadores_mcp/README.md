# `adaptadores_mcp/` — Adaptadores MCP (Model Context Protocol)

Componente **Adaptadores MCP** de la rúbrica: expone el sistema a asistentes LLM externos (Claude
Code/Desktop u otro orquestador) mediante el protocolo MCP, con llamadas seguras a herramientas.

| Archivo | Rol |
|---|---|
| `mcp.py` | Servidor MCP (streamable HTTP en `/mcp`, SDK oficial `mcp`) con 7 tools: 5 de lectura + 2 mutaciones (contactar/decidir) con **confirmación en dos pasos** (preview sin efectos + `confirm_token` HMAC de 120 s). |

**Seguridad (conforme al protocolo):** mismo JWT del dashboard (firma+rotación, revocación, tenancy),
RBAC por rol dentro de cada tool, y **auditoría** de cada invocación (`audit_log`, action `mcp.<tool>`).
Cada tool es una capa de adaptación pura que reusa la MISMA función del endpoint FastAPI → hereda
tenancy, enmascarado por rol y listados sin N+1. Config-gated por `MCP_ENABLED` (default **off**:
superficie mínima / PII).

**Cliente demo:** `scripts/mcp_client_demo.py`. **Conectar:**
`claude mcp add --transport http leia http://localhost:8000/mcp/ --header "Authorization: Bearer <token>"`.
