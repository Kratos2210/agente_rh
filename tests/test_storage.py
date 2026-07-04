"""Fase 1 (cierre) — almacenamiento durable de documentos (contenido en Postgres)."""

from __future__ import annotations

import base64

from agente import service


def test_read_document_b64_roundtrip(tmp_path):
    p = tmp_path / "cv.pdf"
    payload = b"%PDF-1.4 contenido de prueba"
    p.write_bytes(payload)
    b64, size = service._read_document_b64(str(p))
    assert size == len(payload)
    assert base64.b64decode(b64) == payload


def test_read_document_b64_missing():
    assert service._read_document_b64("/no/existe.pdf") == ("", 0)
    assert service._read_document_b64("") == ("", 0)


def test_persist_save_document_stores_durably(monkeypatch, tmp_path):
    p = tmp_path / "hoja.pdf"
    p.write_bytes(b"PDFDATA")
    calls: dict = {}
    monkeypatch.setattr(service.repositories, "save_document_content", lambda cid, **kw: calls.update({"cid": cid, **kw}))
    monkeypatch.setattr(service.repositories, "add_candidate_document", lambda cid, doc: calls.__setitem__("meta", doc))
    monkeypatch.setattr(service.repositories, "add_message", lambda *a, **k: None)

    svc = service.InterviewService.__new__(service.InterviewService)  # sin runner
    state = {
        "save_document": {
            "type": "cv", "filename": "hoja.pdf", "local_path": str(p),
            "mime": "application/pdf", "file_id": "f1",
        }
    }
    svc._persist_save_document({"id": "cand1"}, {"id": "conv1"}, state)
    assert calls["cid"] == "cand1" and calls["doc_type"] == "cv"
    assert calls["filename"] == "hoja.pdf" and calls["size_bytes"] == 7
    assert base64.b64decode(calls["content_b64"]) == b"PDFDATA"
    assert calls["meta"]["stored"] == "db"


def test_persist_save_document_degrades_without_file(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(service.repositories, "save_document_content", lambda cid, **kw: calls.setdefault("saved", True))
    monkeypatch.setattr(service.repositories, "add_candidate_document", lambda cid, doc: calls.__setitem__("meta", doc))
    monkeypatch.setattr(service.repositories, "add_message", lambda *a, **k: None)

    svc = service.InterviewService.__new__(service.InterviewService)
    state = {"save_document": {"type": "cul", "filename": "x.pdf", "local_path": "/no/existe.pdf"}}
    svc._persist_save_document({"id": "cand1"}, {"id": "conv1"}, state)
    assert "saved" not in calls                    # no intentó guardar contenido inexistente
    assert calls["meta"]["stored"] == "none"       # solo metadata (degradación)
