"""Tests del registry SQLite: documentos, hilos, mensajes, métricas y caché."""

from __future__ import annotations


def test_create_get_and_dedup(temp_db, sample_doc):
    fetched = temp_db.get_document(sample_doc["id"])
    assert fetched["title"] == "Contrato de arrendamiento"
    assert fetched["status"] == "ready"

    # Dedup por hash de contenido.
    assert temp_db.get_document_by_hash("hash-original")["id"] == sample_doc["id"]
    assert temp_db.get_document_by_hash("no-existe") is None


def test_update_document_whitelist(temp_db, sample_doc):
    temp_db.update_document(sample_doc["id"], title="Nuevo título", status="error")
    # Campo fuera de la whitelist: se ignora silenciosamente (no rompe).
    temp_db.update_document(sample_doc["id"], collection="hackeada")
    doc = temp_db.get_document(sample_doc["id"])
    assert doc["title"] == "Nuevo título"
    assert doc["status"] == "error"
    assert doc["collection"] == "doc_test"  # no cambió


def test_threads_messages_and_questions(temp_db, sample_doc):
    thread = temp_db.create_thread(sample_doc["id"], "Conversación 1")
    temp_db.add_message(sample_doc["id"], "user", "¿Qué dice la cláusula 5?", None, thread["id"])
    temp_db.add_message(
        sample_doc["id"], "assistant", "Habla del plazo.",
        {"usage": {"total_tokens": 100}}, thread["id"],
    )

    msgs = temp_db.list_thread_messages(thread["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["rag"]["usage"]["total_tokens"] == 100

    # list_questions devuelve SOLO las del usuario, con el título del hilo.
    questions = temp_db.list_questions(sample_doc["id"])
    assert len(questions) == 1
    assert questions[0]["thread_title"] == "Conversación 1"


def test_total_usage_and_archive_on_delete(temp_db, sample_doc):
    thread = temp_db.create_thread(sample_doc["id"])
    temp_db.add_message(
        sample_doc["id"], "assistant", "respuesta",
        {"usage": {"total_tokens": 250}, "generation_seconds": 1.0}, thread["id"],
    )
    assert temp_db.total_usage()["total_tokens"] == 250

    # Borrar el documento archiva sus tokens (el histórico no se pierde).
    temp_db.delete_document(sample_doc["id"])
    assert temp_db.get_document(sample_doc["id"]) is None
    assert temp_db.total_usage()["total_tokens"] == 250  # vino de usage_archive


def test_response_cache_roundtrip(temp_db, sample_doc):
    temp_db.add_response_cache(
        sample_doc["id"], "¿Qué es X?", b"\x00\x01\x02\x03", "X es Y.", "[]"
    )
    rows = temp_db.list_response_cache(sample_doc["id"])
    assert len(rows) == 1
    assert rows[0]["answer"] == "X es Y."
    assert rows[0]["embedding"] == b"\x00\x01\x02\x03"

    temp_db.clear_response_cache(sample_doc["id"])
    assert temp_db.list_response_cache(sample_doc["id"]) == []


def test_delete_document_cascades_cache_and_quiz(temp_db, sample_doc):
    temp_db.add_response_cache(sample_doc["id"], "q", b"\x00", "a", "[]")
    temp_db.save_quiz_items(sample_doc["id"], [{
        "id": "i1", "question": "¿?", "correct_answer": "sí",
        "distractors": ["a", "b", "c"], "explanation": "", "page_label": "",
    }])
    temp_db.delete_document(sample_doc["id"])
    assert temp_db.list_response_cache(sample_doc["id"]) == []
    assert temp_db.get_quiz_items(sample_doc["id"]) == []


def test_quiz_attempts_history(temp_db, sample_doc):
    temp_db.save_quiz_attempt(sample_doc["id"], score=3, total=5, details=[{"q": 1}])
    temp_db.save_quiz_attempt(sample_doc["id"], score=5, total=5, details=[])
    attempts = temp_db.list_quiz_attempts(sample_doc["id"])
    assert len(attempts) == 2
    # Recientes primero.
    assert attempts[0]["score"] == 5
