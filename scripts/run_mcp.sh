#!/usr/bin/env bash
# Arranca el backend con el servidor MCP habilitado.
#
# El MCP viene apagado por defecto (MCP_ENABLED no está en .env, a propósito:
# superficie mínima / la convención "config-gated, default off"). Este script lo
# enciende SOLO para esta ejecución, sin tocar el .env. Requiere Supabase arriba.
#
# Tras arrancar, en otra terminal: scripts/mcp_register.sh  (registra el MCP en
# Claude Code con un token fresco).
#
# Uso:  scripts/run_mcp.sh [puerto]     (puerto por defecto: 8000)
set -euo pipefail
PORT="${1:-8000}"
cd "$(dirname "$0")/.."
echo "→ Backend + MCP en http://localhost:${PORT}  (MCP en /mcp/)"
MCP_ENABLED=true exec uv run uvicorn api.main:app --port "$PORT" --reload
