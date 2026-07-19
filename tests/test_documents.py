"""Fase 1 — validación y saneo de documentos entrantes (audit #5)."""

from __future__ import annotations

from channels.documents import MAX_DOCUMENT_BYTES, sanitize_filename, validate_document


def test_sanitize_strips_path_and_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("a/b/c.pdf") == "c.pdf"
    assert sanitize_filename("..\\..\\win.pdf") == "win.pdf"
    assert "/" not in sanitize_filename("x/y/z")
    assert ".." not in sanitize_filename("....//....//x.pdf")


def test_sanitize_fallback_when_empty():
    assert sanitize_filename("") == "documento.pdf"
    assert sanitize_filename("   ") == "documento.pdf"
    assert sanitize_filename("...") == "documento.pdf"


def test_sanitize_limits_charset_and_length():
    out = sanitize_filename("cv robert'); DROP TABLE.pdf")
    assert "'" not in out and ";" not in out
    assert len(sanitize_filename("a" * 300)) <= 120


def test_validate_accepts_pdf():
    assert validate_document("application/pdf", 1000, "cv.pdf")[0] is True
    assert validate_document(None, 1000, "cv.pdf")[0] is True          # por extensión
    assert validate_document("application/pdf", 1000, "sin_ext")[0] is True  # por mime


def test_validate_rejects_non_pdf():
    ok, reason = validate_document("application/zip", 1000, "cv.zip")
    assert ok is False and "PDF" in reason
    assert validate_document("image/png", 1000, "foto.png")[0] is False
    assert validate_document(None, 1000, "malware.exe")[0] is False


def test_validate_rejects_oversized():
    ok, reason = validate_document("application/pdf", MAX_DOCUMENT_BYTES + 1, "cv.pdf")
    assert ok is False and "MB" in reason


# ── is_awaiting_documents: gate de fase para adjuntos entrantes (UX: no pedir PDF
#    cuando no se están recolectando documentos) ────────────────────────────────
from agente.service import InterviewService


class _StubRunner:
    def __init__(self, state):
        self._state = state

    def get_state(self, thread_id):
        return self._state


def _svc_with_state(state):
    svc = InterviewService.__new__(InterviewService)  # sin el constructor pesado
    svc.runner = _StubRunner(state)
    return svc


def test_awaiting_documents_true_only_in_docs_phase():
    assert _svc_with_state({"phase": "awaiting_docs"}).is_awaiting_documents("telegram", "1") is True


def test_awaiting_documents_false_in_other_phases():
    for phase in ("interviewing", "finished", "scheduling", "scheduled", "greeting", "closed"):
        assert _svc_with_state({"phase": phase}).is_awaiting_documents("telegram", "1") is False


def test_awaiting_documents_false_without_conversation():
    assert _svc_with_state({}).is_awaiting_documents("telegram", "1") is False


def test_awaiting_documents_false_on_runner_error():
    class _BoomRunner:
        def get_state(self, thread_id):
            raise RuntimeError("checkpoint DB caída")

    svc = InterviewService.__new__(InterviewService)
    svc.runner = _BoomRunner()
    assert svc.is_awaiting_documents("telegram", "1") is False
