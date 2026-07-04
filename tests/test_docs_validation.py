"""Validación de contenido de documentos (CV/CUL): heurística de tipo + rama de rechazo
del motor (mismatch → re-pedir; tope → aceptar marcando revisión; fail-open sin detección)."""

from __future__ import annotations

from agente.nodes import DOC_SEQUENCE, MAX_DOC_RETRIES, _handle_docs
from agente.prompts import DOC_MISMATCH, DOC_RECEIVED
from channels.documents import detect_document_kind_heuristic


# ── Heurística por palabras clave (pura) ──────────────────────────────────────

def test_heuristic_detects_cv():
    text = "Currículum vitae. Experiencia laboral: 5 años. Formación académica: Ingeniería. Habilidades: Python, SQL."
    kind, conf = detect_document_kind_heuristic(text)
    assert kind == "cv" and conf >= 0.8


def test_heuristic_detects_cul():
    text = ("Certificado Único Laboral emitido por el Ministerio de Trabajo y Promoción del Empleo. "
            "Récord laboral y aportes del trabajador ante EsSalud.")
    kind, conf = detect_document_kind_heuristic(text)
    assert kind == "cul" and conf >= 0.8


def test_heuristic_uncertain_on_unrelated_docs():
    # Cotización de Shopify y certificado de curso: sin señales de CV ni CUL → incierto.
    assert detect_document_kind_heuristic("Cotización Shopify. Precio S/ 4,000. Tienda online.") == ("other", 0.0)
    assert detect_document_kind_heuristic("Statement of Accomplishment. LLMOps Concepts. DataCamp.") == ("other", 0.0)
    assert detect_document_kind_heuristic("") == ("other", 0.0)


# ── Rama del motor: aceptar / rechazar / tope / fail-open ─────────────────────

def _docs_state():
    """Estado mínimo en fase de documentos, pidiendo el primero (CV)."""
    return {"outbound": [], "doc_idx": 0, "doc_retries": 0}


def test_matching_document_accepted_and_advances():
    st = _docs_state()
    expected_type = DOC_SEQUENCE[0][0]  # "cv"
    _handle_docs(st, text="", button=None, document={"detected_kind": expected_type, "local_path": "cv.pdf"})
    assert st["save_document"]["type"] == expected_type
    assert "review_required" not in st["save_document"]
    assert any(DOC_RECEIVED.format(label=DOC_SEQUENCE[0][1]) == m for m in st["outbound"])
    assert st["doc_idx"] == 1                          # avanzó al siguiente documento


def test_unknown_kind_is_failopen_accepted():
    st = _docs_state()
    _handle_docs(st, text="", button=None, document={"detected_kind": None, "local_path": "x.pdf"})
    assert st["save_document"] is not None and st["doc_idx"] == 1  # aceptado sin bloquear


def test_mismatch_rejected_and_reasked():
    st = _docs_state()
    _handle_docs(st, text="", button=None, document={"detected_kind": "other", "local_path": "quote.pdf"})
    assert st.get("save_document") is None            # NO se guardó
    assert st["doc_idx"] == 0                          # NO avanzó
    assert st["doc_retries"] == 1
    assert any(DOC_MISMATCH.format(label=DOC_SEQUENCE[0][1]) == m for m in st["outbound"])


def test_mismatch_exhausted_accepts_with_review_flag():
    st = _docs_state()
    doc = {"detected_kind": "other", "local_path": "quote.pdf"}
    # Se rechaza MAX_DOC_RETRIES veces; el siguiente intento se acepta marcado a revisión.
    for _ in range(MAX_DOC_RETRIES):
        st["outbound"] = []
        st["save_document"] = None
        _handle_docs(st, text="", button=None, document=doc)
        assert st.get("save_document") is None and st["doc_idx"] == 0
    st["outbound"] = []
    _handle_docs(st, text="", button=None, document=doc)
    assert st["save_document"]["review_required"] is True
    assert st["save_document"]["detected_kind"] == "other"
    assert st["doc_idx"] == 1                          # ya no traba el flujo
    assert st["doc_retries"] == 0                      # reiniciado al avanzar


def test_skip_advances_without_document():
    st = _docs_state()
    _handle_docs(st, text="omitir", button=None, document=None)
    assert st.get("save_document") is None and st["doc_idx"] == 1
