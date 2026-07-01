"""Autorización OAuth one-time de Google (Calendar + Sheets) para el agendamiento real.

Necesario solo cuando el calendario del reclutador está en un **Gmail personal**: una cuenta
de servicio no puede crear enlaces Meet ni enviar invitaciones ahí, así que el agente actúa
como el propio usuario vía OAuth.

Uso:
    1. En Google Cloud Console: crea un proyecto, habilita "Google Calendar API" (y "Google
       Sheets API" si registrarás reuniones en una hoja), y crea credenciales OAuth. Sirve tanto
       "Aplicación de escritorio" como "Aplicación web"; descarga el JSON → guárdalo (p. ej.
       `secrets/client_secret.json`).
    2. Si el cliente es de tipo **web**, agrega esta *Authorized redirect URI* en la consola:
           http://localhost:8765/
       (el puerto se puede cambiar con GOOGLE_OAUTH_REDIRECT_PORT). Los clientes "desktop" no
       necesitan registrar nada.
    3. En `.env`: SCHEDULING_PROVIDER=google, GOOGLE_OAUTH_CLIENT_PATH=secrets/client_secret.json,
       GOOGLE_OAUTH_TOKEN_PATH=secrets/token.json
    4. Corre: `uv run python scripts/google_oauth.py`
       Se abre el navegador; inicia sesión con la cuenta del reclutador y autoriza.
       Se guarda el token (con refresh token) en GOOGLE_OAUTH_TOKEN_PATH.

A partir de ahí el backend usa ese token y lo refresca solo. Para revocar/cambiar de cuenta:
borra el token.json y vuelve a correr este script.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Mismos scopes mínimos que usa GoogleScheduler (audit F4): eventos+Meet, freebusy y la hoja
# de registro. Deben coincidir con GoogleScheduler._SCOPES.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Puerto fijo del callback local. Para clientes "web" debe coincidir con la redirect URI
# registrada en la consola (http://localhost:PUERTO/). Configurable por si está ocupado.
REDIRECT_PORT = int(os.getenv("GOOGLE_OAUTH_REDIRECT_PORT", "8765"))


def main() -> int:
    load_dotenv()
    from src.config import get_settings

    settings = get_settings()
    client_path = settings.google_oauth_client_path
    token_path = settings.google_oauth_token_path

    if not client_path:
        print("✗ Falta GOOGLE_OAUTH_CLIENT_PATH en .env (ruta al client_secret.json).")
        return 1
    if not Path(client_path).exists():
        print(f"✗ No existe el client_secret en: {client_path}")
        return 1
    if not token_path:
        print("✗ Falta GOOGLE_OAUTH_TOKEN_PATH en .env (dónde guardar el token).")
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_type = next(iter(json.load(open(client_path, encoding="utf-8"))), "")
    print(f"Cliente OAuth tipo: {client_type or 'desconocido'}")
    if client_type == "web":
        print(f"⚠ Asegúrate de tener registrada la redirect URI: http://localhost:{REDIRECT_PORT}/")

    flow = InstalledAppFlow.from_client_secrets_file(client_path, SCOPES)
    # Puerto fijo (compatible con clientes web que registran la redirect URI). Abre el navegador
    # y levanta un servidor local en ese puerto para recibir el código de autorización.
    creds = flow.run_local_server(port=REDIRECT_PORT, prompt="consent")

    out = Path(token_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"✓ Token guardado en {token_path}")
    print("  Ya puedes agendar con Google Calendar real (Meet + invitación por correo).")
    if not creds.refresh_token:
        print("⚠ El token no trae refresh_token; borra el token.json y reautoriza con prompt=consent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
