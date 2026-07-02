"""Scheduler del proceso + barridos (auto-contacto, inactividad, outbox, reconciliación,
retención) — extraído de `api/main.py` (audit A2).

Un loop asyncio (tick 30 s) que, bajo un advisory lock de Postgres (solo un
proceso/réplica activo), ejecuta el trabajo recurrente. Lee la config POR-TENANT de
la DB en cada tick, así los cambios desde el dashboard aplican sin reinicio. El
trabajo bloqueante (LLM/Telegram/DB) corre en hilos para no frenar el event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from api.runtime import (
    _DEFAULT_AUTO_CONTACT,
    _DEFAULT_INACTIVITY,
    _DEFAULT_RETENTION,
    _DEFAULT_SCHEDULING,
    _RETENTION_STATUSES,
    _now_local,
    _parse_dt,
    _state,
    current_settings,
)
from db import repositories as repo
from notifications import outbox
from src.config import Settings
from src.logging_config import get_logger

logger = get_logger("api.scheduler")

# Clave fija del advisory lock de Postgres que serializa el scheduler entre procesos
# (solo un proceso/réplica ejecuta el auto-contacto + barrido de inactividad).
_SCHEDULER_LOCK_KEY = 704127


# ── Horario laboral (regla del negocio: solo se auto-contacta en horario) ─────────

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


def _is_working_now(settings: Settings, tenant_id: str | None = None) -> bool:
    """¿Estamos ahora en horario laboral? Lee las franjas de la config del tenant (Fase 0.1)."""
    sched = repo.get_app_setting("scheduling", _DEFAULT_SCHEDULING, tenant_id) or {}
    now = _now_local(sched.get("timezone", "America/Lima"))
    return _within_working_hours(
        now,
        sched.get("work_days", [1, 2, 3, 4, 5]),
        _work_windows(sched),
    )


# ── Resolución de tenant por ítem (los barridos cruzan todos los tenants) ─────────

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


# ── Advisory lock (un solo proceso ejecuta el trabajo del scheduler) ──────────────

def _has_database_url() -> bool:
    """¿Hay DATABASE_URL configurado? (necesario para el advisory lock del scheduler)."""
    return bool(getattr(current_settings(), "database_url", ""))


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


# ── Contacto de candidatos aptos ──────────────────────────────────────────────────

def _claim_chat(target: dict, chat: str, vacancy_id: str, channel: str) -> dict:
    """Asigna `chat` al candidato `target`, liberándolo de cualquier otro que lo tenga
    en la misma vacante y purgando la conversación/checkpoint previos de ese thread.

    Así el nuevo candidato arranca limpio y sus mensajes se atribuyen a él (no al
    ocupante anterior, que compartía el thread_id único = canal:chat). El trabajo
    sobre las tablas de negocio es atómico (RPC de 0022, audit D3); el checkpoint
    de LangGraph se borra aparte (otra capa de almacenamiento)."""
    thread = f"{channel}:{chat}"
    repo.claim_candidate_chat(target["id"], vacancy_id, channel, str(chat), thread)
    repo.delete_langgraph_checkpoint(thread)
    return repo.get_candidate(target["id"]) or {**target, "channel_user_id": str(chat)}


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
            delivered = _bot_send(chat_id, msgs) if chat_id is not None else False
            for m in msgs:
                repo.add_message(conv["id"], "assistant", m)
            repo.update_conversation(
                conv["id"], {"reminders_sent": reminders + 1, "last_activity_at": now.isoformat()}
            )
            # G3: la transcripción registró el recordatorio; si Telegram no lo entregó,
            # queda marcado para la alerta operativa (no afirmar entregas que no pasaron).
            if chat_id is not None and not delivered:
                repo.set_delivery_failure(conv["id"], now.isoformat())
            report["reminded"] += 1
        else:  # finalize
            # En coordinación de horario no auto-cerramos: queda para que RR.HH. lo retome.
            if conv.get("state") == PHASE_SCHEDULING:
                continue
            if service:
                result = service.finalize_inactive(conv["langgraph_thread_id"])
                if chat_id is not None and result.messages and not _bot_send(chat_id, result.messages):
                    repo.set_delivery_failure(conv["id"], now.isoformat())
            report["finalized"] += 1
    return report


# ── Reconciliación de estados colgados (audit #7 + O2) ────────────────────────────

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


def _collect_ops_alerts(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Alertas operativas estructuradas (audit #7 + O2). Síncrono.

    Las computa on-demand a partir de la DB (sin estado propio): (1) envíos en
    dead-letter del outbox (agotaron reintentos), (2) reuniones sin enlace Meet
    (Calendar falló), (3) coordinaciones de horario estancadas sin reunión,
    (4) divergencia entre la fase del checkpoint (motor) y el estado de la
    conversación (negocio) — la doble escritura no es transaccional (audit G1).
    No remedia automáticamente: alerta para que RR.HH./ops actúen; el outbox ya
    reintenta los envíos por su cuenta.

    Con `tenant_id` filtra por empresa (las consume el endpoint del dashboard);
    None = global (barrido del scheduler)."""
    from datetime import datetime, timezone

    from agent.state import PHASE_AWAITING_DOCS, PHASE_GREETING, PHASE_INTERVIEWING, PHASE_SCHEDULING

    alerts: list[dict[str, Any]] = []
    vac_tenant = _vacancy_tenant_map() if tenant_id else {}

    def in_tenant(vacancy_id: Any) -> bool:
        return tenant_id is None or vac_tenant.get(vacancy_id) == tenant_id

    dead = repo.count_outbox_by_status(tenant_id).get("failed", 0)
    if dead:
        alerts.append({
            "type": "dead_letter", "count": dead,
            "detail": f"{dead} envío(s) en dead-letter (agotaron reintentos) — revisar SMTP/Telegram y reintentar desde el outbox.",
        })
    for m in repo.list_meetings_without_link():
        if not in_tenant(m.get("vacancy_id")):
            continue
        alerts.append({
            "type": "meeting_no_link", "meeting_id": m.get("id"), "candidate_id": m.get("candidate_id"),
            "detail": f"Reunión {m.get('id')} sin enlace Meet (Calendar falló) — recrear el evento.",
        })
    convs = repo.list_conversations_by_states([PHASE_SCHEDULING])
    meeting_conv_ids = {m["conversation_id"] for m in repo.list_meetings_without_link()}
    # Reunión creada (con o sin link) también cuenta como "tiene reunión".
    meeting_conv_ids |= {
        (repo.get_meeting_by_conversation(c["id"]) or {}).get("conversation_id")
        for c in convs
    }
    stuck = _reconcile_scheduling_stuck(convs, meeting_conv_ids, datetime.now(timezone.utc), _SCHEDULING_STUCK_SECONDS)
    conv_by_id = {c["id"]: c for c in convs}
    for conv_id in stuck:
        conv = conv_by_id.get(conv_id, {})
        if not in_tenant(conv.get("vacancy_id")):
            continue
        alerts.append({
            "type": "scheduling_stuck", "conversation_id": conv_id, "candidate_id": conv.get("candidate_id"),
            "detail": f"Coordinación de horario estancada sin reunión (conv {conv_id}) — retomarla con el candidato.",
        })
    # G3: conversaciones cuya última entrega por Telegram falló y el candidato no ha
    # vuelto a interactuar desde entonces (la transcripción afirma algo no entregado).
    for conv in repo.list_delivery_failed_conversations():
        if not in_tenant(conv.get("vacancy_id")):
            continue
        failed_at = _parse_dt(conv.get("last_delivery_failed_at"))
        if failed_at < _parse_dt(conv.get("last_activity_at")):
            continue  # el candidato interactuó después: el canal volvió a funcionar
        alerts.append({
            "type": "delivery_failed", "conversation_id": conv.get("id"), "candidate_id": conv.get("candidate_id"),
            "detail": f"Entrega por Telegram fallida en la conversación {conv.get('id')} — el último mensaje no llegó al candidato.",
        })
    service = _state.get("service")
    if service:
        for conv in repo.list_conversations_by_states(
            [PHASE_GREETING, PHASE_INTERVIEWING, PHASE_AWAITING_DOCS, PHASE_SCHEDULING]
        ):
            if not in_tenant(conv.get("vacancy_id")):
                continue
            try:
                engine_phase = (service.runner.get_state(conv["langgraph_thread_id"]) or {}).get("phase")
            except Exception:  # noqa: BLE001 — sin checkpoint legible no hay comparación
                continue
            if engine_phase and engine_phase != conv.get("state"):
                alerts.append({
                    "type": "state_divergence", "conversation_id": conv.get("id"), "candidate_id": conv.get("candidate_id"),
                    "detail": f"Conversación {conv.get('id')} divergente (motor={engine_phase}, negocio={conv.get('state')}) — revisar.",
                })
    return alerts


# Tipo de alerta → clave del reporte del barrido (dead_letter agrega su count).
_ALERT_REPORT_KEY = {
    "dead_letter": "dead_letter",
    "meeting_no_link": "meetings_no_link",
    "scheduling_stuck": "scheduling_stuck",
    "state_divergence": "state_divergence",
    "delivery_failed": "delivery_failed",
}


def _reconciliation_sweep(settings: Settings) -> dict[str, int]:
    """Barrido de reconciliación del scheduler: computa las alertas y las loggea.

    Mismas señales que `GET /api/ops/alerts` (una sola fuente: `_collect_ops_alerts`)."""
    report = {
        "alerts": 0, "dead_letter": 0, "meetings_no_link": 0,
        "scheduling_stuck": 0, "state_divergence": 0, "delivery_failed": 0,
    }
    for alert in _collect_ops_alerts():
        report["alerts"] += 1
        key = _ALERT_REPORT_KEY.get(alert.get("type"), "")
        if key:
            report[key] += int(alert.get("count", 1))
        logger.warning("Reconciliación: %s", alert.get("detail", alert))
    return report


# ── Retención de datos (Ley 29733 / GDPR) ────────────────────────────────────────

def _retention_purgeable(created_at: Any, now, days: int) -> bool:
    """True si un candidato (por su antigüedad) supera el período de retención. Puro."""
    age_days = (now - _parse_dt(created_at)).total_seconds() / 86400.0
    return age_days >= max(0, days)


def _retention_reference_ts(cand: dict[str, Any]) -> Any:
    """Timestamp de referencia para la retención (audit D5): la última modificación del
    registro (updated_at, sellado por trigger al decidir/anonimizar) y no la fecha de
    alta — así el reloj corre desde el descarte, no desde la postulación."""
    return cand.get("updated_at") or cand.get("created_at")


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
        if not _retention_purgeable(_retention_reference_ts(cand), now, days):
            continue
        conv = repo.get_conversation_by_candidate(cand["id"])
        if conv:
            repo.delete_messages(conv["id"])
            # El checkpoint de LangGraph guarda el estado serializado completo (respuestas
            # crudas + cv_profile): sin esto, la PII sobrevive a la anonimización (audit M1).
            if conv.get("langgraph_thread_id"):
                repo.delete_langgraph_checkpoint(conv["langgraph_thread_id"])
        repo.delete_candidate_documents(cand["id"])
        # PII residual fuera de las tablas del candidato (audit S4): payloads del outbox
        # (correos completos) y resúmenes de auditoría (nombre) también se purgan.
        try:
            repo.delete_outbox_by_candidate(cand["id"])
            repo.scrub_audit_for_entity(cand["id"])
        except Exception:  # noqa: BLE001 — la purga extra no frena la anonimización
            logger.exception("Retención: purga de outbox/auditoría falló para %s", cand.get("id"))
        repo.anonymize_candidate(cand["id"])
        report["anonymized"] += 1
    if report["anonymized"]:
        logger.info("Retención: %d candidato(s) anonimizado(s)", report["anonymized"])
    return report


# ── Purga de checkpoints LangGraph (audit D4: higiene técnica, no PII) ────────────

# Intervalo mínimo entre purgas de checkpoints (la consulta cruza toda la tabla de
# conversaciones; correrla cada tick de 30 s sería desperdicio).
_CHECKPOINT_PURGE_INTERVAL_SECONDS = 6 * 3600


def _checkpoint_purge_sweep(settings: Settings) -> dict[str, int]:
    """Borra los checkpoints de conversaciones terminales viejas (a lo sumo cada 6 h).

    Config-gated por `checkpoint_retention_days` (0 = off) y requiere DATABASE_URL.
    Distinto de la retención de PII: esto es limpieza del almacenamiento del motor
    (el estado de una conversación terminal ya no se reanuda)."""
    import time

    days = int(getattr(settings, "checkpoint_retention_days", 0) or 0)
    if days <= 0 or not _has_database_url():
        return {"purged": 0}
    last = _state.get("checkpoint_purge_last") or 0.0
    now = time.monotonic()
    if now - last < _CHECKPOINT_PURGE_INTERVAL_SECONDS:
        return {"purged": 0}
    _state["checkpoint_purge_last"] = now
    return {"purged": repo.purge_stale_checkpoints(days)}


# ── Loop principal ────────────────────────────────────────────────────────────────

def _prune_fired_slots(fired: set[str], today) -> set[str]:
    """Purga del set de dedupe del auto-contacto los slots de fechas pasadas (audit A4).

    Puro. El slot es "{tenant}|{fecha_local}|{HH:MM}" y solo deduplica dentro de su día;
    sin la purga, el set crecía sin límite (fuga lenta). Se conserva ±1 día alrededor de
    `today` (UTC) porque la fecha del slot es local a la zona horaria de cada tenant."""
    from datetime import timedelta

    keep = {str(today + timedelta(days=d)) for d in (-1, 0, 1)}
    return {s for s in fired if s.split("|")[1] in keep}


async def _scheduler_loop() -> None:
    """Contacta a los aptos en los horarios configurados (auto-contacto). Tick cada 30 s.

    Lee la config de la DB en cada tick (cambios desde el dashboard aplican sin reinicio).
    El trabajo bloqueante (LLM/Telegram/DB) corre en un hilo para no frenar el event loop.
    Con múltiples réplicas, solo la que sostiene el advisory lock ejecuta el trabajo."""
    from datetime import datetime, timezone

    fired: set[str] = set()
    while True:
        try:
            if not await _ensure_scheduler_lock():
                await asyncio.sleep(30)
                continue
            fired = _prune_fired_slots(fired, datetime.now(timezone.utc).date())
            settings = current_settings()
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
            # Higiene: purga checkpoints LangGraph de conversaciones terminales viejas (D4).
            purged = await asyncio.to_thread(_checkpoint_purge_sweep, settings)
            if purged.get("purged"):
                logger.info("Checkpoints purgados → %s", purged)
        except Exception:  # noqa: BLE001 — el scheduler nunca debe morir
            logger.exception("Error en el tick del scheduler")
        await asyncio.sleep(30)
