"""Repositorios CRUD sobre Supabase para el agente de selección.

Funciones sencillas que devuelven dicts (lo que retorna supabase-py). Las consume
tanto el motor de entrevista (agent/) como los endpoints del reclutador (api/).
Todas son síncronas: el bot las llama desde hilos worker junto con el LLM.
"""

from __future__ import annotations

from typing import Any, Optional

from db.client import get_supabase


# ── Vacantes ──────────────────────────────────────────────────────────────────

def get_vacancy(vacancy_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("vacancies").select("*").eq("id", vacancy_id).limit(1).execute()
    return res.data[0] if res.data else None


def list_vacancies(
    status: Optional[str] = None, *, tenant_id: Optional[str] = None
) -> list[dict[str, Any]]:
    q = get_supabase().table("vacancies").select("*").order("created_at", desc=True)
    if status:
        q = q.eq("status", status)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return q.execute().data or []


def get_default_open_vacancy() -> Optional[dict[str, Any]]:
    """Primera vacante abierta — usada por el bot cuando no se indica vacante."""
    res = (
        get_supabase()
        .table("vacancies")
        .select("*")
        .eq("status", "open")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def create_vacancy(payload: dict[str, Any]) -> dict[str, Any]:
    return get_supabase().table("vacancies").insert(payload).execute().data[0]


def update_vacancy(vacancy_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return (
        get_supabase().table("vacancies").update(payload).eq("id", vacancy_id).execute().data[0]
    )


def get_vacancy_questions(vacancy_id: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("vacancy_questions")
        .select("*")
        .eq("vacancy_id", vacancy_id)
        .order("position", desc=False)
        .execute()
        .data
        or []
    )


def replace_vacancy_questions(vacancy_id: str, questions: list[dict[str, Any]]) -> None:
    """Reemplaza el set de preguntas de una vacante (usado por el dashboard).

    Atómico vía RPC (audit D3: delete+insert en una transacción — un fallo a mitad ya
    no deja la vacante sin preguntas). Sin la función (migración 0022 no aplicada),
    cae a la secuencia multi-request previa."""
    sb = get_supabase()
    try:
        sb.rpc(
            "app_replace_vacancy_questions", {"vid": vacancy_id, "qs": questions or []}
        ).execute()
        return
    except Exception:  # noqa: BLE001 — retro-compat: sin el RPC, camino previo
        pass
    sb.table("vacancy_questions").delete().eq("vacancy_id", vacancy_id).execute()
    if questions:
        rows = [{**q, "vacancy_id": vacancy_id} for q in questions]
        sb.table("vacancy_questions").insert(rows).execute()


# ── Candidatos ──────────────────────────────────────────────────────────────────

def find_candidate_by_source_ref(
    vacancy_id: str, source: str, source_ref: str
) -> Optional[dict[str, Any]]:
    """Busca al candidato por su id estable de plataforma (`source_ref`), que sobrevive
    a la reasignación de chat del contacto demo (el channel_user_id sí muta)."""
    if not source_ref:
        return None
    res = (
        get_supabase()
        .table("candidates")
        .select("*")
        .eq("vacancy_id", vacancy_id)
        .eq("source", source)
        .eq("source_ref", source_ref)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_or_create_candidate(
    vacancy_id: str,
    channel: str,
    channel_user_id: str,
    name: str = "",
    *,
    source: str = "telegram",
    source_ref: str = "",
) -> dict[str, Any]:
    sb = get_supabase()
    res = (
        sb.table("candidates")
        .select("*")
        .eq("vacancy_id", vacancy_id)
        .eq("channel", channel)
        .eq("channel_user_id", channel_user_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]
    payload = {
        "vacancy_id": vacancy_id,
        "channel": channel,
        "channel_user_id": channel_user_id,
        "name": name,
        "source": source,
    }
    if source_ref:
        payload["source_ref"] = source_ref
    try:
        return sb.table("candidates").insert(payload).execute().data[0]
    except Exception:  # noqa: BLE001 — audit A5: dos mensajes concurrentes pueden pasar
        # ambos el select vacío; el unique (vacancy_id, channel, channel_user_id) hace
        # que solo un insert gane — el perdedor relee la fila del ganador (sin duplicar).
        res = (
            sb.table("candidates")
            .select("*")
            .eq("vacancy_id", vacancy_id)
            .eq("channel", channel)
            .eq("channel_user_id", channel_user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
        raise


def add_candidate_document(candidate_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Anexa un documento (CUL/CV) al jsonb documents del candidato (idempotente por type+filename)."""
    cand = get_candidate(candidate_id) or {}
    docs = list(cand.get("documents") or [])
    key = (doc.get("type"), doc.get("filename"))
    docs = [d for d in docs if (d.get("type"), d.get("filename")) != key]
    docs.append(doc)
    return update_candidate(candidate_id, {"documents": docs})


def update_candidate(candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return (
        get_supabase().table("candidates").update(payload).eq("id", candidate_id).execute().data[0]
    )


# ── Almacenamiento durable de documentos (contenido en Postgres) ─────────────────

def save_document_content(
    candidate_id: str,
    *,
    doc_type: str,
    filename: str,
    content_b64: str,
    mime: str = "application/pdf",
    size_bytes: int = 0,
    conversation_id: Optional[str] = None,
) -> dict[str, Any]:
    """Guarda (o reemplaza) el contenido de un documento del candidato. Idempotente por (candidate, type)."""
    return (
        get_supabase()
        .table("candidate_documents")
        .upsert(
            {
                "candidate_id": candidate_id,
                "conversation_id": conversation_id,
                "type": doc_type,
                "filename": filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "content_b64": content_b64,
            },
            on_conflict="candidate_id,type",
        )
        .execute()
        .data[0]
    )


def get_document_content(candidate_id: str, doc_type: str) -> Optional[dict[str, Any]]:
    """Fila del documento (con content_b64) o None si no está en la DB."""
    res = (
        get_supabase()
        .table("candidate_documents")
        .select("*")
        .eq("candidate_id", candidate_id)
        .eq("type", doc_type)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def claim_candidate_for_contact(candidate_id: str) -> Optional[dict[str, Any]]:
    """Transición atómica `prescreen_passed → invited` (UPDATE ... WHERE status='prescreen_passed').

    Devuelve la fila actualizada si este llamador ganó el claim; `None` si otro disparo ya la
    contactó (el estado ya no era `prescreen_passed`). Postgres serializa el UPDATE, así que ante
    dos disparos concurrentes (botón manual + auto-contacto) solo uno gana → un único saludo."""
    res = (
        get_supabase()
        .table("candidates")
        .update({"status": "invited"})
        .eq("id", candidate_id)
        .eq("status", "prescreen_passed")
        .execute()
    )
    return res.data[0] if res.data else None


def claim_candidate_chat(
    target_id: str, vacancy_id: str, channel: str, chat: str, thread_id: str
) -> None:
    """Reasigna `chat` al candidato `target_id`: libera a otros que lo tengan en la
    vacante, purga la conversación del thread y asigna el chat — todo en una transacción
    (RPC de 0022, audit D3). Sin el RPC cae a la secuencia multi-request previa."""
    sb = get_supabase()
    try:
        sb.rpc(
            "app_claim_candidate_chat",
            {
                "target": target_id,
                "vid": vacancy_id,
                "chan": channel,
                "chat": str(chat),
                "thread": thread_id,
            },
        ).execute()
        return
    except Exception:  # noqa: BLE001 — retro-compat: sin el RPC, camino previo
        pass
    rows = (
        sb.table("candidates")
        .select("id")
        .eq("vacancy_id", vacancy_id)
        .eq("channel", channel)
        .eq("channel_user_id", str(chat))
        .neq("id", target_id)
        .execute()
        .data
        or []
    )
    for other in rows:
        update_candidate(other["id"], {"channel_user_id": f"freed-{other['id'][:8]}"})
    delete_thread_conversations(thread_id)
    update_candidate(target_id, {"channel_user_id": str(chat)})


def list_candidates(vacancy_id: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("candidates")
        .select("*")
        .eq("vacancy_id", vacancy_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


# Columnas de la fila de listado + conversación/scorecard EMBEBIDOS (D1: PostgREST
# resuelve los joins vía FKs en UN solo round-trip; antes eran 2 consultas por candidato).
_CANDIDATE_ROW_COLS = (
    "id,name,status,channel,source,created_at,vacancy_id,prescreen,"
    "conversations(id,created_at,scorecards(semaphore,total_score))"
)


def list_candidate_rows(
    vacancy_ids: list[str],
    *,
    search: str = "",
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Filas de candidato para listados (columnas livianas + embeds) + total exacto.

    `search` filtra por nombre (ilike, case-insensitive). `limit/offset` paginan (U1);
    el total viene del header `count=exact` de PostgREST en la misma consulta."""
    if not vacancy_ids:
        return ([], 0)
    q = (
        get_supabase()
        .table("candidates")
        .select(_CANDIDATE_ROW_COLS, count="exact")
        .in_("vacancy_id", vacancy_ids)
        .order("created_at", desc=True)
    )
    if search:
        # % y _ son comodines de ilike: se escapan para buscar el texto literal.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.ilike("name", f"%{escaped}%")
    if limit is not None:
        q = q.range(offset, offset + limit - 1)
    res = q.execute()
    return (res.data or [], res.count or 0)


def count_candidates_by_status(vacancy_ids: list[str]) -> dict[str, dict[str, int]]:
    """{vacancy_id: {status: n}} en UNA consulta liviana de 2 columnas (sin N+1)."""
    if not vacancy_ids:
        return {}
    rows = (
        get_supabase()
        .table("candidates")
        .select("vacancy_id,status")
        .in_("vacancy_id", vacancy_ids)
        .execute()
        .data
        or []
    )
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        per = out.setdefault(r["vacancy_id"], {})
        per[r.get("status", "")] = per.get(r.get("status", ""), 0) + 1
    return out


# ── Conversaciones ───────────────────────────────────────────────────────────

def get_conversation_by_thread(thread_id: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("conversations")
        .select("*")
        .eq("langgraph_thread_id", thread_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_conversation_by_candidate(candidate_id: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("conversations")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_candidate(candidate_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("candidates").select("*").eq("id", candidate_id).limit(1).execute()
    return res.data[0] if res.data else None


def get_or_create_conversation(
    candidate_id: str, vacancy_id: str, thread_id: str
) -> dict[str, Any]:
    existing = get_conversation_by_thread(thread_id)
    if existing:
        return existing
    return (
        get_supabase()
        .table("conversations")
        .insert(
            {
                "candidate_id": candidate_id,
                "vacancy_id": vacancy_id,
                "langgraph_thread_id": thread_id,
            }
        )
        .execute()
        .data[0]
    )


def delete_thread_conversations(thread_id: str) -> int:
    """Borra las conversaciones de un thread (cascade: mensajes/respuestas/scorecards).

    Se usa al reasignar un chat a otro candidato (override de demo) para liberar el
    thread_id único y evitar que los mensajes se atribuyan al ocupante anterior."""
    sb = get_supabase()
    rows = sb.table("conversations").select("id").eq("langgraph_thread_id", thread_id).execute().data or []
    for r in rows:
        sb.table("conversations").delete().eq("id", r["id"]).execute()
    return len(rows)


def delete_langgraph_checkpoint(thread_id: str) -> None:
    """Borra el checkpoint durable de LangGraph de un thread (arranque limpio). No lanza."""
    import psycopg

    from db.client import get_database_url

    try:
        with psycopg.connect(get_database_url(), autocommit=True) as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                try:
                    conn.execute(f"delete from {table} where thread_id = %s", (thread_id,))
                except Exception:  # noqa: BLE001 — la tabla puede no existir aún
                    pass
    except Exception:  # noqa: BLE001
        pass


def set_delivery_failure(conversation_id: str, when_iso: Optional[str]) -> None:
    """Marca (o limpia con None) la última entrega fallida por Telegram (audit G3).

    Fail-safe: sin la columna (migración 0022 no aplicada) no rompe el turno."""
    try:
        get_supabase().table("conversations").update(
            {"last_delivery_failed_at": when_iso}
        ).eq("id", conversation_id).execute()
    except Exception:  # noqa: BLE001
        pass


def list_delivery_failed_conversations() -> list[dict[str, Any]]:
    """Conversaciones con una entrega Telegram fallida marcada (para las alertas ops)."""
    try:
        return (
            get_supabase()
            .table("conversations")
            .select("*")
            .not_.is_("last_delivery_failed_at", "null")
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 — sin la columna todavía, no hay alertas
        return []


def purge_stale_checkpoints(days: int) -> int:
    """Borra los checkpoints LangGraph de conversaciones terminales con más de `days`
    días sin actividad (audit D4: crecían sin límite). Terminal = conversación `closed`
    o candidato en estado final (rechazado/contratado/no respondió/no asistió).

    Corre por conexión directa (DATABASE_URL, mismas tablas que PostgresSaver).
    Devuelve cuántos threads se purgaron; 0 y silencio si algo falla."""
    import psycopg

    from db.client import get_database_url

    stale_sql = """
        select c.langgraph_thread_id
          from conversations c
          join candidates k on k.id = c.candidate_id
         where coalesce(c.last_activity_at, c.created_at) < now() - make_interval(days => %s)
           and (c.state = 'closed'
                or k.status in ('rejected', 'hired', 'no_show', 'no_response', 'declined'))
    """
    try:
        with psycopg.connect(get_database_url(), autocommit=True) as conn:
            threads = [r[0] for r in conn.execute(stale_sql, (max(1, days),)).fetchall()]
            purged = 0
            for thread in threads:
                found = False
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    try:
                        cur = conn.execute(
                            f"delete from {table} where thread_id = %s", (thread,)
                        )
                        found = found or bool(cur.rowcount)
                    except Exception:  # noqa: BLE001 — la tabla puede no existir aún
                        pass
                purged += int(found)
            return purged
    except Exception:  # noqa: BLE001 — la purga es higiene, nunca tumba el scheduler
        return 0


def list_conversations_by_states(states: list[str]) -> list[dict[str, Any]]:
    """Conversaciones cuyo estado del flujo está en `states` (para el barrido de inactividad)."""
    if not states:
        return []
    return (
        get_supabase()
        .table("conversations")
        .select("*")
        .in_("state", states)
        .execute()
        .data
        or []
    )


def update_conversation(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return (
        get_supabase()
        .table("conversations")
        .update(payload)
        .eq("id", conversation_id)
        .execute()
        .data[0]
    )


# ── Transiciones de fase (audit G4: tiempo-por-estado + reconstrucción del flujo) ─

def add_state_transition(conversation_id: str, from_state: str, to_state: str) -> None:
    """Registra un cambio de fase de la conversación. Fail-safe: nunca rompe el turno."""
    try:
        get_supabase().table("state_transitions").insert(
            {
                "conversation_id": conversation_id,
                "from_state": from_state or "",
                "to_state": to_state,
            }
        ).execute()
    except Exception:  # noqa: BLE001 — sin la tabla (0022) el flujo sigue igual
        pass


def list_state_transitions(conversation_id: str) -> list[dict[str, Any]]:
    try:
        return (
            get_supabase()
            .table("state_transitions")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001
        return []


# ── Mensajes ─────────────────────────────────────────────────────────────────

def add_message(conversation_id: str, role: str, content: str) -> dict[str, Any]:
    return (
        get_supabase()
        .table("messages")
        .insert({"conversation_id": conversation_id, "role": role, "content": content})
        .execute()
        .data[0]
    )


def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )


# ── Respuestas evaluadas ──────────────────────────────────────────────────────

def upsert_answer(
    conversation_id: str,
    question_id: str,
    raw_answer: str,
    score: Optional[float],
    justification: str,
    follow_up_count: int,
) -> dict[str, Any]:
    return (
        get_supabase()
        .table("answers")
        .upsert(
            {
                "conversation_id": conversation_id,
                "question_id": question_id,
                "raw_answer": raw_answer,
                "score": score,
                "justification": justification,
                "follow_up_count": follow_up_count,
            },
            on_conflict="conversation_id,question_id",
        )
        .execute()
        .data[0]
    )


def get_answers(conversation_id: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("answers")
        .select("*")
        .eq("conversation_id", conversation_id)
        .execute()
        .data
        or []
    )


# ── Scorecard ────────────────────────────────────────────────────────────────

def save_scorecard(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = {**payload, "conversation_id": conversation_id}
    try:
        return (
            get_supabase()
            .table("scorecards")
            .upsert(row, on_conflict="conversation_id")
            .execute()
            .data[0]
        )
    except Exception:
        # Retro-compat: sin la columna `prompt_version` (migración 0021), guarda lo básico.
        row.pop("prompt_version", None)
        return (
            get_supabase()
            .table("scorecards")
            .upsert(row, on_conflict="conversation_id")
            .execute()
            .data[0]
        )


def get_scorecard(conversation_id: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("scorecards")
        .select("*")
        .eq("conversation_id", conversation_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


# ── Reuniones agendadas (entrevista de la fase 2) ───────────────────────────────

def save_meeting(payload: dict[str, Any]) -> dict[str, Any]:
    """Inserta/actualiza la reunión de una conversación+etapa (idempotente por (conversation_id, stage))."""
    return (
        get_supabase()
        .table("meetings")
        .upsert(payload, on_conflict="conversation_id,stage")
        .execute()
        .data[0]
    )


def update_meeting(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Actualiza campos de una reunión ya registrada (p. ej. enlace/event_id tras crear el evento)."""
    return (
        get_supabase()
        .table("meetings")
        .update(payload)
        .eq("id", meeting_id)
        .execute()
        .data[0]
    )


def get_meeting_by_conversation_stage(conversation_id: str, stage: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("meetings")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("stage", stage)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_meetings_by_candidate(candidate_id: str) -> list[dict[str, Any]]:
    """Todas las reuniones del candidato (una por etapa), en orden cronológico."""
    return (
        get_supabase()
        .table("meetings")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )


def set_meeting_attendance(meeting_id: str, attendance: str) -> dict[str, Any]:
    return (
        get_supabase()
        .table("meetings")
        .update({"attendance": attendance})
        .eq("id", meeting_id)
        .execute()
        .data[0]
    )


def get_meeting_by_conversation(conversation_id: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("meetings")
        .select("*")
        .eq("conversation_id", conversation_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_meetings_without_link() -> list[dict[str, Any]]:
    """Reuniones agendadas sin enlace Meet (Calendar falló) — para la reconciliación.

    Excluye las presenciales (`modality=onsite`): esas no llevan Meet por diseño
    (multi-etapa 0019) y marcarlas sería un falso positivo en las alertas."""
    return (
        get_supabase()
        .table("meetings")
        .select("*")
        .eq("meet_link", "")
        .eq("status", "scheduled")
        .neq("modality", "onsite")
        .execute()
        .data
        or []
    )


def get_meeting_by_candidate(candidate_id: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("meetings")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


# ── Reclutadores (roster de RR.HH.) ─────────────────────────────────────────────

def list_recruiters(
    active_only: bool = False, *, tenant_id: Optional[str] = None
) -> list[dict[str, Any]]:
    q = get_supabase().table("recruiters").select("*").order("created_at", desc=False)
    if active_only:
        q = q.eq("active", True)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return q.execute().data or []


def get_recruiter(recruiter_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("recruiters").select("*").eq("id", recruiter_id).limit(1).execute()
    return res.data[0] if res.data else None


def create_recruiter(payload: dict[str, Any]) -> dict[str, Any]:
    return get_supabase().table("recruiters").insert(payload).execute().data[0]


def update_recruiter(recruiter_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return (
        get_supabase().table("recruiters").update(payload).eq("id", recruiter_id).execute().data[0]
    )


# ── Configuración de la app (key/value editable desde el dashboard) ──────────────

def get_app_setting(
    key: str, default: Optional[Any] = None, tenant_id: Optional[str] = None
) -> Any:
    """Config del tenant (Fase 0.1). Sin fila para ese tenant → `default` (defaults del código)."""
    q = get_supabase().table("app_settings").select("value").eq("key", key)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    res = q.limit(1).execute()
    return res.data[0]["value"] if res.data else default


def set_app_setting(key: str, value: Any, tenant_id: str) -> dict[str, Any]:
    return (
        get_supabase()
        .table("app_settings")
        .upsert(
            {"key": key, "value": value, "tenant_id": tenant_id},
            on_conflict="tenant_id,key",
        )
        .execute()
        .data[0]
    )


def list_tenants() -> list[dict[str, Any]]:
    """Todas las empresas cliente (para los barridos por-tenant del scheduler)."""
    return (
        get_supabase()
        .table("tenants")
        .select("id,slug,name,active")
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )


# ── Métricas: uso de tokens del LLM ────────────────────────────────────────────

def record_usage(
    stage: str,
    model: str,
    tokens: dict[str, int],
    *,
    vacancy_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    prompt_version: str = "",
) -> None:
    """Registra el uso de tokens + latencia/errores de una etapa. No rompe el flujo si falla."""
    total = int(tokens.get("total_tokens", 0) or 0)
    if (
        total <= 0
        and not tokens.get("input_tokens")
        and not tokens.get("output_tokens")
        and not tokens.get("errors")  # un fallo con 0 tokens TAMBIÉN es señal (fallback)
        and not tokens.get("duration_ms")  # latencia sola también (stage="turn", O-3)
    ):
        return
    base = {
        "vacancy_id": vacancy_id,
        "candidate_id": candidate_id,
        "conversation_id": conversation_id,
        "stage": stage,
        "model": model or "",
        "input_tokens": int(tokens.get("input_tokens", 0) or 0),
        "output_tokens": int(tokens.get("output_tokens", 0) or 0),
        "total_tokens": total,
    }
    extra = {  # columnas de 0020/0021; si la migración no está, cae al insert base
        "calls": int(tokens.get("calls", 0) or 0),
        "errors": int(tokens.get("errors", 0) or 0),
        "duration_ms": int(tokens.get("duration_ms", 0) or 0),
        "prompt_version": prompt_version or "",
    }
    try:
        get_supabase().table("llm_usage").insert({**base, **extra}).execute()
    except Exception:  # noqa: BLE001 — retro-compat: sin las columnas nuevas, registra lo básico
        try:
            get_supabase().table("llm_usage").insert(base).execute()
        except Exception:  # noqa: BLE001 — las métricas no deben tumbar la conversación
            pass


def record_traces(
    traces: list[dict[str, Any]],
    *,
    vacancy_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    model: str = "",
    prompt_version: str = "",
) -> None:
    """Persiste las trazas LLM del turno (prompt/respuesta por llamada — O-1).

    Best-effort como `record_usage`: las trazas no deben tumbar la conversación
    (tabla ausente si la migración 0024 no está aplicada, DB caída, etc.)."""
    if not traces:
        return
    rows = [
        {
            "vacancy_id": vacancy_id,
            "candidate_id": candidate_id,
            "conversation_id": conversation_id,
            "stage": t.get("stage", ""),
            # Routing de costos: cada traza puede traer su propio modelo; cae al general.
            "model": t.get("model") or model or "",
            "prompt_version": prompt_version or "",
            "prompt_text": t.get("prompt", "") or "",
            "response_text": t.get("response"),
            "error": t.get("error"),
            "duration_ms": int(t.get("duration_ms", 0) or 0),
        }
        for t in traces
    ]
    try:
        get_supabase().table("llm_traces").insert(rows).execute()
    except Exception:  # noqa: BLE001 — observabilidad, nunca rompe el flujo
        pass


def list_llm_traces(candidate_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Trazas LLM de un candidato, de la más reciente a la más antigua."""
    return (
        get_supabase().table("llm_traces").select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def list_llm_traces_by_stage(stage: str, limit: int = 50) -> list[dict[str, Any]]:
    """Trazas recientes de una etapa con respuesta (para el juez de groundedness — O-5)."""
    return (
        get_supabase().table("llm_traces")
        .select("id,candidate_id,stage,model,prompt_version,prompt_text,response_text,created_at")
        .eq("stage", stage)
        .not_.is_("response_text", "null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def list_llm_traces_by_stage_since(stage: str, since_iso: str, limit: int = 500) -> list[dict[str, Any]]:
    """Trazas de una etapa con respuesta desde `since_iso`, con `vacancy_id` para mapear al
    tenant (barrido de calidad — paso 4). De la más reciente a la más antigua."""
    return (
        get_supabase().table("llm_traces")
        .select("id,vacancy_id,candidate_id,stage,model,prompt_text,response_text,created_at")
        .eq("stage", stage)
        .not_.is_("response_text", "null")
        .gte("created_at", since_iso)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def delete_llm_traces_by_candidate(candidate_id: str) -> None:
    """Purga las trazas de un candidato (retención/erasure: los prompts llevan PII)."""
    get_supabase().table("llm_traces").delete().eq("candidate_id", candidate_id).execute()


# ── Métricas de calidad (signo vital — paso 4) ────────────────────────────────

def save_quality_metric(
    tenant_id: str, metric: str, day: str, rate: float, sample_size: int, threshold: float
) -> None:
    """Upsert idempotente de la métrica de calidad del día (tenant, metric, day)."""
    get_supabase().table("quality_metrics").upsert(
        {
            "tenant_id": tenant_id,
            "metric": metric,
            "day": day,
            "rate": rate,
            "sample_size": sample_size,
            "threshold": threshold,
        },
        on_conflict="tenant_id,metric,day",
    ).execute()


def list_quality_metrics(tenant_id: str, limit: int = 60) -> list[dict[str, Any]]:
    """Métricas de calidad recientes de un tenant (todas las métricas), del día más reciente
    al más antiguo — para la tendencia del dashboard."""
    return (
        get_supabase().table("quality_metrics").select("*")
        .eq("tenant_id", tenant_id)
        .order("day", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def _usage_rows(vacancy_id: Optional[str] = None) -> list[dict[str, Any]]:
    q = get_supabase().table("llm_usage").select("*")
    if vacancy_id:
        q = q.eq("vacancy_id", vacancy_id)
    return q.execute().data or []


# Etapa sintética (O-3): una fila por turno del candidato con la latencia end-to-end
# (mensaje entrante → respuestas persistidas), sin tokens. Se excluye de los agregados
# de tokens/llamadas LLM y se resume solo en el bloque `latency`.
TURN_STAGE = "turn"


def _percentile(sorted_samples: list[float], q: float) -> float:
    """Percentil nearest-rank sobre muestras YA ordenadas (puro, sin numpy)."""
    if not sorted_samples:
        return 0.0
    rank = max(1, int(-(-q * len(sorted_samples) // 100)))  # ceil(q/100 * n)
    return sorted_samples[rank - 1]


def _latency_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Latencia por etapa con percentiles (O-3), desde las filas de `llm_usage`.

    Cada fila trae la SUMA de `duration_ms` de sus `calls` en ese turno; la muestra
    por llamada es duration_ms/calls, ponderada por `calls`. Incluye la etapa
    sintética "turn" (latencia end-to-end del turno del candidato)."""
    samples: dict[str, list[float]] = {}
    for r in rows:
        calls = int(r.get("calls", 0) or 0)
        duration = int(r.get("duration_ms", 0) or 0)
        if calls <= 0:
            continue
        samples.setdefault(r.get("stage") or "?", []).extend([duration / calls] * calls)
    out: dict[str, dict[str, int]] = {}
    for stage, vals in samples.items():
        vals.sort()
        out[stage] = {
            "calls": len(vals),
            "avg_ms": round(sum(vals) / len(vals)),
            "p50_ms": round(_percentile(vals, 50)),
            "p95_ms": round(_percentile(vals, 95)),
            "p99_ms": round(_percentile(vals, 99)),
        }
    return out


def _aggregate_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Las filas "turn" son solo-latencia: fuera de los agregados de tokens/llamadas LLM.
    llm_rows = [r for r in rows if (r.get("stage") or "") != TURN_STAGE]
    total = sum(int(r.get("total_tokens", 0) or 0) for r in llm_rows)
    inp = sum(int(r.get("input_tokens", 0) or 0) for r in llm_rows)
    out = sum(int(r.get("output_tokens", 0) or 0) for r in llm_rows)
    by_stage: dict[str, int] = {}
    # Desglose por modelo (O-2): base del costo real — cada modelo tiene su precio.
    by_model: dict[str, dict[str, int]] = {}
    for r in llm_rows:
        by_stage[r.get("stage", "?")] = by_stage.get(r.get("stage", "?"), 0) + int(r.get("total_tokens", 0) or 0)
        m = by_model.setdefault(r.get("model") or "?", {"input": 0, "output": 0, "total": 0})
        m["input"] += int(r.get("input_tokens", 0) or 0)
        m["output"] += int(r.get("output_tokens", 0) or 0)
        m["total"] += int(r.get("total_tokens", 0) or 0)
    # Observabilidad O1: llamadas, errores (≈ fallbacks) y latencia media por llamada.
    calls = sum(int(r.get("calls", 0) or 0) for r in llm_rows)
    errors = sum(int(r.get("errors", 0) or 0) for r in llm_rows)
    duration = sum(int(r.get("duration_ms", 0) or 0) for r in llm_rows)
    return {
        "total": total, "input": inp, "output": out, "by_stage": by_stage, "by_model": by_model,
        "calls": calls, "errors": errors, "duration_ms": duration,
        "avg_ms": round(duration / calls) if calls else 0,
        # O-3: percentiles por etapa (incluida "turn" = latencia end-to-end del turno).
        "latency": _latency_summary(rows),
    }


def save_http_snapshot(rows: list[dict[str, Any]]) -> None:
    """Persiste un snapshot de métricas HTTP por ruta (O-6). Best-effort: nunca rompe."""
    if not rows:
        return
    try:
        get_supabase().table("http_metrics_snapshots").insert(rows).execute()
    except Exception:  # noqa: BLE001 — observabilidad, no tumba el scheduler
        pass


def prune_http_snapshots(before_iso: str) -> None:
    """Poda snapshots HTTP anteriores al instante dado (retención O-6). Best-effort."""
    try:
        get_supabase().table("http_metrics_snapshots").delete().lt("taken_at", before_iso).execute()
    except Exception:  # noqa: BLE001
        pass


def usage_rows_since(since_iso: str) -> list[dict[str, Any]]:
    """Filas de `llm_usage` desde un instante (gasto del mes — O-2; SLA de latencia — O-4)."""
    return (
        get_supabase().table("llm_usage")
        .select("vacancy_id,model,stage,calls,duration_ms,input_tokens,output_tokens,total_tokens")
        .gte("created_at", since_iso)
        .execute()
        .data
        or []
    )


# ── Métricas: embudo de candidatos ──────────────────────────────────────────────

def _funnel(candidates: list[dict[str, Any]]) -> dict[str, int]:
    """Cuenta candidatos por etapa del embudo de postulación."""
    funnel = {
        "imported": 0,
        "prescreen_passed": 0,
        "prescreen_rejected": 0,
        "invited": 0,
        "interviewing": 0,
        "finished": 0,
        "advanced": 0,
        "rejected": 0,
    }
    for c in candidates:
        status = c.get("status", "")
        # "importados" = todo lo que vino del sourcing (tiene un verdict de CV).
        verdict = (c.get("prescreen") or {}).get("verdict")
        if verdict:
            funnel["imported"] += 1
            if verdict == "reject":
                funnel["prescreen_rejected"] += 1
            else:
                funnel["prescreen_passed"] += 1
        if status == "invited":
            funnel["invited"] += 1
        elif status == "interviewing":
            funnel["interviewing"] += 1
        elif status == "finished":
            funnel["finished"] += 1
        elif status == "advanced":
            funnel["advanced"] += 1
        elif status == "rejected":
            funnel["rejected"] += 1
    return funnel


def vacancy_metrics(vacancy_id: str) -> dict[str, Any]:
    cands = list_candidates(vacancy_id)
    return {"funnel": _funnel(cands), "tokens": _aggregate_tokens(_usage_rows(vacancy_id))}


def global_metrics(*, tenant_id: Optional[str] = None) -> dict[str, Any]:
    if tenant_id:
        cands: list[dict[str, Any]] = []
        for vac in list_vacancies(tenant_id=tenant_id):
            cands.extend(list_candidates(vac["id"]))
        rows: list[dict[str, Any]] = []
        for vac in list_vacancies(tenant_id=tenant_id):
            rows.extend(_usage_rows(vac["id"]))
        return {"funnel": _funnel(cands), "tokens": _aggregate_tokens(rows)}
    cands = get_supabase().table("candidates").select("*").execute().data or []
    return {"funnel": _funnel(cands), "tokens": _aggregate_tokens(_usage_rows())}


# ── Outbox durable (envíos salientes con reintentos + dead-letter) ───────────────

def enqueue_outbox(row: dict[str, Any]) -> dict[str, Any]:
    """Encola un envío saliente pendiente (email/telegram) para reintento durable."""
    return get_supabase().table("outbox").insert(row).execute().data[0]


def list_due_outbox(now_iso: str, limit: int = 50) -> list[dict[str, Any]]:
    """Envíos pendientes ya vencidos (next_attempt_at <= ahora), del más antiguo al más nuevo."""
    return (
        get_supabase()
        .table("outbox")
        .select("*")
        .eq("status", "pending")
        .lte("next_attempt_at", now_iso)
        .order("next_attempt_at", desc=False)
        .limit(limit)
        .execute()
        .data
        or []
    )


def update_outbox(outbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return get_supabase().table("outbox").update(payload).eq("id", outbox_id).execute().data[0]


def count_outbox_by_status(tenant_id: Optional[str] = None) -> dict[str, int]:
    """Conteo por estado (pending/sent/failed) para el diagnóstico de entregas.

    Sin `tenant_id` → global (reconciliación process-wide). Con `tenant_id` → aislado por empresa."""
    q = get_supabase().table("outbox").select("status")
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    rows = q.execute().data or []
    out: dict[str, int] = {}
    for r in rows:
        out[r.get("status", "?")] = out.get(r.get("status", "?"), 0) + 1
    return out


def list_outbox(
    tenant_id: str, statuses: Optional[list[str]] = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Envíos del tenant (del más reciente al más antiguo), opcionalmente filtrados por estado."""
    q = get_supabase().table("outbox").select("*").eq("tenant_id", tenant_id)
    if statuses:
        q = q.in_("status", statuses)
    return q.order("created_at", desc=True).limit(limit).execute().data or []


def get_outbox(outbox_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("outbox").select("*").eq("id", outbox_id).limit(1).execute()
    return res.data[0] if res.data else None


def delete_outbox_by_candidate(candidate_id: str) -> None:
    """Borra los envíos del candidato (payloads con PII — audit S4). La FK cascade de
    0022 cubre el erasure; esta llamada explícita cubre la retención y entornos sin 0022."""
    get_supabase().table("outbox").delete().eq("candidate_id", candidate_id).execute()


# ── Tenants (empresas cliente) ──────────────────────────────────────────────────

def get_tenant(tenant_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("tenants").select("*").eq("id", tenant_id).limit(1).execute()
    return res.data[0] if res.data else None


def get_tenant_by_slug(slug: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("tenants").select("*").eq("slug", slug).limit(1).execute()
    return res.data[0] if res.data else None


def create_tenant(name: str, slug: str) -> dict[str, Any]:
    return get_supabase().table("tenants").insert({"name": name, "slug": slug}).execute().data[0]


# ── Usuarios del dashboard (auth) ───────────────────────────────────────────────

def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    res = (
        get_supabase()
        .table("users")
        .select("*")
        .eq("email", email.strip().lower())
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    res = get_supabase().table("users").select("*").eq("id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


def count_users() -> int:
    res = get_supabase().table("users").select("id").limit(1).execute()
    return len(res.data or [])


def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    data = {**payload, "email": str(payload.get("email", "")).strip().lower()}
    return get_supabase().table("users").insert(data).execute().data[0]


def list_users(*, tenant_id: Optional[str] = None) -> list[dict[str, Any]]:
    q = get_supabase().table("users").select("*").order("created_at", desc=False)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    return q.execute().data or []


# ── Auditoría de acciones del dashboard (quién/qué/cuándo) ───────────────────────

def add_audit_log(row: dict[str, Any]) -> dict[str, Any]:
    return get_supabase().table("audit_log").insert(row).execute().data[0]


def scrub_audit_for_entity(entity_id: str) -> None:
    """Vacía los resúmenes de auditoría de una entidad (audit S4: el summary lleva el
    nombre del candidato y sobrevivía al erasure). Conserva quién/qué/cuándo."""
    get_supabase().table("audit_log").update({"summary": ""}).eq(
        "entity_id", entity_id
    ).execute()


def list_audit_log(tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("audit_log")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


# ── Feedback + decisión por etapa (líder / gerencia / RR.HH.) ────────────────────

def save_stage_feedback(row: dict[str, Any]) -> dict[str, Any]:
    return get_supabase().table("stage_feedback").insert(row).execute().data[0]


def list_stage_feedback(candidate_id: str) -> list[dict[str, Any]]:
    return (
        get_supabase()
        .table("stage_feedback")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )


# ── Borrado / anonimización de candidatos (Ley 29733: erasure + retención) ───────

def delete_candidate(candidate_id: str) -> None:
    """Borra el candidato y, por cascada (FK), su conversación/mensajes/respuestas/scorecard/reunión."""
    get_supabase().table("candidates").delete().eq("id", candidate_id).execute()


def anonymize_candidate(candidate_id: str) -> dict[str, Any]:
    """Borra la PII del candidato conservando la fila (para métricas agregadas)."""
    return update_candidate(
        candidate_id,
        {
            "name": "",
            "channel_user_id": f"anon-{candidate_id[:8]}",
            "cv_profile": {},
            "documents": [],
        },
    )


def list_candidates_by_statuses(statuses: list[str]) -> list[dict[str, Any]]:
    if not statuses:
        return []
    return (
        get_supabase().table("candidates").select("*").in_("status", statuses).execute().data or []
    )


def delete_messages(conversation_id: str) -> None:
    """Borra la transcripción de una conversación (retención de datos)."""
    get_supabase().table("messages").delete().eq("conversation_id", conversation_id).execute()


def delete_candidate_documents(candidate_id: str) -> None:
    """Borra el contenido durable de los documentos del candidato (retención de datos)."""
    get_supabase().table("candidate_documents").delete().eq("candidate_id", candidate_id).execute()
