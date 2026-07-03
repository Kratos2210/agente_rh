"""Cliente MCP de demostración: consume el servidor `/mcp` con el SDK oficial.

Demuestra el protocolo end-to-end y su modelo de seguridad:
  1. Login normal del dashboard → JWT (mismo token, misma tenancy y rol).
  2. Handshake MCP (streamable HTTP) con el Bearer en el header.
  3. `tools/list` + invocación de tools de consulta (`list_vacancies`, `list_candidates`).
  4. Lee la bitácora de auditoría para mostrar que cada invocación quedó registrada
     (`mcp.<tool>`).

Requiere el backend corriendo con `MCP_ENABLED=true`, p. ej.:
    MCP_ENABLED=true uv run uvicorn api.main:app --port 8010

Uso:
    uv run python scripts/mcp_client_demo.py [--base-url http://localhost:8010] \
        [--email admin@datawith.ai] [--password admin1234]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402


def login(base_url: str, email: str, password: str) -> str:
    r = httpx.post(
        f"{base_url}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _payload(result) -> object:
    """Extrae el JSON de una respuesta MCP.

    El SDK expone `structuredContent` ({'result': ...}) cuando la tool devuelve
    datos tipados; si no, cada content block trae un JSON por ítem."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    blocks = []
    for block in result.content or []:
        if getattr(block, "text", None):
            try:
                blocks.append(json.loads(block.text))
            except ValueError:
                blocks.append(block.text)
    if not blocks:
        return None
    return blocks[0] if len(blocks) == 1 else blocks


async def demo(base_url: str, token: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(f"{base_url}/mcp/", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools/list → {len(names)} herramientas: {', '.join(names)}")

            vacs = _payload(await session.call_tool("list_vacancies", {}))
            items = vacs if isinstance(vacs, list) else (vacs or {}).get("items", [])
            if not items:
                print("Sin vacantes visibles para este tenant.")
                return 0
            first = items[0]
            print(f"list_vacancies → {len(items)} vacante(s); primera: {first.get('title')}")

            cands = _payload(
                await session.call_tool("list_candidates", {"vacancy_id": first.get("id")})
            )
            citems = (cands or {}).get("items", []) if isinstance(cands, dict) else (cands or [])
            print(f"list_candidates → {len(citems)} candidato(s) en '{first.get('title')}'")

    # La seguridad no es solo el Bearer: cada invocación quedó auditada.
    audit = httpx.get(
        f"{base_url}/api/audit", headers={"Authorization": f"Bearer {token}"}, timeout=15
    )
    if audit.status_code == 200:
        mcp_rows = [a for a in audit.json() if str(a.get("action", "")).startswith("mcp.")]
        print(f"auditoría → {len(mcp_rows)} invocaciones MCP registradas "
              f"(últimas: {[a['action'] for a in mcp_rows[:4]]})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8010")
    parser.add_argument("--email", default="admin@datawith.ai")
    parser.add_argument("--password", default="admin1234")
    args = parser.parse_args()

    try:
        token = login(args.base_url, args.email, args.password)
    except Exception as e:  # noqa: BLE001
        print(f"Login falló contra {args.base_url}: {e}")
        return 1
    print("login → token JWT obtenido (mismo del dashboard)")

    try:
        return asyncio.run(demo(args.base_url, token))
    except Exception as e:  # noqa: BLE001
        print(f"MCP falló: {e} (¿backend con MCP_ENABLED=true?)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
