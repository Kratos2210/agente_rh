# Gestión y rotación de secretos — `agente_rh`

Runbook operativo para el hallazgo **F5** de `auditoria_integraciones_externas.md`
(secretos en `.env` plano, sin gestor ni rotación). Objetivo: que cualquiera pueda
**rotar** un secreto comprometido o vencido sin adivinar, y endurecer el despliegue
antes de producción.

> Estado del MVP: los secretos viven en `.env` (fuera del código, ya en `.gitignore`).
> Es **aceptable para el MVP**. Este documento define cómo rotarlos y el camino a un
> gestor de secretos para producción real.

## Guarda automática al arrancar

`api.auth.assert_secure_config(settings)` corre en el `lifespan` **antes de servir**.
En `ENVIRONMENT=production` **detiene el arranque** (RuntimeError) si:

- `JWT_SECRET` es el default o mide < 32 bytes.
- `JWT_SECRET_PREVIOUS` contiene un secreto de rotación default/corto (< 32 bytes).
- `ADMIN_PASSWORD` es el default (`admin1234`) o mide < 12 caracteres.

En `development` solo advierte (no bloquea). Cubierto por `tests/test_secrets.py`.

## Inventario de secretos

| Secreto (`.env`) | Qué protege | Radio de impacto si se filtra | Dónde se rota |
|---|---|---|---|
| `JWT_SECRET` | Firma de sesiones del dashboard | Suplantar a cualquier usuario/tenant | Este archivo (rotación grácil) |
| `SUPABASE_SERVICE_KEY` | Acceso total a la DB (salta RLS) | Lectura/escritura de todos los tenants | Supabase → API keys |
| `DATABASE_URL` | Conexión Postgres directa (checkpointer) | Acceso a la DB | Supabase → Database (password) |
| `TELEGRAM_BOT_TOKEN` | Control del bot | Enviar/leer mensajes como el bot | BotFather |
| `SMTP_PASSWORD` | Envío de correos | Enviar correo como la cuenta | Proveedor SMTP (app password) |
| `GOOGLE_OAUTH_TOKEN_PATH` / `GOOGLE_CREDENTIALS_PATH` | Calendar + Sheets | Crear eventos / escribir la hoja | Google Cloud / OAuth |
| `ADMIN_PASSWORD` | Admin inicial (bootstrap) | Acceso admin al arrancar sin usuarios | `.env` + cambiar tras 1er login |
| `OPENAI_API_KEY` | LLM (Groq/gateway) | Consumo de tokens facturados | Panel del proveedor |
| `LANGSMITH_API_KEY` | Trazas (opcional) | Ver trazas | LangSmith |

## Rotación por secreto

### `JWT_SECRET` — rotación grácil (sin cerrar sesiones)

El código firma **siempre** con `JWT_SECRET` y acepta al validar el actual **más** los de
`JWT_SECRET_PREVIOUS` (CSV). Así se rota sin invalidar los tokens vivos:

1. Genera uno nuevo: `openssl rand -base64 48`.
2. En `.env`: mueve el valor actual de `JWT_SECRET` a `JWT_SECRET_PREVIOUS` y pon el nuevo
   en `JWT_SECRET`.
   ```
   JWT_SECRET=<nuevo>
   JWT_SECRET_PREVIOUS=<anterior>
   ```
3. Reinicia el backend. Los tokens viejos siguen válidos (validan contra `PREVIOUS`); los
   nuevos se firman con `<nuevo>`.
4. Pasada la ventana de gracia (≈ `JWT_EXPIRE_MINUTES`, default 12 h) **vacía**
   `JWT_SECRET_PREVIOUS=` y reinicia. Los tokens firmados con el viejo dejan de validar.

Rotación de emergencia (secreto comprometido): omite el paso 2/3 —pon solo el nuevo en
`JWT_SECRET` y deja `JWT_SECRET_PREVIOUS` vacío— para **invalidar todas** las sesiones ya.

### `SUPABASE_SERVICE_KEY` / `DATABASE_URL`

Supabase → Project Settings → API (rota la service_role key) / Database (rota el password).
Actualiza `.env` y reinicia. Local (Docker): las llaves las imprime `supabase status`.

### `TELEGRAM_BOT_TOKEN`

BotFather → `/revoke` → copia el token nuevo a `.env` → reinicia. El token viejo queda
inservible al instante (recomendado si quedó escrito en logs históricos — ver F1).

### `SMTP_PASSWORD`

Regenera el *app password* en el proveedor de correo, actualiza `.env`, reinicia. El outbox
reintentará solo los envíos que hayan fallado durante el corte.

### Google (Calendar/Sheets)

- OAuth de usuario: borra `token.json` y re-corre `uv run python scripts/google_oauth.py`
  (regenera el refresh token). Reducir scopes (F4) también invalida el token previo.
- Cuenta de servicio: rota la key en Google Cloud → IAM → Service Accounts → Keys.

### `ADMIN_PASSWORD`

Es solo para el **bootstrap** (crea el admin si no hay usuarios). Tras el primer login,
cambia la contraseña del usuario admin desde el dashboard; `ADMIN_PASSWORD` deja de usarse
(el bootstrap es idempotente: no re-crea usuarios).

## Cadencia recomendada

- **JWT_SECRET**: cada 90 días, o inmediato ante sospecha de fuga.
- **Service key / DB password / SMTP / Google**: cada 6–12 meses, o inmediato ante fuga.
- **Telegram token**: solo ante fuga (rotar invalida el token vivo).
- Tras cualquier incidente que exponga logs/backups: rota lo del inventario que aparezca.

## Camino a producción (endurecimiento)

Para prod real, mover los secretos fuera del `.env` plano a un gestor y cargarlos como
variables de entorno del proceso (Pydantic Settings las lee igual, sin cambiar código):

- **Orquestador**: secrets de Docker Compose / Kubernetes / systemd `EnvironmentFile` con
  permisos restringidos.
- **Gestor dedicado**: Doppler, HashiCorp Vault, AWS/GCP Secrets Manager, o **Supabase Vault**.
  En Kubernetes, el camino "rellená y aplicá" ya está scaffoldeado con **External Secrets
  Operator** en [`deploy/k8s/secret-manager/`](../deploy/k8s/secret-manager/README.md): sincroniza
  desde el gestor al mismo Secret `agente-rh-secrets` que consumen los deployments, sin tocar la app.
- Nunca commitear `.env` (ya en `.gitignore`); usar `.env.example` como plantilla sin valores.
- Habilitar rotación automática donde el gestor lo soporte y auditar accesos.
