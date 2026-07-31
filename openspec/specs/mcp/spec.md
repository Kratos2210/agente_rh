# mcp Specification

## Purpose
Superficie Model Context Protocol para clientes LLM externos: consulta del pipeline y
mutaciones con confirmación. Manual de dominio: `spec/MCP.md`.

## Requirements

### Requirement: Apagado por defecto
El servidor MCP DEBE (SHALL) estar gobernado por `MCP_ENABLED` (default false); apagado,
`/mcp` DEBE (MUST) responder 404.

#### Scenario: Instalación fresca
- **GIVEN** un `.env` sin `MCP_ENABLED`
- **WHEN** un cliente llama a `/mcp/`
- **THEN** recibe 404

### Requirement: Misma identidad que el dashboard
Toda petición MCP DEBE (MUST) autenticarse con el MISMO Bearer JWT del dashboard (firma,
rotación, revocación, tenant); sin token válido DEBE (MUST) responder 401 antes de tocar
cualquier tool.

#### Scenario: Token revocado
- **GIVEN** un usuario desactivado con token aún vigente
- **WHEN** invoca una tool
- **THEN** recibe 401

### Requirement: Tools como adaptadores de la API
Cada tool DEBE (MUST) delegar en la misma función del endpoint del dashboard con el user
resuelto — heredando tenancy, RBAC y enmascarado por rol — sin lógica de negocio propia.

#### Scenario: Viewer pide alertas operativas
- **GIVEN** un token de rol viewer
- **WHEN** invoca `get_ops_alerts` (admin)
- **THEN** la tool devuelve el error de permiso del mismo modo que la API

### Requirement: Mutaciones en dos fases
Las tools de mutación DEBEN (MUST) operar en dos fases: sin `confirm_token` NO mutan (preview
con efectos + token HMAC, TTL 120 s); con token válido ejecutan el endpoint real. El token DEBE
(MUST) estar atado a tool, candidato, decisión, usuario y tenant, y firmarse con clave derivada
exclusiva (un JWT de acceso jamás valida como confirmación).

#### Scenario: Preview y confirmación
- **GIVEN** `decide_candidate` invocada sin token para rechazar a un candidato
- **WHEN** el cliente repite la llamada con el `confirm_token` recibido dentro del TTL
- **THEN** solo la segunda llamada ejecuta el rechazo; la primera no cambió nada

#### Scenario: Token reutilizado para otro candidato
- **GIVEN** un `confirm_token` emitido para el candidato A
- **WHEN** se usa contra el candidato B
- **THEN** la verificación falla y no hay mutación

### Requirement: Auditoría de cada invocación
Toda invocación de tool DEBE (SHALL) registrarse en el audit log (`mcp.<tool>` y
`mcp.<tool>.preview`), quedando cubierta por el scrub de retención.

#### Scenario: Sesión de consultas por MCP
- **GIVEN** un asistente que listó vacantes y candidatas
- **WHEN** un admin revisa `/api/audit`
- **THEN** ve las acciones `mcp.list_vacancies` y `mcp.list_candidates` con su autor
