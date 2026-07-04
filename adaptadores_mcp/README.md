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

**Minimización de PII (perfil "sin PII", Ley 29733):** un cliente MCP externo reenvía las
respuestas al proveedor del LLM, así que las tools de candidatos **enmascaran la PII antes de
salir del proceso**: el nombre se reemplaza por un seudónimo estable `Candidato #<id>`, y el CV
(`cv_profile`), teléfono, correo, `channel_user_id`, la transcripción y el `summary` del
prescreen **no se exponen** (solo el conteo de mensajes y un flag `cv_profile_present`). Se
conserva el valor operativo (semáforo, scores, verdict, estado, métricas, reuniones sin
teléfonos/correos, feedback por etapa). El contacto del reclutador en `list_vacancies` también
se minimiza. El dashboard NO se toca: sigue con la PII completa en la infra propia. *(Residual
consciente: las justificaciones del scorecard son texto del evaluador y podrían citar un dato
que el candidato dijo en la entrevista; se mantienen por su valor.)*

**Cómo activarlo y conectarlo:**
1. `scripts/run_mcp.sh` — arranca el backend con `MCP_ENABLED=true` **solo** para esa ejecución
   (el flag NO va en `.env`: la convención es "config-gated, default off"). Requiere Supabase.
2. `scripts/mcp_register.sh [nombre]` — hace login admin, saca un JWT fresco y registra
   (idempotente) el MCP en Claude Code. **Re-correr cuando el token expire** (~12 h,
   `JWT_EXPIRE_MINUTES`), ya que el header Bearer del registro es estático.
3. Verificar: `claude mcp list` → `leia … ✔ Connected`.

**Cliente demo (sin Claude):** `scripts/mcp_client_demo.py`. **Registro manual:**
`claude mcp add --transport http leia http://localhost:8000/mcp/ --header "Authorization: Bearer <token>"`.
