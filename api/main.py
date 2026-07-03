"""FastAPI backend del Agente de Selección de Talento (agente_rh) — ensamblaje.

Tras el refactor A2 (auditoría e2e), este módulo solo:
  - arranca el proceso (lifespan): bot de Telegram (polling) + scheduler + admin inicial;
  - ensambla la app: CORS, health, login/me y los routers por dominio
    (`api/routes/*`: vacantes, candidatos, reclutadores, configuración, observabilidad);
  - re-exporta los símbolos históricos (`api.scheduler`, `api.deps`, `api.runtime`)
    para no romper a los consumidores existentes (tests/scripts que importan api.main).

La lógica vive en:
  - `api/scheduler.py` — loop + barridos (auto-contacto, inactividad, outbox,
    reconciliación, retención) y contacto de candidatos.
  - `api/deps.py` — guards de tenant, auditoría y helpers de listados.
  - `api/runtime.py` — estado compartido del proceso + defaults de configuración.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import authenticate, create_access_token, get_current_user
from api.ratelimit import SlidingWindowLimiter
from api.runtime import (  # noqa: F401 — re-export (compat tests/consumidores)
    _DEFAULT_AUTO_CONTACT,
    _DEFAULT_INACTIVITY,
    _DEFAULT_RETENTION,
    _DEFAULT_SCHEDULING,
    _RETENTION_STATUSES,
    _now_iso,
    _now_local,
    _parse_dt,
    _state,
    current_settings,
    init_phoenix,
    init_sentry,
)
from api.scheduler import (  # noqa: F401 — re-export (compat tests/consumidores)
    _acquire_scheduler_lock,
    _bot_send,
    _claim_chat,
    _collect_ops_alerts,
    _contact_candidate,
    _contact_prescreen_passed,
    _conv_chat_id,
    _ensure_scheduler_lock,
    _inactivity_decision,
    _inactivity_sweep,
    _is_working_now,
    _prune_fired_slots,
    _reconcile_scheduling_stuck,
    _reconciliation_sweep,
    _reminder_messages,
    _retention_purgeable,
    _retention_sweep,
    _scheduler_loop,
    _tenant_cfg_resolver,
    _vacancy_tenant_map,
    _within_working_hours,
    _work_windows,
)
from api.deps import (  # noqa: F401 — re-export (compat tests/consumidores)
    _audit,
    _candidate_row_from_embed,
    _page_params,
    _require_candidate_in_tenant,
    _require_vacancy_in_tenant,
    _with_cost,
)
from api.routes.candidates import (  # noqa: F401 — re-export (compat tests/consumidores)
    _psych_exam_for_role,
    _public_candidate,
)
from db import repositories as repo  # noqa: F401 — re-export (los tests parchean main.repo)
from notifications import outbox  # noqa: F401 — re-export (los tests parchean main.outbox)
from src.config import Settings, get_settings
from src.logging_config import get_logger, setup_logging
from src.observability import setup_tracing

logger = get_logger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    setup_logging()
    settings: Settings = get_settings()
    _state["settings"] = settings
    _state["tracing_on"] = setup_tracing(settings)
    # Error tracking (O-6, config-gated): sin SENTRY_DSN es un no-op.
    _state["sentry_on"] = init_sentry(settings)
    if _state["sentry_on"]:
        logger.info("Sentry activo (environment=%s)", settings.environment)
    # Observabilidad LLM (config-gated): spans OpenInference → Phoenix self-hosted.
    _state["phoenix_on"] = init_phoenix(settings)
    if _state["phoenix_on"]:
        logger.info("Phoenix activo (endpoint=%s)", settings.phoenix_endpoint)
    _state["event_loop"] = asyncio.get_running_loop()

    # Seguridad (P0): en producción, rechaza secretos por defecto/débiles ANTES de servir.
    # Fuera del try/except de abajo a propósito: debe DETENER el arranque si algo es inseguro.
    from api.auth import assert_secure_config

    assert_secure_config(settings)

    # Auth: crea el admin inicial si no hay usuarios (idempotente). No rompe el arranque
    # si la DB no está disponible (el login fallará luego con un error claro).
    try:
        from api.auth import ensure_default_admin

        ensure_default_admin(settings)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo asegurar el admin inicial (¿DB no disponible?)")

    # Bot de Telegram (opcional). Solo arranca si hay token configurado.
    # Dos modos (roadmap paso 3): POLLING (default, dev local) o WEBHOOK (prod detrás del
    # ingress). En webhook el Application se inicia SIN updater y los updates llegan por la
    # ruta POST /telegram/webhook, lo que permite replicas>1 y rolling/canary.
    _tg_app = None
    _state["telegram_mode"] = "off"
    if settings.telegram_bot_token:
        from telegram import Update

        from api.telegram_bot import build_bot_app, webhook_enabled, webhook_url

        _tg_app = build_bot_app(settings, _state)
        await _tg_app.initialize()
        await _tg_app.start()
        _state["tg_app"] = _tg_app
        if webhook_enabled(settings):
            from api.telegram_bot import resolve_webhook_secret

            await _tg_app.bot.set_webhook(
                url=webhook_url(settings),
                secret_token=resolve_webhook_secret(settings),
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            _state["telegram_mode"] = "webhook"
            logger.info("Bot de Telegram arrancado en modo webhook (%s)", webhook_url(settings))
        else:
            await _tg_app.updater.start_polling(drop_pending_updates=True)
            _state["telegram_mode"] = "polling"
            logger.info("Bot de Telegram arrancado en modo polling")
    else:
        logger.info("TELEGRAM_BOT_TOKEN vacío: el bot no arranca (solo API)")

    # Scheduler de auto-contacto (siempre activo; respeta la config de la DB).
    scheduler_task = asyncio.create_task(_scheduler_loop())
    _state["scheduler_task"] = scheduler_task
    logger.info("Scheduler de auto-contacto iniciado")

    # Servidor MCP (config-gated): el mount de /mcp es una sub-app Starlette cuyo
    # lifespan FastAPI no ejecuta — su session manager se corre aquí explícitamente.
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as _mcp_stack:
        if _mcp_server is not None:
            await _mcp_stack.enter_async_context(_mcp_server.session_manager.run())
            logger.info("Servidor MCP activo en /mcp")
        yield

    scheduler_task.cancel()
    lock_conn = _state.get("scheduler_lock_conn")
    if lock_conn is not None:
        try:  # cerrar la conexión libera el advisory lock (otra réplica puede tomarlo)
            lock_conn.close()
        except Exception:  # noqa: BLE001
            pass
    if _tg_app is not None:
        if _state.get("telegram_mode") == "webhook":
            try:  # dejar de recibir updates en esta réplica antes de bajar
                await _tg_app.bot.delete_webhook()
            except Exception:  # noqa: BLE001
                logger.exception("No se pudo borrar el webhook de Telegram")
        elif _tg_app.updater is not None and _tg_app.updater.running:
            await _tg_app.updater.stop()
        await _tg_app.stop()
        await _tg_app.shutdown()
        logger.info("Bot de Telegram detenido")
    _state.clear()


app = FastAPI(title="Agente de Selección de Talento", lifespan=lifespan)

# CORS parametrizado por settings (audit S5): en producción se configura el dominio
# real del dashboard vía CORS_ORIGINS (CSV) sin tocar código.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


# O3 (auditoría): métricas HTTP por ruta (conteo/errores/latencia) — visibles en
# /api/ops/http-metrics y en la página de observabilidad. Usa la PLANTILLA de la ruta
# (cardinalidad acotada), disponible en el scope recién después del routing.
@app.middleware("http")
async def _http_metrics_middleware(request: Request, call_next):
    import time

    from api.httpmetrics import http_metrics

    start = time.perf_counter()
    status = 500  # si call_next lanza, cuenta como error del servidor
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        if path.startswith("/api"):
            http_metrics.record(request.method, path, status, (time.perf_counter() - start) * 1000)


# O-6: request-id por request — propaga el `X-Request-ID` entrante (gateway/proxy) o
# genera uno; queda en el contextvar (los logs JSON lo incluyen) y en la respuesta.
# Declarado DESPUÉS del de métricas a propósito: el último agregado es el más externo,
# así el request_id ya está seteado cuando corre todo lo demás.
@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    import uuid

    from src.logging_config import set_request_id

    rid = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:16]
    set_request_id(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        set_request_id("-")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Recibe los updates de Telegram en modo webhook (roadmap paso 3).

    Telegram firma cada POST con el header X-Telegram-Bot-Api-Secret-Token; lo validamos
    contra el secreto configurado/derivado antes de tocar el payload. El update se encola en
    el Application ya arrancado (mismo camino que el polling: mismos handlers)."""
    from api.telegram_bot import process_webhook_update, secret_matches

    settings: Settings | None = _state.get("settings")
    tg_app = _state.get("tg_app")
    if settings is None or tg_app is None or _state.get("telegram_mode") != "webhook":
        raise HTTPException(404, "Webhook no activo")
    header = request.headers.get("x-telegram-bot-api-secret-token")
    if not secret_matches(settings, header):
        raise HTTPException(403, "Secret token inválido")
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 — payload malformado: 400, no 500
        raise HTTPException(400, "Payload inválido")
    await process_webhook_update(tg_app, data)
    return {"status": "ok"}


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings: Settings | None = _state.get("settings")
    # Modo del scheduler: expone si el agendamiento cayó a simulado por un fallo de
    # credenciales de Google ("simulated-fallback") — degradación visible, no silenciosa.
    scheduler = getattr(_state.get("service"), "scheduler", None)
    from integrations.scheduling import scheduler_mode

    mode = scheduler_mode(settings, scheduler) if settings else "unknown"
    return {
        "status": "ok",
        "telegram": bool(settings and settings.telegram_bot_token),
        "telegram_mode": _state.get("telegram_mode", "off"),
        "supabase": bool(settings and settings.supabase_url),
        "scheduler": mode,
        "scheduler_degraded": mode == "simulated-fallback",
    }


# ── Autenticación (login/me). Se quedan en main: los tests parchean main.authenticate ──

class LoginIn(BaseModel):
    email: str
    password: str


# R1 (auditoría): el login es fuerza-brutable sin límite — 5 intentos/minuto por IP.
# bcrypt encarece cada intento pero no lo impide; esta es la barrera explícita.
_login_limiter = SlidingWindowLimiter(max_calls=5, per_seconds=60)


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request) -> dict[str, Any]:
    """Autentica email+contraseña y devuelve un JWT (Bearer) + los datos del usuario."""
    ip = request.client.host if request.client else "unknown"
    if not _login_limiter.allow(f"login:{ip}"):
        raise HTTPException(429, "Demasiados intentos de acceso. Espera un minuto e inténtalo de nuevo.")
    user = authenticate(payload.email, payload.password)
    if not user:
        raise HTTPException(401, "Credenciales inválidas")
    token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        tenant_id=user["tenant_id"],
        settings=current_settings(),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name", ""),
            "role": user["role"],
            "tenant_id": user["tenant_id"],
        },
    }


@app.get("/api/auth/me")
def whoami(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Devuelve el usuario autenticado (para que el frontend valide su sesión)."""
    return user


# ── Routers por dominio (audit A2) ────────────────────────────────────────────────

from api.routes.candidates import router as candidates_router  # noqa: E402
from api.routes.observability import router as observability_router  # noqa: E402
from api.routes.recruiters import router as recruiters_router  # noqa: E402
from api.routes.settings import router as settings_router  # noqa: E402
from api.routes.vacancies import router as vacancies_router  # noqa: E402

app.include_router(vacancies_router)
app.include_router(candidates_router)
app.include_router(recruiters_router)
app.include_router(settings_router)
app.include_router(observability_router)

# ── Servidor MCP (config-gated, default off) ──────────────────────────────────────
# Herramientas read-only en /mcp para clientes LLM externos, con el MISMO JWT,
# tenancy y auditoría del dashboard. Ver api/mcp.py.
_mcp_server = None
if get_settings().mcp_enabled:
    from api.mcp import mount_mcp

    _mcp_server = mount_mcp(app)
