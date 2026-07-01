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
