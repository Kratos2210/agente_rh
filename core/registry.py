"""SQLite registry for documents and per-document chat history.

Single-file persistence (data/registry.db) using only the standard library.
All writes go through a process-wide lock; reads open short-lived connections.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_DB_PATH: Optional[Path] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    title        TEXT NOT NULL,
    domain       TEXT NOT NULL DEFAULT '',
    terms        TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT 'General',
    collection   TEXT NOT NULL,
    page_offset  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'processing',
    error        TEXT,
    pages         INTEGER,
    chunks        INTEGER,
    content_hash  TEXT,
    key_questions TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    rag_json    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_document ON messages(document_id, id);
CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT 'Conversación',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threads_document ON threads(document_id, created_at);
CREATE TABLE IF NOT EXISTS usage_archive (
    day       TEXT PRIMARY KEY,
    tokens    INTEGER NOT NULL DEFAULT 0,
    responses INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS quiz_items (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    question       TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    distractors    TEXT NOT NULL,
    explanation    TEXT,
    page_label     TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_items_document ON quiz_items(document_id);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    score       INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    details     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_document ON quiz_attempts(document_id, created_at);
CREATE TABLE IF NOT EXISTS response_cache (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    question    TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    answer      TEXT NOT NULL,
    sources     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_response_cache_document ON response_cache(document_id);
CREATE TABLE IF NOT EXISTS telegram_sessions (
    chat_id    INTEGER PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    thread_id  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Migraciones idempotentes sobre bases existentes (multi-hilo)."""
    doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "content_hash" not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
    if "key_questions" not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN key_questions TEXT")
    if "category" not in doc_cols:
        conn.execute("ALTER TABLE documents ADD COLUMN category TEXT NOT NULL DEFAULT 'General'")

    # Tabla telegram_sessions: idempotente vía CREATE TABLE IF NOT EXISTS en _SCHEMA,
    # pero para DBs creadas antes de este campo agregamos la migración explícita.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS telegram_sessions ("
        "  chat_id    INTEGER PRIMARY KEY,"
        "  doc_id     TEXT NOT NULL,"
        "  thread_id  TEXT NOT NULL,"
        "  updated_at TEXT NOT NULL"
        ")"
    )

    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "thread_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id)"
    )
    # Mensajes previos al multi-hilo: agruparlos en un hilo "Conversación 1" por doc.
    orphans = conn.execute(
        "SELECT DISTINCT document_id FROM messages WHERE thread_id IS NULL"
    ).fetchall()
    for (doc_id,) in orphans:
        thread_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO threads (id, document_id, title, created_at) VALUES (?,?,?,?)",
            (thread_id, doc_id, "Conversación 1", _now()),
        )
        conn.execute(
            "UPDATE messages SET thread_id = ? WHERE document_id = ? AND thread_id IS NULL",
            (thread_id, doc_id),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("registry no inicializado: llamá init_db() primero")
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _row_to_doc(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_document(
    *,
    filename: str,
    title: str,
    collection: str,
    domain: str = "",
    terms: str = "",
    category: str = "General",
    page_offset: int = 0,
    status: str = "processing",
    pages: Optional[int] = None,
    chunks: Optional[int] = None,
    content_hash: Optional[str] = None,
    doc_id: Optional[str] = None,
) -> dict[str, Any]:
    doc_id = doc_id or uuid.uuid4().hex
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO documents
               (id, filename, title, domain, terms, category, collection, page_offset,
                status, pages, chunks, content_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, filename, title, domain, terms, category, collection, page_offset,
             status, pages, chunks, content_hash, _now()),
        )
    return get_document(doc_id)  # type: ignore[return-value]


def get_document_by_hash(content_hash: str) -> Optional[dict[str, Any]]:
    """Documento existente con el mismo contenido (dedup por hash)."""
    if not content_hash:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE content_hash = ? LIMIT 1", (content_hash,)
        ).fetchone()
    return _row_to_doc(row) if row else None


_DOC_FIELDS = {"title", "domain", "terms", "category", "page_offset", "status", "error",
               "pages", "chunks", "filename", "content_hash", "key_questions"}


def update_document(doc_id: str, **fields: Any) -> None:
    cols = {k: v for k, v in fields.items() if k in _DOC_FIELDS}
    if not cols:
        return
    sets = ", ".join(f"{k} = ?" for k in cols)
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE documents SET {sets} WHERE id = ?", (*cols.values(), doc_id))


def get_document(doc_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_doc(row) if row else None


def list_documents() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [_row_to_doc(r) for r in rows]


def delete_document(doc_id: str) -> None:
    """Borra el documento y TODO lo suyo (colección via API, hilos y mensajes por
    cascade), pero ANTES archiva el consumo de tokens por día para que el histórico
    de métricas no se pierda al eliminar el documento."""
    with _LOCK, _connect() as conn:
        # Archivar tokens consumidos por día (mismo bucket que daily_usage).
        rows = conn.execute(
            "SELECT created_at, rag_json FROM messages "
            "WHERE document_id = ? AND role = 'assistant' AND rag_json IS NOT NULL",
            (doc_id,),
        ).fetchall()
        per_day: dict[str, dict[str, int]] = {}
        for r in rows:
            try:
                day = _day_key(r["created_at"])
                usage = json.loads(r["rag_json"]).get("usage") or {}
                tokens = int(usage.get("total_tokens") or 0)
            except Exception:
                continue
            acc = per_day.setdefault(day, {"tokens": 0, "responses": 0})
            acc["tokens"] += tokens
            acc["responses"] += 1
        for day, acc in per_day.items():
            conn.execute(
                "INSERT INTO usage_archive(day, tokens, responses) VALUES (?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET tokens = tokens + excluded.tokens, "
                "responses = responses + excluded.responses",
                (day, acc["tokens"], acc["responses"]),
            )
        # Borrar el documento (cascade elimina messages y threads).
        conn.execute("DELETE FROM response_cache WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM quiz_attempts WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM quiz_items WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM messages WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def add_message(
    document_id: str,
    role: str,
    content: str,
    rag: Optional[dict[str, Any]] = None,
    thread_id: Optional[str] = None,
) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO messages (document_id, thread_id, role, content, rag_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (document_id, thread_id, role, content,
             json.dumps(rag, ensure_ascii=False) if rag else None, _now()),
        )


def delete_messages(document_id: str) -> None:
    """Vacía el hilo de conversación de un documento (el documento queda intacto)."""
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM messages WHERE document_id = ?", (document_id,))


def _rows_to_messages(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        m = dict(r)
        m["rag"] = json.loads(m.pop("rag_json")) if m.get("rag_json") else None
        out.append(m)
    return out


def list_messages(document_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE document_id = ? ORDER BY id", (document_id,)
        ).fetchall()
    return _rows_to_messages(rows)


def list_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)
        ).fetchall()
    return _rows_to_messages(rows)


def list_questions(document_id: str) -> list[dict[str, Any]]:
    """Preguntas del usuario en el documento (todas sus conversaciones), recientes primero.

    Ligero (sin rag_json): alimenta el buscador de preguntas ya hechas, que evita
    repetir consultas y gastar tokens al saltar a la respuesta ya guardada.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT m.id, m.thread_id, m.content, m.created_at, t.title AS thread_title
               FROM messages m JOIN threads t ON t.id = m.thread_id
               WHERE m.document_id = ? AND m.role = 'user'
               ORDER BY m.id DESC""",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def total_usage() -> dict[str, Any]:
    """Totales acumulados de TODO el historial (mensajes vivos + archivo de borrados).

    Fuente real (servidor) para el panel de métricas: sobrevive a recargas, cambios
    de navegador y al borrado de documentos (cuyos tokens quedan en usage_archive).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rag_json FROM messages WHERE role = 'assistant' AND rag_json IS NOT NULL"
        ).fetchall()
        archived = conn.execute("SELECT tokens, responses FROM usage_archive").fetchall()

    tokens = responses = 0
    seconds = 0.0
    for r in rows:
        try:
            u = json.loads(r["rag_json"])
            tokens += int((u.get("usage") or {}).get("total_tokens") or 0)
            seconds += float(u.get("retrieval_seconds") or 0) + float(u.get("generation_seconds") or 0)
            responses += 1
        except Exception:
            continue
    for a in archived:
        tokens += int(a["tokens"] or 0)
        responses += int(a["responses"] or 0)

    return {"total_tokens": tokens, "responses": responses, "total_seconds": round(seconds, 2)}


def _day_key(created_at: str) -> str:
    """Día local 'YYYY-MM-DD' de un timestamp ISO (mismo bucket en vivo y archivado)."""
    return datetime.fromisoformat(created_at).astimezone().strftime("%Y-%m-%d")


def daily_usage(days: int = 7) -> list[dict[str, Any]]:
    """
    Tokens consumidos por día (hora local del servidor), últimos `days` días.

    Se calcula desde los mensajes persistidos (rag_json.usage) MÁS el archivo de
    consumo de documentos ya borrados (usage_archive), así el histórico de tokens
    no se pierde al eliminar un documento. Los días sin consumo van con 0.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT created_at, rag_json FROM messages "
            "WHERE role = 'assistant' AND rag_json IS NOT NULL"
        ).fetchall()
        archived = conn.execute(
            "SELECT day, tokens, responses FROM usage_archive"
        ).fetchall()

    totals: dict[str, dict[str, int]] = {}
    for r in rows:
        try:
            day = _day_key(r["created_at"])
            usage = json.loads(r["rag_json"]).get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)
        except Exception:
            continue
        acc = totals.setdefault(day, {"tokens": 0, "responses": 0})
        acc["tokens"] += tokens
        acc["responses"] += 1

    # Consumo de documentos ya borrados (preservado al eliminar).
    for a in archived:
        acc = totals.setdefault(a["day"], {"tokens": 0, "responses": 0})
        acc["tokens"] += int(a["tokens"] or 0)
        acc["responses"] += int(a["responses"] or 0)

    today = datetime.now().astimezone().date()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        acc = totals.get(day, {"tokens": 0, "responses": 0})
        out.append({"day": day, "tokens": acc["tokens"], "responses": acc["responses"]})
    return out


# --- Hilos de conversación (varios por documento) --------------------------

def create_thread(document_id: str, title: str = "Nueva conversación") -> dict[str, Any]:
    thread_id = uuid.uuid4().hex
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO threads (id, document_id, title, created_at) VALUES (?,?,?,?)",
            (thread_id, document_id, title, _now()),
        )
    return get_thread(thread_id)  # type: ignore[return-value]


def get_thread(thread_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


def list_threads(document_id: str) -> list[dict[str, Any]]:
    """Hilos del documento (más reciente primero) con su número de mensajes."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT t.*, COUNT(m.id) AS messages
               FROM threads t LEFT JOIN messages m ON m.thread_id = t.id
               WHERE t.document_id = ?
               GROUP BY t.id ORDER BY t.created_at DESC""",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def rename_thread(thread_id: str, title: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id))


def delete_thread(thread_id: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))


# --- Quiz de aprendizaje (preguntas de opción múltiple por documento) -------

def save_quiz_items(document_id: str, items: list[dict[str, Any]]) -> None:
    """Reemplaza el set de quiz cacheado del documento por uno nuevo."""
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM quiz_items WHERE document_id = ?", (document_id,))
        for it in items:
            conn.execute(
                "INSERT INTO quiz_items "
                "(id, document_id, question, correct_answer, distractors, explanation, "
                "page_label, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    it["id"], document_id, it["question"], it["correct_answer"],
                    json.dumps(it.get("distractors") or [], ensure_ascii=False),
                    it.get("explanation", ""), it.get("page_label", ""), _now(),
                ),
            )


def get_quiz_items(document_id: str) -> list[dict[str, Any]]:
    """Items de quiz cacheados del documento (con distractors deserializados)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_items WHERE document_id = ? ORDER BY created_at, id",
            (document_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["distractors"] = json.loads(d.get("distractors") or "[]")
        except Exception:
            d["distractors"] = []
        out.append(d)
    return out


def delete_quiz_items(document_id: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM quiz_items WHERE document_id = ?", (document_id,))


def save_quiz_attempt(
    document_id: str, score: int, total: int, details: list[dict[str, Any]]
) -> dict[str, Any]:
    attempt_id = uuid.uuid4().hex
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO quiz_attempts (id, document_id, score, total, details, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (attempt_id, document_id, score, total,
             json.dumps(details, ensure_ascii=False), now),
        )
    return {"id": attempt_id, "document_id": document_id, "score": score,
            "total": total, "created_at": now}


def list_quiz_attempts(document_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Historial de intentos del documento, recientes primero (sin el detalle pesado)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, score, total, created_at FROM quiz_attempts "
            "WHERE document_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (document_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Caché semántico de respuestas (ahorro de tokens) -----------------------

def add_response_cache(
    document_id: str, question: str, embedding: bytes, answer: str, sources: str
) -> None:
    """Guarda una respuesta ya generada junto al embedding de su pregunta."""
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO response_cache "
            "(id, document_id, question, embedding, answer, sources, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, document_id, question, embedding, answer, sources, _now()),
        )


def list_response_cache(document_id: str) -> list[dict[str, Any]]:
    """Entradas cacheadas del documento (con el embedding crudo en bytes)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT question, embedding, answer, sources FROM response_cache "
            "WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_response_cache(document_id: str) -> None:
    """Vacía el caché de un documento (al reemplazar o borrar su contenido)."""
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM response_cache WHERE document_id = ?", (document_id,))


# ---------------------------------------------------------------------------
# Sesiones Telegram (chat_id → documento + hilo activo)
# ---------------------------------------------------------------------------

def get_telegram_session(chat_id: int) -> Optional[dict[str, Any]]:
    """Devuelve la sesión activa de un chat de Telegram, o None si no existe."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT doc_id, thread_id FROM telegram_sessions WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return dict(row) if row else None


def set_telegram_session(chat_id: int, doc_id: str, thread_id: str) -> None:
    """Guarda o actualiza la sesión de un chat de Telegram."""
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO telegram_sessions (chat_id, doc_id, thread_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET doc_id=excluded.doc_id, "
            "thread_id=excluded.thread_id, updated_at=excluded.updated_at",
            (chat_id, doc_id, thread_id, _now()),
        )
