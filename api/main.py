"""FastAPI backend del Agente de Selección de Talento (agente_rh).

Expone:
  - El arranque del bot de Telegram (modo polling, dentro del lifespan) que conduce
    la entrevista conversacional con los candidatos.
  - Endpoints para el dashboard del reclutador (vacantes, candidatos, scorecards y
    acciones avanzar/rechazar) — se completan en la Fase 5.

Reutiliza el patrón de lifespan + CORS + logging del proyecto agente_pro, pero sin
el stack RAG/quiz/voz/uploads (podado en la Fase 0).
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

# Clave fija del advisory lock de Postgres que serializa el scheduler entre procesos
# (solo un proceso/réplica ejecuta el auto-contacto + barrido de inactividad).
_SCHEDULER_LOCK_KEY = 704127

# Raíz donde el bot guarda los documentos descargados de Telegram (CV/CUL).
_UPLOADS_ROOT = Path("uploads").resolve()


def _now_iso() -> str:
    """Timestamp UTC ISO (usado al registrar el envío del examen psicológico)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _now_local(tzname: str):
    """Hora actual en la zona dada; cae a UTC-5 (Lima, sin DST) si la zona no resuelve."""
    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tzname))
    except Exception:  # noqa: BLE001 — sin tzdata, Lima es fijo UTC-5
        return datetime.now(timezone(timedelta(hours=-5)))


def _within_working_hours(now, work_days: list[int], windows: list) -> bool:
    """True si `now` cae dentro del horario laboral (día hábil + alguna franja horaria).

    Puro y testeable. Regla del negocio: solo se contacta al candidato en horario
    laboral. `windows` es una lista de pares (inicio, fin) en HH:MM; basta con caer en
    una. Compara HH:MM como texto (zero-padded); el fin de cada franja es exclusivo."""
    if now.isoweekday() not in (work_days or [1, 2, 3, 4, 5]):
        return False
    hhmm = now.strftime("%H:%M")
    return any(str(start) <= hhmm < str(end) for start, end in (windows or []))


def _work_windows(sched: dict) -> list[tuple[str, str]]:
    """Franjas horarias de la config. Usa `work_windows` (lista de [inicio, fin]) si existe;
    si no, cae a la ventana única `work_start`/`work_end` (compatibilidad hacia atrás)."""
    wins = sched.get("work_windows")
    if wins:
        return [(w[0], w[1]) for w in wins]
    return [(sched.get("work_start", "09:00"), sched.get("work_end", "18:00"))]


def _is_working_now(settings: "Settings", tenant_id: str | None = None) -> bool:
    """¿Estamos ahora en horario laboral? Lee las franjas de la config del tenant (Fase 0.1)."""
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, tenant_id) or {}
    now = _now_local(sched.get("timezone", "America/Lima"))
    return _within_working_hours(
        now,
        sched.get("work_days", [1, 2, 3, 4, 5]),
        _work_windows(sched),
    )


def _vacancy_tenant_map() -> dict[str, str | None]:
    """Mapa {vacancy_id → tenant_id} de todas las vacantes (para resolver el tenant por ítem
    en los barridos del scheduler, que recorren candidatos/conversaciones de todos los tenants)."""
    return {v["id"]: v.get("tenant_id") for v in repo.list_vacancies()}


def _tenant_cfg_resolver(key: str, default: dict[str, Any]):
    """Devuelve `get(tenant_id) → cfg` con caché por barrido (una lectura por tenant)."""
    cache: dict[str | None, Any] = {}

    def get(tenant_id: str | None) -> dict[str, Any]:
        if tenant_id not in cache:
            cache[tenant_id] = repo.get_app_setting(key, default, tenant_id) or default
        return cache[tenant_id]

    return get


def _has_database_url() -> bool:
    """¿Hay DATABASE_URL configurado? (necesario para el advisory lock del scheduler)."""
    settings: Settings = _state.get("settings") or get_settings()
    return bool(getattr(settings, "database_url", ""))


def _acquire_scheduler_lock():
    """Intenta tomar el advisory lock del scheduler en Postgres.

    Devuelve la conexión que lo sostiene (mantenerla abierta = mantener el lock) o
    None si otro proceso/réplica ya lo tiene. Así solo UN proceso ejecuta el
    auto-contacto y el barrido de inactividad, aunque haya varias réplicas."""
    import psycopg

    from db.client import get_database_url

    try:
        conn = psycopg.connect(get_database_url(), autocommit=True)
    except Exception:  # noqa: BLE001 — sin DB no hay lock; el loop cae al modo sin-lock
        return None
    try:
        got = conn.execute("select pg_try_advisory_lock(%s)", (_SCHEDULER_LOCK_KEY,)).fetchone()[0]
    except Exception:  # noqa: BLE001
        conn.close()
        return None
    if got:
        return conn
    conn.close()
    return None


async def _ensure_scheduler_lock() -> bool:
    """True si este proceso debe ejecutar el trabajo del scheduler en este tick.

    Con DATABASE_URL: intenta/mantiene el advisory lock (uno solo gana; los demás quedan
    en standby y reintentan). Verifica que la conexión siga viva (si el activo muere, un
    standby toma el relevo). Sin DATABASE_URL: cae al modo sin-lock (un solo proceso)."""
    if not _has_database_url():
        return True
    conn = _state.get("scheduler_lock_conn")
    if conn is not None:
        try:  # ¿sigue viva la conexión que sostiene el lock?
            await asyncio.to_thread(lambda: conn.execute("select 1"))
            return True
        except Exception:  # noqa: BLE001 — se cayó; soltamos y reintentamos abajo
            logger.warning("Scheduler: se perdió la conexión del lock; se re-adquirirá")
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            _state["scheduler_lock_conn"] = None
    conn = await asyncio.to_thread(_acquire_scheduler_lock)
    if conn is not None:
        _state["scheduler_lock_conn"] = conn
        logger.info("Scheduler: advisory lock adquirido (este proceso es el activo)")
        return True
    return False  # otro proceso es el activo; standby


async def _scheduler_loop() -> None:
    """Contacta a los aptos en los horarios configurados (auto-contacto). Tick cada 30 s.

    Lee la config de la DB en cada tick (cambios desde el dashboard aplican sin reinicio).
    El trabajo bloqueante (LLM/Telegram/DB) corre en un hilo para no frenar el event loop.
    Con múltiples réplicas, solo la que sostiene el advisory lock ejecuta el trabajo."""
    fired: set[str] = set()
    while True:
        try:
            if not await _ensure_scheduler_lock():
                await asyncio.sleep(30)
                continue
            settings: Settings = _state.get("settings") or get_settings()
            # Auto-contacto por horarios configurados: la config es POR-TENANT (Fase 0.1), así
            # que se evalúa por empresa (cada una con su zona horaria y sus horas).
            for tenant in await asyncio.to_thread(repo.list_tenants):
                tid = tenant["id"]
                cfg = repo.get_app_setting("auto_contact", _DEFAULT_AUTO_CONTACT, tid) or {}
                if not cfg.get("enabled"):
                    continue
                now = _now_local(cfg.get("timezone", "America/Lima"))
                hhmm = now.strftime("%H:%M")
                slot = f"{tid}|{now.date()}|{hhmm}"
                if hhmm in (cfg.get("times") or []) and slot not in fired:
                    fired.add(slot)
                    report = await asyncio.to_thread(_contact_prescreen_passed, settings, tid)
                    logger.info("Auto-contacto %s (tenant %s) → %s", hhmm, tid, report)
            # Auto-contacto del producto: si está activo, contacta a los aptos pendientes durante
            # el horario laboral (recoge a los que un sync fuera de hora dejó en `prescreen_passed`).
            # `_contact_candidate` respeta la ventana laboral del tenant de cada candidato; idempotente.
            if settings.auto_contact_on_pass:
                report = await asyncio.to_thread(_contact_prescreen_passed, settings)
                if report.get("contacted"):
                    logger.info("Auto-contacto (horario laboral) → %s", report)
            # Barrido de inactividad de las entrevistas colgadas (recordatorios + cierre).
            sweep = await asyncio.to_thread(_inactivity_sweep, settings)
            if sweep.get("reminded") or sweep.get("finalized"):
                logger.info("Inactividad → %s", sweep)
            # Outbox: reintenta los envíos externos que fallaron (email/Telegram) con backoff.
            drained = await asyncio.to_thread(outbox.drain, settings)
            if drained.get("sent") or drained.get("retry") or drained.get("dead"):
                logger.info("Outbox → %s", drained)
            # Reconciliación: detecta y alerta estados colgados (dead-letter, reuniones sin link, etc.).
            recon = await asyncio.to_thread(_reconciliation_sweep, settings)
            if recon.get("alerts"):
                logger.info("Reconciliación → %s", recon)
            # Retención: anonimiza PII de candidatos descartados vencidos (si está activa).
            retention = await asyncio.to_thread(_retention_sweep, settings)
            if retention.get("anonymized"):
                logger.info("Retención → %s", retention)
        except Exception:  # noqa: BLE001 — el scheduler nunca debe morir
            logger.exception("Error en el tick del scheduler")
        await asyncio.sleep(30)

from src.config import Settings, get_settings
from src.logging_config import get_logger, setup_logging
from src.observability import setup_tracing

logger = get_logger("api.main")

# Estado global del proceso (settings, loop, refs al bot). Se llena en el lifespan.
_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    setup_logging()
    settings: Settings = get_settings()
    _state["settings"] = settings
    _state["tracing_on"] = setup_tracing(settings)
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
    _tg_app = None
    if settings.telegram_bot_token:
        from api.telegram_bot import build_bot_app

        _tg_app = build_bot_app(settings, _state)
        await _tg_app.initialize()
        await _tg_app.start()
        await _tg_app.updater.start_polling(drop_pending_updates=True)
        _state["tg_app"] = _tg_app
        logger.info("Bot de Telegram arrancado en modo polling")
    else:
        logger.info("TELEGRAM_BOT_TOKEN vacío: el bot no arranca (solo API)")

    # Scheduler de auto-contacto (siempre activo; respeta la config de la DB).
    scheduler_task = asyncio.create_task(_scheduler_loop())
    _state["scheduler_task"] = scheduler_task
    logger.info("Scheduler de auto-contacto iniciado")

    yield

    scheduler_task.cancel()
    lock_conn = _state.get("scheduler_lock_conn")
    if lock_conn is not None:
        try:  # cerrar la conexión libera el advisory lock (otra réplica puede tomarlo)
            lock_conn.close()
        except Exception:  # noqa: BLE001
            pass
    if _tg_app is not None:
        await _tg_app.updater.stop()
        await _tg_app.stop()
        await _tg_app.shutdown()
        logger.info("Bot de Telegram detenido")
    _state.clear()


app = FastAPI(title="Agente de Selección de Talento", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "supabase": bool(settings and settings.supabase_url),
        "scheduler": mode,
        "scheduler_degraded": mode == "simulated-fallback",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints del dashboard del reclutador
# ─────────────────────────────────────────────────────────────────────────────
from db import repositories as repo  # noqa: E402
from notifications.candidate import (  # noqa: E402
    DECISION_ADVANCE,
    DECISION_HIRED,
    DECISION_REJECT,
)
from notifications import outbox  # noqa: E402
from api.auth import (  # noqa: E402
    authenticate,
    create_access_token,
    get_current_user,
    require_role,
)


class LoginIn(BaseModel):
    email: str
    password: str


# R1 (auditoría): el login es fuerza-brutable sin límite — 5 intentos/minuto por IP.
# bcrypt encarece cada intento pero no lo impide; esta es la barrera explícita.
from api.ratelimit import SlidingWindowLimiter  # noqa: E402

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
    settings: Settings = _state.get("settings") or get_settings()
    token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        tenant_id=user["tenant_id"],
        settings=settings,
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


def _require_vacancy_in_tenant(vacancy_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Carga la vacante y verifica que pertenezca al tenant del usuario (si no, 404)."""
    vac = repo.get_vacancy(vacancy_id)
    if not vac or vac.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Vacante no encontrada")
    return vac


def _require_candidate_in_tenant(
    candidate_id: str, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Carga el candidato + su vacante y verifica el tenant (si no, 404). Devuelve (cand, vac)."""
    cand = repo.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(404, "Candidato no encontrado")
    vac = repo.get_vacancy(cand.get("vacancy_id"))
    if not vac or vac.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Candidato no encontrado")
    return cand, vac


def _audit(user: dict[str, Any], action: str, *, entity_type: str = "", entity_id: str = "", summary: str = "") -> None:
    """Registra una acción del dashboard (quién/qué/cuándo). No rompe la acción si falla (audit #8)."""
    try:
        repo.add_audit_log(
            {
                "tenant_id": user.get("tenant_id"),
                "actor_user_id": user.get("id"),
                "actor_email": user.get("email") or "",
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "summary": summary,
            }
        )
    except Exception:  # noqa: BLE001 — la auditoría no debe tumbar la acción
        logger.exception("No se pudo registrar la auditoría (%s)", action)


_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: str) -> str:
    """Valida un string "HH:MM" (24h). Lanza ValueError si no cumple (→ 422 de FastAPI)."""
    if not _HHMM_RE.match(str(value).strip()):
        raise ValueError(f"Hora inválida '{value}': usa formato HH:MM (24h)")
    return value


class QuestionIn(BaseModel):
    position: int = Field(ge=0)
    text: str
    criterion: str = ""
    weight: float = Field(default=1.0, ge=0)
    max_follow_ups: int = Field(default=1, ge=0)


class VacancyIn(BaseModel):
    title: str
    description: str = ""
    requirements: str = ""
    intro_message: str = ""
    details_message: str = ""
    company_info: str = ""
    semaphore_thresholds: dict[str, Any] = {"green_min": 75, "yellow_min": 50}
    recruiter_id: str | None = None             # RR.HH. asignado (Fase 1 + coordinación)
    lead_recruiter_id: str | None = None        # líder del proyecto (Fase 2)
    manager_recruiter_id: str | None = None     # gerencia (Fase 3)
    meeting_duration_minutes: int = Field(default=45, gt=0)  # duración de la entrevista
    # Datos del aviso de empleo (rediseño "hira"; columnas en 0012_vacancy_fields.sql).
    area: str = ""
    modality: str = "presencial"                # presencial | hibrido | remoto
    location: str = ""
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    benefits: list[str] = []
    portals: list[str] = []                     # ej. ["bumeran", "linkedin"]
    auto_agent: bool = True
    questions: list[QuestionIn] = []


class RecruiterIn(BaseModel):
    name: str
    email: str = ""
    company: str = ""
    role: str = "Reclutador"
    phone: str = ""
    telegram_chat_id: str = ""
    calendar_id: str = "primary"
    location: str = ""                          # dirección de oficina (entrevistas presenciales)
    active: bool = True


_DEFAULT_SCHEDULING = {
    "enabled": True, "provider": "simulated", "slot_minutes": 45,
    "work_days": [1, 2, 3, 4, 5], "work_start": "09:00", "work_end": "18:00",
    "work_windows": [["09:00", "18:00"]],
    "timezone": "America/Lima", "horizon_days": 7, "options": 3,
}


class SchedulingIn(BaseModel):
    enabled: bool = True
    provider: str = "simulated"             # "simulated" | "google"
    slot_minutes: int = Field(default=45, gt=0)
    work_days: list[int] = [1, 2, 3, 4, 5]  # ISO: 1=lunes .. 7=domingo
    work_start: str = "09:00"               # ventana única (compat hacia atrás)
    work_end: str = "18:00"
    work_windows: list[list[str]] = [["09:00", "18:00"]]  # franjas [inicio, fin] del auto-contacto
    timezone: str = "America/Lima"
    horizon_days: int = Field(default=7, ge=1)
    options: int = Field(default=3, ge=1, le=5)

    @field_validator("provider")
    @classmethod
    def _provider_valid(cls, v: str) -> str:
        if v not in ("simulated", "google"):
            raise ValueError("provider debe ser 'simulated' o 'google'")
        return v

    @field_validator("work_days")
    @classmethod
    def _work_days_valid(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("work_days: cada día debe estar entre 1 (lunes) y 7 (domingo)")
        return v

    @field_validator("work_start", "work_end")
    @classmethod
    def _times_valid(cls, v: str) -> str:
        return _validate_hhmm(v)

    @field_validator("work_windows")
    @classmethod
    def _windows_valid(cls, v: list[list[str]]) -> list[list[str]]:
        for w in v:
            if len(w) != 2:
                raise ValueError("work_windows: cada franja debe ser [inicio, fin]")
            _validate_hhmm(w[0])
            _validate_hhmm(w[1])
        return v


class DecisionIn(BaseModel):
    decision: str  # "advance" | "reject"


class PsychExamIn(BaseModel):
    """Credenciales del examen psicológico que RR.HH. obtiene de la plataforma externa."""
    link: str
    code: str = ""
    key: str = ""


class AttendanceIn(BaseModel):
    stage: str  # "hr" | "lead" | "manager"
    attended: str  # "attended" | "no_show"
    reschedule: bool = False  # si no asistió: reabrir horarios (True) o cerrar (False)

    @field_validator("stage")
    @classmethod
    def _stage_valid(cls, v: str) -> str:
        if v not in ("hr", "lead", "manager"):
            raise ValueError("stage debe ser 'hr', 'lead' o 'manager'")
        return v

    @field_validator("attended")
    @classmethod
    def _attended_valid(cls, v: str) -> str:
        if v not in ("attended", "no_show"):
            raise ValueError("attended debe ser 'attended' o 'no_show'")
        return v


class AdvanceStageIn(BaseModel):
    """Feedback + decisión de una etapa. Al aprobar 'hr'/'lead' se agenda la etapa siguiente."""
    stage: str  # "hr" | "lead" | "manager"
    decision: str  # "approved" | "rejected"
    feedback: str = ""
    modality: str = "onsite"  # modalidad de la SIGUIENTE etapa (lead: elegible; manager: forzado onsite)

    @field_validator("stage")
    @classmethod
    def _stage_valid(cls, v: str) -> str:
        if v not in ("hr", "lead", "manager"):
            raise ValueError("stage debe ser 'hr', 'lead' o 'manager'")
        return v

    @field_validator("decision")
    @classmethod
    def _decision_valid(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("decision debe ser 'approved' o 'rejected'")
        return v

    @field_validator("modality")
    @classmethod
    def _modality_valid(cls, v: str) -> str:
        if v not in ("virtual", "onsite"):
            raise ValueError("modality debe ser 'virtual' o 'onsite'")
        return v


_DEFAULT_AUTO_CONTACT = {"enabled": False, "times": ["11:00", "15:00"], "timezone": "America/Lima"}


class AutoContactIn(BaseModel):
    enabled: bool = False
    times: list[str] = ["11:00", "15:00"]   # horas "HH:MM" a las que se contacta
    timezone: str = "America/Lima"

    @field_validator("times")
    @classmethod
    def _times_valid(cls, v: list[str]) -> list[str]:
        return [_validate_hhmm(t) for t in v]


# Inactividad: recordar a los N minutos de silencio y cerrar tras M recordatorios.
_DEFAULT_INACTIVITY = {"enabled": True, "reminder_minutes": 2, "max_reminders": 2}


class InactivityIn(BaseModel):
    enabled: bool = True
    reminder_minutes: int = Field(default=2, ge=1)   # silencio antes de recordar / reintentar
    max_reminders: int = Field(default=2, ge=0)      # recordatorios antes de cerrar "No respondió"


# Retención de datos (Ley 29733): anonimiza la PII de candidatos descartados con más de N días.
_DEFAULT_RETENTION = {"enabled": False, "days": 180}
# Estados terminales-descartados cuya PII se anonimiza tras el período de retención.
_RETENTION_STATUSES = ["rejected", "declined", "no_response", "prescreen_rejected", "no_show"]


class RetentionIn(BaseModel):
    enabled: bool = False
    days: int = Field(default=180, ge=0)


def _candidate_row_from_embed(c: dict[str, Any]) -> dict[str, Any]:
    """Fila de candidato con semáforo/score y prescreen, para listas y pipeline.

    Consume el embed `conversations(scorecards)` de `repo.list_candidate_rows` (D1:
    cero consultas extra por candidato). Usa la conversación más reciente. PostgREST
    embebe como OBJETO las relaciones que detecta to-one (scorecards tiene unique de
    conversation_id) y como lista las to-many — se aceptan ambas formas."""
    raw = c.get("conversations") or []
    convs = [raw] if isinstance(raw, dict) else list(raw)
    convs.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    conv = convs[0] if convs else None
    cards = (conv or {}).get("scorecards")
    scorecard = cards if isinstance(cards, dict) else (cards[0] if cards else None)
    prescreen = c.get("prescreen") or {}
    return {
        "id": c["id"],
        "name": c["name"],
        "status": c["status"],
        "channel": c["channel"],
        "source": c.get("source", "telegram"),
        "created_at": c["created_at"],
        "vacancy_id": c.get("vacancy_id"),
        "conversation_id": conv["id"] if conv else None,
        "semaphore": scorecard["semaphore"] if scorecard else None,
        "total_score": scorecard["total_score"] if scorecard else None,
        "prescreen_score": prescreen.get("pre_score"),
        "prescreen_verdict": prescreen.get("verdict"),
    }


def _page_params(limit: int, offset: int) -> tuple[int, int]:
    """Sanea limit/offset de paginación (U1): 1 ≤ limit ≤ 500, offset ≥ 0."""
    return (max(1, min(limit, 500)), max(0, offset))


@app.get("/api/vacancies")
def list_vacancies(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    # D1: 3 consultas fijas (reclutadores + vacantes + conteo por estado), sin 1/vacante.
    tenant_id = user["tenant_id"]
    recruiters = {r["id"]: r for r in repo.list_recruiters(tenant_id=tenant_id)}
    vacancies = repo.list_vacancies(tenant_id=tenant_id)
    counts = repo.count_candidates_by_status([v["id"] for v in vacancies])
    out = []
    for v in vacancies:
        per = counts.get(v["id"], {})
        out.append({
            **v,
            "candidate_count": sum(per.values()),
            "stage_counts": per,
            "recruiter": recruiters.get(v.get("recruiter_id")),
        })
    return out


@app.get("/api/candidates")
def list_all_candidates(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Candidatos de las vacantes abiertas del tenant (Pipeline global), paginado.

    D1: 2 consultas fijas (vacantes + candidatos con embeds). U1: `q` busca por
    nombre; `limit/offset` paginan; `total` permite armar los controles en la UI."""
    titles = {v["id"]: v["title"] for v in repo.list_vacancies(status="open", tenant_id=user["tenant_id"])}
    limit, offset = _page_params(limit, offset)
    rows, total = repo.list_candidate_rows(
        list(titles), search=q.strip(), limit=limit, offset=offset
    )
    items = [
        {**_candidate_row_from_embed(c), "vacancy_title": titles.get(c.get("vacancy_id"), "")}
        for c in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.post("/api/vacancies", status_code=201)
def create_vacancy(
    payload: VacancyIn, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    data = payload.model_dump()
    questions = data.pop("questions", [])
    data["tenant_id"] = user["tenant_id"]
    vacancy = repo.create_vacancy(data)
    if questions:
        repo.replace_vacancy_questions(vacancy["id"], questions)
    _audit(user, "vacancy.create", entity_type="vacancy", entity_id=vacancy["id"], summary=vacancy.get("title", ""))
    return {**vacancy, "questions": repo.get_vacancy_questions(vacancy["id"])}


@app.get("/api/vacancies/{vacancy_id}")
def get_vacancy(
    vacancy_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    vacancy = _require_vacancy_in_tenant(vacancy_id, user)
    recruiter = repo.get_recruiter(vacancy["recruiter_id"]) if vacancy.get("recruiter_id") else None
    lead = repo.get_recruiter(vacancy["lead_recruiter_id"]) if vacancy.get("lead_recruiter_id") else None
    manager = repo.get_recruiter(vacancy["manager_recruiter_id"]) if vacancy.get("manager_recruiter_id") else None
    # Deep-link del bot para publicar en avisos: enruta el /start a ESTA vacante (A1).
    settings: Settings = _state.get("settings") or get_settings()
    bot_username = (settings.telegram_bot_username or "").strip().lstrip("@")
    return {
        **vacancy,
        "questions": repo.get_vacancy_questions(vacancy_id),
        "recruiter": recruiter,
        "lead_recruiter": lead,
        "manager_recruiter": manager,
        "telegram_deep_link": (
            f"https://t.me/{bot_username}?start={vacancy_id}" if bot_username else ""
        ),
    }


@app.put("/api/vacancies/{vacancy_id}")
def update_vacancy(
    vacancy_id: str,
    payload: VacancyIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    _require_vacancy_in_tenant(vacancy_id, user)
    data = payload.model_dump()
    questions = data.pop("questions", [])
    vacancy = repo.update_vacancy(vacancy_id, data)
    repo.replace_vacancy_questions(vacancy_id, questions)
    _audit(user, "vacancy.update", entity_type="vacancy", entity_id=vacancy_id, summary=vacancy.get("title", ""))
    return {**vacancy, "questions": repo.get_vacancy_questions(vacancy_id)}


@app.get("/api/vacancies/{vacancy_id}/candidates")
def list_candidates(
    vacancy_id: str,
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Candidatos de la vacante, paginados (U1) y en 1 consulta con embeds (D1)."""
    _require_vacancy_in_tenant(vacancy_id, user)
    limit, offset = _page_params(limit, offset)
    rows, total = repo.list_candidate_rows(
        [vacancy_id], search=q.strip(), limit=limit, offset=offset
    )
    items = [_candidate_row_from_embed(c) for c in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.post("/api/vacancies/{vacancy_id}/sync-applicants")
def sync_applicants_endpoint(
    vacancy_id: str, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    """Importa postulantes de la plataforma (simulada), los pre-filtra por CV y
    contacta a los aptos. Devuelve el reporte {imported, passed, rejected, contacted}."""
    _require_vacancy_in_tenant(vacancy_id, user)
    from agent.llm import MeteredLLM, build_default_llm
    from agent.sourcing_service import sync_applicants
    from integrations.sourcing import get_connector

    settings: Settings = _state.get("settings") or get_settings()
    llm = MeteredLLM(build_default_llm())
    connector = get_connector(settings)
    vacancy = repo.get_vacancy(vacancy_id)

    # Contacto automático solo si está habilitado en config (default: contacto manual por botón).
    contact_fn = None
    if settings.auto_contact_on_pass:
        def contact_fn(candidate: dict) -> bool:  # noqa: E306
            return _contact_candidate(candidate, vacancy, settings).get("contacted", False)

    report = sync_applicants(
        vacancy_id,
        llm=llm,
        connector=connector,
        pass_min=settings.prescreen_pass_min,
        contact_fn=contact_fn,
    )
    return report.as_dict()


@app.get("/api/vacancies/{vacancy_id}/metrics")
def vacancy_metrics_endpoint(
    vacancy_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _require_vacancy_in_tenant(vacancy_id, user)
    return _with_cost(repo.vacancy_metrics(vacancy_id))


@app.get("/api/metrics")
def global_metrics_endpoint(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return _with_cost(repo.global_metrics(tenant_id=user["tenant_id"]))


def _with_cost(metrics: dict[str, Any]) -> dict[str, Any]:
    settings: Settings = _state.get("settings") or get_settings()
    price = float(getattr(settings, "token_price_per_1k", 0.0) or 0.0)
    total = int((metrics.get("tokens") or {}).get("total", 0))
    metrics["est_cost"] = round(total / 1000 * price, 4) if price else 0.0
    return metrics


# Campos internos del candidato que NO deben salir en las respuestas de la API (P1: PII/IDs
# de canal). El dashboard no los usa; `cv_profile`/`documents` sí se conservan (se muestran).
_CANDIDATE_PRIVATE_FIELDS = ("channel_user_id",)


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Copia del candidato sin los identificadores internos de canal (Telegram chat id)."""
    return {k: v for k, v in candidate.items() if k not in _CANDIDATE_PRIVATE_FIELDS}


@app.get("/api/candidates/{candidate_id}")
def get_candidate_detail(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    conv = repo.get_conversation_by_candidate(candidate_id)
    scorecard = repo.get_scorecard(conv["id"]) if conv else None
    messages = repo.get_messages(conv["id"]) if conv else []

    # Backfill de etiquetas en scorecards ya guardados (per_criterion en orden de posición).
    if scorecard and scorecard.get("per_criterion"):
        questions = repo.get_vacancy_questions(candidate["vacancy_id"])
        labels = [q.get("label") or "" for q in questions]
        for i, pc in enumerate(scorecard["per_criterion"]):
            if not pc.get("label") and i < len(labels):
                pc["label"] = labels[i]

    return {
        "candidate": _public_candidate(candidate),
        "vacancy": {"id": vacancy["id"], "title": vacancy["title"]} if vacancy else None,
        "thresholds": (vacancy or {}).get("semaphore_thresholds") or {"green_min": 75, "yellow_min": 50},
        "scorecard": scorecard,
        "transcript": messages,
        "meetings": repo.list_meetings_by_candidate(candidate_id),
        "stage_feedback": repo.list_stage_feedback(candidate_id),
        "psych_exam": candidate.get("psych_exam"),
    }


def _resolve_document_path(doc: dict[str, Any]) -> Path | None:
    """Resuelve la ruta en disco de un documento, segura dentro de uploads/.

    Usa local_path; si falta o no existe (docs previos), busca el filename bajo uploads/**.
    Devuelve None si no hay un archivo válido y contenido dentro de la carpeta uploads."""
    candidates: list[Path] = []
    if doc.get("local_path"):
        candidates.append(Path(doc["local_path"]))
    filename = doc.get("filename") or ""
    if filename:
        candidates.extend(_UPLOADS_ROOT.glob(f"**/{filename}"))
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        # Anti path-traversal: debe quedar dentro de uploads/ y existir.
        if rp.is_file() and _UPLOADS_ROOT in rp.parents:
            return rp
    return None


@app.get("/api/candidates/{candidate_id}/documents/{doc_type}")
def download_candidate_document(
    candidate_id: str, doc_type: str, user: dict[str, Any] = Depends(get_current_user)
):
    """Sirve el PDF de un documento recibido (cv | cul). Fuente durable: Postgres; fallback: disco."""
    candidate, _ = _require_candidate_in_tenant(candidate_id, user)
    # 1) Contenido durable en la DB (sobrevive redeploys) — fuente de verdad.
    row = repo.get_document_content(candidate_id, doc_type)
    if row and row.get("content_b64"):
        import base64

        try:
            data = base64.b64decode(row["content_b64"])
        except Exception:  # noqa: BLE001
            data = b""
        if data:
            fname = row.get("filename") or f"{doc_type}.pdf"
            return Response(
                content=data,
                media_type=row.get("mime") or "application/pdf",
                headers={"Content-Disposition": f'inline; filename="{fname}"'},
            )
    # 2) Fallback: archivo en disco (documentos previos al almacenamiento durable).
    doc = next((d for d in (candidate.get("documents") or []) if d.get("type") == doc_type), None)
    if not doc:
        raise HTTPException(404, "Documento no encontrado")
    path = _resolve_document_path(doc)
    if not path:
        raise HTTPException(404, "El archivo no está disponible en el servidor")
    return FileResponse(
        path, media_type="application/pdf", filename=doc.get("filename") or path.name
    )


def _claim_chat(target: dict, chat: str, vacancy_id: str, channel: str) -> dict:
    """Asigna `chat` al candidato `target`, liberándolo de cualquier otro que lo tenga
    en la misma vacante y purgando la conversación/checkpoint previos de ese thread.

    Así el nuevo candidato arranca limpio y sus mensajes se atribuyen a él (no al
    ocupante anterior, que compartía el thread_id único = canal:chat)."""
    for other in repo.list_candidates(vacancy_id):
        if (
            other["id"] != target["id"]
            and other.get("channel") == channel
            and str(other.get("channel_user_id")) == str(chat)
        ):
            repo.update_candidate(other["id"], {"channel_user_id": f"freed-{other['id'][:8]}"})
    thread = f"{channel}:{chat}"
    repo.delete_thread_conversations(thread)
    repo.delete_langgraph_checkpoint(thread)
    return repo.update_candidate(target["id"], {"channel_user_id": str(chat)})


def _contact_candidate(candidate: dict, vacancy: dict, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    """Contacta a un candidato apto: inicia la conversación y envía saludo + botones por el bot.

    Idempotente por estado (el llamador debe garantizar que viene de `prescreen_passed`).
    `force=True` salta la regla de horario laboral: lo usa el botón manual de RR.HH., que puede
    contactar a cualquier hora. El auto-contacto (sync + scheduler) deja `force=False`.
    Devuelve {contacted, chat_id, note, status}."""
    service = _state.get("service")
    tg_app = _state.get("tg_app")
    loop = _state.get("event_loop")
    if not service or not tg_app or not loop:
        return {"contacted": False, "chat_id": None, "note": "El bot de Telegram no está activo.", "status": candidate["status"]}

    # Regla del negocio: el AUTO-contacto solo dispara dentro de las franjas configuradas. Fuera
    # de hora se deja en `prescreen_passed`; el barrido del scheduler lo contactará al abrir una
    # franja. El contacto manual (force=True) no se ve afectado: RR.HH. puede contactar siempre.
    if not force and not _is_working_now(settings, vacancy.get("tenant_id")):
        sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, vacancy.get("tenant_id")) or {}
        franjas = ", ".join(f"{s}-{e}" for s, e in _work_windows(sched))
        return {
            "contacted": False, "chat_id": None, "status": candidate["status"],
            "note": f"Fuera de las franjas de auto-contacto ({franjas}, L–V): se contactará en la próxima franja hábil.",
        }

    # Claim atómico ANTES de enviar: solo un disparo gana la transición prescreen_passed →
    # invited; los demás ven que ya fue contactado y se cortan sin enviar. Evita saludos
    # duplicados ante disparos concurrentes (p.ej. botón manual + auto-contacto). Si algo
    # falla después, se revierte a prescreen_passed para poder reintentar.
    claimed = repo.claim_candidate_for_contact(candidate["id"])
    if not claimed:
        return {
            "contacted": False, "chat_id": None, "status": "invited",
            "note": "El candidato ya fue contactado (otro disparo ganó la carrera).",
        }
    candidate = claimed

    try:
        chat = str(candidate.get("channel_user_id") or "")
        if not chat.lstrip("-").isdigit():
            demo = (settings.demo_telegram_chat_id or "").strip()
            if not demo:
                repo.update_candidate(candidate["id"], {"status": "prescreen_passed"})
                return {
                    "contacted": False, "chat_id": None, "status": "prescreen_passed",
                    "note": "Postulante sin chat real de Telegram. Configura DEMO_TELEGRAM_CHAT_ID para probarlo en vivo.",
                }
            candidate = _claim_chat(candidate, demo, vacancy["id"], candidate["channel"])
            chat = demo

        # Inicia la conversación (siembra el CV) y envía el saludo + botones por el bot vivo.
        from channels.telegram import send_messages

        result = service.initiate_contact(candidate, vacancy)
        fut = asyncio.run_coroutine_threadsafe(
            send_messages(tg_app.bot, int(chat), result.messages, show_consent_buttons=result.show_consent_buttons),
            loop,
        )
        fut.result(timeout=20)
    except Exception:  # noqa: BLE001 — revertir el claim para no dejar al candidato "invited" sin saludo
        repo.update_candidate(candidate["id"], {"status": "prescreen_passed"})
        raise
    return {"contacted": True, "chat_id": chat, "note": "Saludo enviado por Telegram.", "status": "invited"}


def _contact_prescreen_passed(settings: Settings, tenant_id: str | None = None) -> dict[str, int]:
    """Contacta a los candidatos `prescreen_passed` de las vacantes abiertas (de `tenant_id`,
    o de todos los tenants si es None).

    Síncrono (lo corre el scheduler en un hilo). Idempotente: cada candidato pasa a
    `invited` una sola vez; los ya contactados no se tocan. La ventana laboral se evalúa
    por el tenant de cada vacante dentro de `_contact_candidate`."""
    report = {"attempted": 0, "contacted": 0}
    for vac in repo.list_vacancies(status="open", tenant_id=tenant_id):
        for cand in repo.list_candidates(vac["id"]):
            if cand.get("status") != "prescreen_passed":
                continue
            report["attempted"] += 1
            try:
                if _contact_candidate(cand, vac, settings).get("contacted"):
                    report["contacted"] += 1
            except Exception:  # noqa: BLE001
                logger.exception("Auto-contacto falló para %s", cand.get("id"))
    return report


# ── Inactividad del candidato (recordatorios + cierre por no-respuesta) ──────────

def _inactivity_decision(idle_seconds: float, reminders_sent: int, cfg: dict[str, Any]) -> str:
    """Decide qué hacer con una conversación en silencio: wait | remind | finalize.

    Puro y testeable. Recuerda a los `reminder_minutes` de silencio; tras `max_reminders`
    recordatorios sin respuesta, finaliza."""
    threshold = max(1, int(cfg.get("reminder_minutes", 2))) * 60
    if idle_seconds < threshold:
        return "wait"
    if reminders_sent < int(cfg.get("max_reminders", 2)):
        return "remind"
    return "finalize"


def _parse_dt(value: Any):
    """Parsea un timestamptz de Supabase a datetime con tz (cae a 'ahora' si no resuelve)."""
    from datetime import datetime, timezone

    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def _conv_chat_id(conv: dict[str, Any]) -> int | None:
    """Extrae el chat_id numérico de Telegram del thread "{channel}:{chat}" (None si no aplica)."""
    _, _, chat = (conv.get("langgraph_thread_id") or "").partition(":")
    chat = chat.strip()
    return int(chat) if chat.lstrip("-").isdigit() else None


def _bot_send(chat_id: int, messages: list[str]) -> bool:
    """Envía mensajes por el bot vivo desde un hilo worker. No rompe si el bot no está."""
    tg_app = _state.get("tg_app")
    loop = _state.get("event_loop")
    if not tg_app or not loop or not messages:
        return False
    from channels.telegram import send_messages

    try:
        fut = asyncio.run_coroutine_threadsafe(
            send_messages(tg_app.bot, int(chat_id), messages, show_consent_buttons=False), loop
        )
        fut.result(timeout=20)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el mensaje de inactividad a %s", chat_id)
        return False


def _reminder_messages(conv: dict[str, Any], service) -> list[str]:
    """Texto del recordatorio según la fase (en entrevista incluye la pregunta pendiente)."""
    from agent.prompts import REMINDER_DOCS, REMINDER_GREETING, REMINDER_INTERVIEW, SCHEDULING_REMINDER
    from agent.state import PHASE_AWAITING_DOCS, PHASE_GREETING, PHASE_SCHEDULING, current_question

    state = conv.get("state")
    if state == PHASE_GREETING:
        return [REMINDER_GREETING]
    if state == PHASE_AWAITING_DOCS:
        return [REMINDER_DOCS]
    if state == PHASE_SCHEDULING:
        return [SCHEDULING_REMINDER]
    q = {}
    if service:
        q = current_question(service.runner.get_state(conv["langgraph_thread_id"])) or {}
    return [REMINDER_INTERVIEW.format(question=q.get("text", "tu última respuesta pendiente"))]


def _inactivity_sweep(settings: Settings) -> dict[str, int]:
    """Barre las conversaciones colgadas: recuerda y, si persiste el silencio, las cierra.

    Síncrono (lo corre el scheduler en un hilo). Idempotente por `reminders_sent`."""
    from datetime import datetime, timezone

    from agent.state import PHASE_AWAITING_DOCS, PHASE_GREETING, PHASE_INTERVIEWING, PHASE_SCHEDULING

    report = {"checked": 0, "reminded": 0, "finalized": 0}
    # Config POR-TENANT (Fase 0.1): resuelve el tenant de cada conversación por su vacante.
    vac_tenant = _vacancy_tenant_map()
    cfg_for = _tenant_cfg_resolver("inactivity", _DEFAULT_INACTIVITY)
    service = _state.get("service")
    now = datetime.now(timezone.utc)
    for conv in repo.list_conversations_by_states(
        [PHASE_GREETING, PHASE_INTERVIEWING, PHASE_AWAITING_DOCS, PHASE_SCHEDULING]
    ):
        cfg = cfg_for(vac_tenant.get(conv.get("vacancy_id")))
        if not cfg.get("enabled"):
            continue
        report["checked"] += 1
        idle = (now - _parse_dt(conv.get("last_activity_at"))).total_seconds()
        reminders = int(conv.get("reminders_sent", 0) or 0)
        decision = _inactivity_decision(idle, reminders, cfg)
        if decision == "wait":
            continue
        chat_id = _conv_chat_id(conv)
        if decision == "remind":
            msgs = _reminder_messages(conv, service)
            if chat_id is not None:
                _bot_send(chat_id, msgs)
            for m in msgs:
                repo.add_message(conv["id"], "assistant", m)
            repo.update_conversation(
                conv["id"], {"reminders_sent": reminders + 1, "last_activity_at": now.isoformat()}
            )
            report["reminded"] += 1
        else:  # finalize
            # En coordinación de horario no auto-cerramos: queda para que RR.HH. lo retome.
            if conv.get("state") == PHASE_SCHEDULING:
                continue
            if service:
                result = service.finalize_inactive(conv["langgraph_thread_id"])
                if chat_id is not None and result.messages:
                    _bot_send(chat_id, result.messages)
            report["finalized"] += 1
    return report


# ── Reconciliación de estados colgados (audit #7) ────────────────────────────────

# Umbral por defecto para considerar "estancada" una coordinación de horario sin reunión.
_SCHEDULING_STUCK_SECONDS = 24 * 3600


def _reconcile_scheduling_stuck(convs, meeting_conv_ids, now, threshold_seconds) -> list[str]:
    """IDs de conversaciones en `scheduling` inactivas > umbral y **sin reunión** creada.

    Puro y testeable. Señala coordinaciones de horario que quedaron estancadas (p. ej. el
    candidato no eligió y no hay reunión) para que RR.HH. las retome."""
    from agent.state import PHASE_SCHEDULING

    stuck: list[str] = []
    for c in convs:
        if c.get("state") != PHASE_SCHEDULING or c.get("id") in meeting_conv_ids:
            continue
        idle = (now - _parse_dt(c.get("last_activity_at"))).total_seconds()
        if idle >= threshold_seconds:
            stuck.append(c["id"])
    return stuck


def _reconciliation_sweep(settings: Settings) -> dict[str, int]:
    """Detecta estados colgados y los hace visibles (alertas estructuradas). Síncrono.

    (1) Envíos en dead-letter del outbox (agotaron reintentos). (2) Reuniones sin enlace
    Meet (Calendar falló). (3) Coordinaciones de horario estancadas sin reunión.
    (4) Divergencia entre la fase del checkpoint (motor) y el estado de la conversación
    (negocio) — la doble escritura no es transaccional (audit G1). No hace remediación
    automática riesgosa: alerta para que RR.HH./ops actúen. El outbox ya reintenta los
    envíos por su cuenta."""
    from datetime import datetime, timezone

    from agent.state import PHASE_SCHEDULING

    report = {
        "alerts": 0, "dead_letter": 0, "meetings_no_link": 0,
        "scheduling_stuck": 0, "state_divergence": 0,
    }
    dead = repo.count_outbox_by_status().get("failed", 0)
    if dead:
        report["dead_letter"] = dead
        report["alerts"] += 1
        logger.warning("Reconciliación: %d envío(s) en dead-letter — revisar SMTP/Telegram", dead)
    for m in repo.list_meetings_without_link():
        report["meetings_no_link"] += 1
        report["alerts"] += 1
        logger.warning(
            "Reconciliación: reunión %s sin enlace Meet (candidato %s) — recrear el evento",
            m.get("id"), m.get("candidate_id"),
        )
    convs = repo.list_conversations_by_states([PHASE_SCHEDULING])
    meeting_conv_ids = {m["conversation_id"] for m in repo.list_meetings_without_link()}
    # Reunión creada (con o sin link) también cuenta como "tiene reunión".
    meeting_conv_ids |= {
        (repo.get_meeting_by_conversation(c["id"]) or {}).get("conversation_id")
        for c in convs
    }
    stuck = _reconcile_scheduling_stuck(convs, meeting_conv_ids, datetime.now(timezone.utc), _SCHEDULING_STUCK_SECONDS)
    for conv_id in stuck:
        report["scheduling_stuck"] += 1
        report["alerts"] += 1
        logger.warning("Reconciliación: coordinación de horario estancada (conv %s) — retomar", conv_id)
    # (4) Divergencia motor↔negocio: el turno persiste el checkpoint y LUEGO proyecta a
    # Supabase; un fallo entre ambos deja la proyección desactualizada. `_sync_business` se
    # auto-repara al siguiente mensaje, pero si el candidato no vuelve a escribir la
    # divergencia es permanente e invisible — aquí se alerta (sin remediar).
    service = _state.get("service")
    if service:
        from agent.state import PHASE_AWAITING_DOCS, PHASE_GREETING, PHASE_INTERVIEWING

        for conv in repo.list_conversations_by_states(
            [PHASE_GREETING, PHASE_INTERVIEWING, PHASE_AWAITING_DOCS, PHASE_SCHEDULING]
        ):
            try:
                engine_phase = (service.runner.get_state(conv["langgraph_thread_id"]) or {}).get("phase")
            except Exception:  # noqa: BLE001 — sin checkpoint legible no hay comparación
                continue
            if engine_phase and engine_phase != conv.get("state"):
                report["state_divergence"] += 1
                report["alerts"] += 1
                logger.warning(
                    "Reconciliación: conversación %s divergente (motor=%s, negocio=%s) — revisar",
                    conv.get("id"), engine_phase, conv.get("state"),
                )
    return report


# ── Retención de datos (Ley 29733 / GDPR) ────────────────────────────────────────

def _retention_purgeable(created_at: Any, now, days: int) -> bool:
    """True si un candidato (por su antigüedad) supera el período de retención. Puro."""
    age_days = (now - _parse_dt(created_at)).total_seconds() / 86400.0
    return age_days >= max(0, days)


def _retention_sweep(settings: Settings) -> dict[str, int]:
    """Anonimiza la PII de candidatos descartados que superan el período de retención.

    Síncrono (scheduler, bajo el advisory lock). Desactivado por defecto. Conserva la fila
    (para métricas agregadas) pero borra nombre/chat/CV/documentos + la transcripción."""
    from datetime import datetime, timezone

    report = {"anonymized": 0}
    # Config POR-TENANT (Fase 0.1): cada empresa define su período/activación de retención.
    vac_tenant = _vacancy_tenant_map()
    cfg_for = _tenant_cfg_resolver("retention", _DEFAULT_RETENTION)
    now = datetime.now(timezone.utc)
    for cand in repo.list_candidates_by_statuses(_RETENTION_STATUSES):
        cfg = cfg_for(vac_tenant.get(cand.get("vacancy_id")))
        if not cfg.get("enabled"):
            continue
        days = int(cfg.get("days", 180) or 180)
        if cand.get("name") == "" and not cand.get("cv_profile"):
            continue  # ya anonimizado
        if not _retention_purgeable(cand.get("created_at"), now, days):
            continue
        conv = repo.get_conversation_by_candidate(cand["id"])
        if conv:
            repo.delete_messages(conv["id"])
            # El checkpoint de LangGraph guarda el estado serializado completo (respuestas
            # crudas + cv_profile): sin esto, la PII sobrevive a la anonimización (audit M1).
            if conv.get("langgraph_thread_id"):
                repo.delete_langgraph_checkpoint(conv["langgraph_thread_id"])
        repo.delete_candidate_documents(cand["id"])
        repo.anonymize_candidate(cand["id"])
        report["anonymized"] += 1
    if report["anonymized"]:
        logger.info("Retención: %d candidato(s) anonimizado(s)", report["anonymized"])
    return report


@app.get("/api/settings/retention")
def get_retention(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("retention", _DEFAULT_RETENTION, user["tenant_id"])


@app.put("/api/settings/retention")
def put_retention(
    payload: RetentionIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("retention", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="retention")
    return repo.get_app_setting("retention", _DEFAULT_RETENTION, user["tenant_id"])


# ── Observabilidad: auditoría + salud del outbox (solo admin) ──────────────────────

@app.get("/api/audit")
def list_audit(user: dict[str, Any] = Depends(require_role("admin"))) -> list[dict[str, Any]]:
    """Registro de acciones del dashboard del tenant (quién/qué/cuándo)."""
    return repo.list_audit_log(user["tenant_id"])


@app.get("/api/outbox")
def get_outbox_health(
    user: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Salud de los envíos salientes del tenant: conteo por estado + pendientes/dead-letters."""
    tenant_id = user["tenant_id"]
    counts = repo.count_outbox_by_status(tenant_id)
    items = repo.list_outbox(tenant_id, statuses=["pending", "failed"], limit=100)
    return {"counts": counts, "items": items}


@app.post("/api/outbox/{outbox_id}/retry")
def retry_outbox(
    outbox_id: str, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Reintenta un envío detenido (dead-letter o pendiente): lo marca vencido para el próximo drenaje."""
    from datetime import datetime, timezone

    row = repo.get_outbox(outbox_id)
    if not row or str(row.get("tenant_id")) != str(user["tenant_id"]):
        raise HTTPException(404, "Envío no encontrado")
    if row.get("status") == "sent":
        raise HTTPException(409, "El envío ya fue entregado.")
    repo.update_outbox(
        outbox_id,
        {
            "status": "pending",
            "next_attempt_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _audit(user, "outbox.retry", entity_type="outbox", entity_id=outbox_id,
           summary=str(row.get("kind", "")))
    return {"requeued": True}


@app.get("/api/settings/inactivity")
def get_inactivity(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("inactivity", _DEFAULT_INACTIVITY, user["tenant_id"])


@app.put("/api/settings/inactivity")
def put_inactivity(
    payload: InactivityIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("inactivity", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="inactivity")
    return repo.get_app_setting("inactivity", _DEFAULT_INACTIVITY, user["tenant_id"])


@app.get("/api/settings/auto-contact")
def get_auto_contact(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("auto_contact", _DEFAULT_AUTO_CONTACT, user["tenant_id"])


@app.put("/api/settings/auto-contact")
def put_auto_contact(
    payload: AutoContactIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("auto_contact", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="auto_contact")
    return repo.get_app_setting("auto_contact", _DEFAULT_AUTO_CONTACT, user["tenant_id"])


@app.post("/api/candidates/{candidate_id}/contact")
def contact_candidate(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("recruiter"))
) -> dict[str, Any]:
    """Disparador manual del primer contacto. Idempotente: solo desde `prescreen_passed`."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if candidate["status"] != "prescreen_passed":
        raise HTTPException(409, "El candidato ya fue contactado o no está apto para contactar.")
    settings: Settings = _state.get("settings") or get_settings()
    # Contacto manual de RR.HH.: acción humana explícita, válida a cualquier hora (force).
    result = _contact_candidate(candidate, vacancy, settings, force=True)
    _audit(user, "candidate.contact", entity_type="candidate", entity_id=candidate_id, summary=candidate.get("name", ""))
    # No exponer el chat_id crudo de Telegram en la respuesta HTTP (P1). Queda en logs.
    return {k: v for k, v in result.items() if k != "chat_id"}


@app.post("/api/candidates/{candidate_id}/decision")
def decide_candidate(
    candidate_id: str,
    payload: DecisionIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    if payload.decision not in (DECISION_ADVANCE, DECISION_REJECT):
        raise HTTPException(400, "decision debe ser 'advance' o 'reject'")
    settings: Settings = _state.get("settings") or get_settings()

    if payload.decision == DECISION_REJECT:
        repo.update_candidate(candidate_id, {"status": "rejected"})
        notified = outbox.deliver_candidate_notify(
            settings, candidate, payload.decision, tenant_id=user["tenant_id"]
        )
        _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
               summary=f"rechazar · {candidate.get('name', '')}")
        return {"status": "rejected", "notified": notified}

    # advance: si el agendamiento está activo, abre la coordinación del horario (en vez del
    # aviso genérico); si no, conserva el comportamiento anterior (notifica "avanza").
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"]) or {}
    service = _state.get("service")
    if sched.get("enabled") and service:
        vacancy = repo.get_vacancy(candidate["vacancy_id"])
        if not vacancy:
            raise HTTPException(404, "Vacante no encontrada")
        result = service.initiate_scheduling(candidate, vacancy)
        chat = str(candidate.get("channel_user_id") or "")
        sent = _bot_send(int(chat), result.messages) if chat.lstrip("-").isdigit() else False
        _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
               summary=f"avanzar → agendar · {candidate.get('name', '')}")
        return {
            "status": "scheduling",
            "scheduling_started": True,
            "messages_sent": sent,
            "messages": result.messages,
        }

    repo.update_candidate(candidate_id, {"status": "advanced"})
    notified = outbox.deliver_candidate_notify(
        settings, candidate, payload.decision, tenant_id=user["tenant_id"]
    )
    _audit(user, "candidate.decide", entity_type="candidate", entity_id=candidate_id,
           summary=f"avanzar · {candidate.get('name', '')}")
    return {"status": "advanced", "notified": notified}


@app.get("/api/candidates/{candidate_id}/meeting")
def get_candidate_meeting(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any] | None:
    """Reunión más reciente del candidato (o null si aún no hay)."""
    _require_candidate_in_tenant(candidate_id, user)
    return repo.get_meeting_by_candidate(candidate_id)


@app.get("/api/candidates/{candidate_id}/meetings")
def list_candidate_meetings(
    candidate_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    """Reuniones del candidato, una por etapa (hr / lead / manager)."""
    _require_candidate_in_tenant(candidate_id, user)
    return repo.list_meetings_by_candidate(candidate_id)


def _send_scheduling_messages(candidate: dict[str, Any], messages: list[str]) -> bool:
    """Envía por el bot vivo los mensajes de coordinación (propuesta de horarios)."""
    chat = str(candidate.get("channel_user_id") or "")
    return _bot_send(int(chat), messages) if chat.lstrip("-").isdigit() else False


# Modalidad forzada por etapa siguiente (gerencia es 100% presencial).
_NEXT_STAGE = {"hr": "lead", "lead": "manager"}


@app.post("/api/candidates/{candidate_id}/psych-exam")
def send_psych_exam(
    candidate_id: str,
    payload: PsychExamIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Fase 1: envía por correo el examen psicológico (link+código+clave) y lo registra."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    # R3 (auditoría): idempotencia — reenviar las MISMAS credenciales duplica el correo
    # (doble click). Con credenciales nuevas sí se permite (reemplazo legítimo).
    prev = candidate.get("psych_exam") or {}
    if prev and (prev.get("link"), prev.get("code"), prev.get("key")) == (
        payload.link, payload.code, payload.key
    ):
        raise HTTPException(409, "Ese examen ya fue enviado a este candidato.")
    settings: Settings = _state.get("settings") or get_settings()
    exam = {
        "link": payload.link,
        "code": payload.code,
        "key": payload.key,
        "sent_at": _now_iso(),
        "sent_by": user.get("email") or "",
    }
    sent = outbox.deliver_psych_exam(settings, vacancy, candidate, exam, conversation_id=None)
    repo.update_candidate(candidate_id, {"psych_exam": exam})
    _audit(user, "candidate.psych_exam", entity_type="candidate", entity_id=candidate_id,
           summary=f"examen psicológico enviado · {candidate.get('name', '')}")
    return {"sent": sent, "psych_exam": exam}


@app.post("/api/candidates/{candidate_id}/attendance")
def mark_attendance(
    candidate_id: str,
    payload: AttendanceIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """RR.HH. marca la asistencia a la entrevista de una etapa. `no_show` puede reagendar o cerrar."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    settings: Settings = _state.get("settings") or get_settings()
    conv = repo.get_conversation_by_candidate(candidate_id)
    if not conv:
        raise HTTPException(404, "El candidato no tiene una conversación")
    meeting = repo.get_meeting_by_conversation_stage(conv["id"], payload.stage)
    if not meeting:
        raise HTTPException(404, f"No hay reunión agendada en la etapa '{payload.stage}'")
    repo.set_meeting_attendance(meeting["id"], payload.attended)

    if payload.attended == "attended":
        _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
               summary=f"asistió ({payload.stage}) · {candidate.get('name', '')}")
        return {"attendance": "attended", "status": candidate.get("status")}

    # No asistió: reagendar (reabre horarios de la misma etapa) o cerrar (no_show + notifica).
    service = _state.get("service")
    if payload.reschedule and service:
        result = service.initiate_scheduling(
            candidate, vacancy, stage=payload.stage, modality=meeting.get("modality") or "virtual"
        )
        sent = _send_scheduling_messages(candidate, result.messages)
        _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
               summary=f"no asistió → reagendar ({payload.stage}) · {candidate.get('name', '')}")
        return {"attendance": "no_show", "rescheduled": True, "messages_sent": sent, "messages": result.messages}

    repo.update_candidate(candidate_id, {"status": "no_show"})
    notified = outbox.deliver_candidate_notify(
        settings, candidate, DECISION_REJECT, conversation_id=conv["id"], tenant_id=user["tenant_id"]
    )
    _audit(user, "candidate.attendance", entity_type="candidate", entity_id=candidate_id,
           summary=f"no asistió → cerrar ({payload.stage}) · {candidate.get('name', '')}")
    return {"attendance": "no_show", "status": "no_show", "notified": notified}


@app.post("/api/candidates/{candidate_id}/advance-stage")
def advance_stage(
    candidate_id: str,
    payload: AdvanceStageIn,
    user: dict[str, Any] = Depends(require_role("recruiter")),
) -> dict[str, Any]:
    """Registra el feedback + decisión de una etapa. Aprobar 'hr'/'lead' agenda la etapa siguiente;
    aprobar 'manager' contrata; rechazar cierra y notifica."""
    candidate, vacancy = _require_candidate_in_tenant(candidate_id, user)
    settings: Settings = _state.get("settings") or get_settings()
    conv = repo.get_conversation_by_candidate(candidate_id)
    repo.save_stage_feedback({
        "candidate_id": candidate_id,
        "conversation_id": conv["id"] if conv else None,
        "stage": payload.stage,
        "feedback": payload.feedback,
        "decision": payload.decision,
        "decided_by": user.get("id"),
        "decided_email": user.get("email") or "",
    })
    _audit(user, "candidate.stage_decision", entity_type="candidate", entity_id=candidate_id,
           summary=f"{payload.stage}: {payload.decision} · {candidate.get('name', '')}")

    if payload.decision == "rejected":
        repo.update_candidate(candidate_id, {"status": "rejected"})
        notified = outbox.deliver_candidate_notify(
            settings, candidate, DECISION_REJECT,
            conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
        )
        return {"status": "rejected", "notified": notified}

    # Aprobado.
    next_stage = _NEXT_STAGE.get(payload.stage)
    if next_stage is None:  # aprobar en 'manager' = contratado
        repo.update_candidate(candidate_id, {"status": "hired"})
        notified = outbox.deliver_candidate_notify(
            settings, candidate, DECISION_HIRED,
            conversation_id=conv["id"] if conv else None, tenant_id=user["tenant_id"],
        )
        return {"status": "hired", "notified": notified}

    # Agenda la etapa siguiente (lead: modalidad elegida por RR.HH.; manager: forzado presencial).
    modality = "onsite" if next_stage == "manager" else payload.modality
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"]) or {}
    service = _state.get("service")
    if not (sched.get("enabled") and service):
        raise HTTPException(409, "El agendamiento no está activo: no se puede coordinar la etapa siguiente")
    result = service.initiate_scheduling(candidate, vacancy, stage=next_stage, modality=modality)
    sent = _send_scheduling_messages(candidate, result.messages)
    status = {"lead": "lead_scheduling", "manager": "mgr_scheduling"}[next_stage]
    return {
        "status": status,
        "scheduling_started": True,
        "stage": next_stage,
        "modality": modality,
        "messages_sent": sent,
        "messages": result.messages,
    }


@app.delete("/api/candidates/{candidate_id}")
def erase_candidate(
    candidate_id: str, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Derecho al olvido (Ley 29733): borra el candidato y todos sus datos (cascada) + checkpoint."""
    cand, _ = _require_candidate_in_tenant(candidate_id, user)
    conv = repo.get_conversation_by_candidate(candidate_id)
    if conv and conv.get("langgraph_thread_id"):
        repo.delete_langgraph_checkpoint(conv["langgraph_thread_id"])
    repo.delete_candidate(candidate_id)
    _audit(user, "candidate.delete", entity_type="candidate", entity_id=candidate_id,
           summary=f"borrado (derecho al olvido) · {cand.get('name', '')}")
    return {"deleted": True}


# ── Reclutadores (roster de RR.HH.) ──────────────────────────────────────────────

# Estados "activos" de un candidato (en proceso, no descartado/terminal off-path).
_ACTIVE_STATUSES = {
    "sourced", "prescreen_passed", "invited", "consented",
    "interviewing", "finished", "scheduling", "scheduled", "advanced",
    "lead_scheduling", "lead_scheduled", "mgr_scheduling", "mgr_scheduled",
}


@app.get("/api/recruiters")
def list_recruiters(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Roster con carga de trabajo: vacantes abiertas y candidatos activos por reclutador."""
    tenant_id = user["tenant_id"]
    open_vac = repo.list_vacancies(status="open", tenant_id=tenant_id)
    # D1: un solo conteo por estado para TODAS las vacantes abiertas (sin 1/vacante).
    counts = repo.count_candidates_by_status([v["id"] for v in open_vac])
    open_count: dict[str, int] = {}
    active_count: dict[str, int] = {}
    for v in open_vac:
        rid = v.get("recruiter_id")
        if not rid:
            continue
        open_count[rid] = open_count.get(rid, 0) + 1
        per = counts.get(v["id"], {})
        active = sum(n for status, n in per.items() if status in _ACTIVE_STATUSES)
        active_count[rid] = active_count.get(rid, 0) + active
    return [
        {**r, "open_vacancies": open_count.get(r["id"], 0), "active_candidates": active_count.get(r["id"], 0)}
        for r in repo.list_recruiters(tenant_id=tenant_id)
    ]


@app.post("/api/recruiters", status_code=201)
def create_recruiter(
    payload: RecruiterIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    recruiter = repo.create_recruiter({**payload.model_dump(), "tenant_id": user["tenant_id"]})
    _audit(user, "recruiter.create", entity_type="recruiter", entity_id=recruiter["id"], summary=recruiter.get("name", ""))
    return recruiter


@app.put("/api/recruiters/{recruiter_id}")
def update_recruiter(
    recruiter_id: str,
    payload: RecruiterIn,
    user: dict[str, Any] = Depends(require_role("admin")),
) -> dict[str, Any]:
    existing = repo.get_recruiter(recruiter_id)
    if not existing or existing.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Reclutador no encontrado")
    recruiter = repo.update_recruiter(recruiter_id, payload.model_dump())
    _audit(user, "recruiter.update", entity_type="recruiter", entity_id=recruiter_id, summary=recruiter.get("name", ""))
    return recruiter


# ── Configuración de agendamiento ─────────────────────────────────────────────────

@app.get("/api/settings/scheduling")
def get_scheduling(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"])


@app.put("/api/settings/scheduling")
def put_scheduling(
    payload: SchedulingIn, user: dict[str, Any] = Depends(require_role("admin"))
) -> dict[str, Any]:
    repo.set_app_setting("scheduling", payload.model_dump(), user["tenant_id"])
    _audit(user, "settings.update", entity_type="settings", entity_id="scheduling")
    return repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, user["tenant_id"])
