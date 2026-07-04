#!/usr/bin/env bash
# Registra (o re-registra) el servidor MCP de agente_rh en Claude Code.
#
# El servidor MCP vive en adaptadores_mcp/mcp.py y se sirve en /mcp del backend
# (config-gated por MCP_ENABLED=true). La autenticación es el MISMO JWT del
# dashboard, que caduca (JWT_EXPIRE_MINUTES, ~12 h por defecto). Como el header
# Bearer del registro es estático, hay que re-correr este script cuando el token
# expire.
#
# Requisitos: backend arriba en $BACKEND_URL con MCP_ENABLED=true, y un usuario
# admin válido (por defecto admin@datawith.ai / admin1234, o ADMIN_EMAIL/
# ADMIN_PASSWORD del entorno).
#
# Uso:  scripts/mcp_register.sh [nombre]      (nombre por defecto: leia)
set -euo pipefail

NAME="${1:-leia}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@datawith.ai}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin1234}"

echo "→ Login como ${ADMIN_EMAIL} en ${BACKEND_URL} ..."
RESP="$(curl -s -X POST "${BACKEND_URL}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")"

TOKEN="$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')"
if [ -z "$TOKEN" ]; then
  echo "✗ No se obtuvo access_token. Respuesta del backend:" >&2
  echo "$RESP" >&2
  exit 1
fi
echo "✓ Token obtenido."

# Re-registro idempotente: quita el anterior si existe.
claude mcp remove "$NAME" 2>/dev/null || true

echo "→ Registrando MCP '${NAME}' → ${BACKEND_URL}/mcp/ ..."
claude mcp add --transport http "$NAME" "${BACKEND_URL}/mcp/" \
  --header "Authorization: Bearer ${TOKEN}"

echo "✓ Listo. Verifica con: claude mcp list"
