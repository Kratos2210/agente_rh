"""Validación y saneo de documentos entrantes (CV/CUL) — Fase 1 (audit #5).

Puros y testeables, independientes del canal. Protegen contra **path traversal** en
el nombre de archivo y rechazan tipos/tamaños no permitidos ANTES de descargar (evita
guardar ejecutables o archivos gigantes)."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# El flujo pide la hoja de vida / CUL en PDF.
ALLOWED_MIME = {"application/pdf"}
ALLOWED_EXT = {".pdf"}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20 MB (límite práctico de Telegram para bots)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_REJECT_TYPE = "Por ahora solo aceptamos archivos PDF. ¿Podrías reenviarlo en ese formato?"


def sanitize_filename(name: str) -> str:
    """Nombre de archivo seguro: sin componentes de ruta ni traversal, caracteres acotados.

    Toma solo el basename, elimina `..` y separadores, limita caracteres y longitud.
    Nunca devuelve vacío (usa `documento.pdf` de respaldo)."""
    raw = (name or "").strip().replace("\\", "/")
    base = PurePosixPath(raw).name          # descarta cualquier componente de ruta
    base = base.replace("..", "")           # sin traversal residual
    base = _SAFE_CHARS.sub("_", base).strip(" .")
    return base[:120] or "documento.pdf"


def validate_document(mime: str | None, size: int | None, filename: str | None) -> tuple[bool, str]:
    """(ok, motivo_para_el_candidato). Acepta solo PDF dentro del límite de tamaño."""
    ext = PurePosixPath((filename or "").lower()).suffix
    if not (ext in ALLOWED_EXT or mime in ALLOWED_MIME):
        return (False, _REJECT_TYPE)
    if size is not None and size > MAX_DOCUMENT_BYTES:
        mb = MAX_DOCUMENT_BYTES // (1024 * 1024)
        return (False, f"El archivo supera el límite de {mb} MB. ¿Podrías enviar una versión más liviana?")
    return (True, "")


# ── Detección heurística del TIPO de documento (CV vs CUL) ────────────────────────
# Puro y sin I/O ni LLM: recibe el texto ya extraído del PDF y estima qué documento es,
# por palabras clave distintivas. La capa de servicio complementa con el LLM cuando la
# confianza es baja (validación híbrida). Solo frases/tokens distintivos (evita falsos
# positivos por subcadenas: nada de "cv"/"cul" sueltos — aparecen en "cultura", "vínculo"…).
_CV_MARKERS = (
    "experiencia laboral", "experiencia profesional", "formación académica", "formacion academica",
    "educación", "educacion", "habilidades", "referencias", "perfil profesional",
    "currículum", "curriculum vitae", "hoja de vida", "idiomas", "competencias",
    "objetivo profesional", "resumen profesional", "datos personales", "logros",
)
_CUL_MARKERS = (
    "certificado único laboral", "certificado unico laboral", "ministerio de trabajo",
    "promoción del empleo", "promocion del empleo", "planilla electrónica", "planilla electronica",
    "essalud", "sunafil", "reporte del trabajador", "récord laboral", "record laboral",
    "certijoven", "certiadulto", "seguro social de salud",
)


def _count_markers(low: str, markers: tuple[str, ...]) -> int:
    return sum(1 for m in markers if m in low)


def _kind_confidence(dominant: int, other: int) -> float:
    """Confianza de la heurística: alta con evidencia fuerte y margen; media con margen mínimo."""
    if dominant >= 2 and dominant - other >= 2:
        return 0.9
    if dominant - other >= 1:
        return 0.5
    return 0.0


def detect_document_kind_heuristic(text: str) -> tuple[str, float]:
    """Estima el tipo de documento a partir de su texto.

    Devuelve (kind, confidence) con kind ∈ {"cv", "cul", "other"}. confidence≈0 = incierto
    (la capa de servicio pedirá desambiguación al LLM). Sin I/O ni LLM."""
    low = (text or "").lower()
    if not low.strip():
        return ("other", 0.0)
    cv = _count_markers(low, _CV_MARKERS)
    cul = _count_markers(low, _CUL_MARKERS)
    if cul > cv:
        return ("cul", _kind_confidence(cul, cv))
    if cv > cul:
        return ("cv", _kind_confidence(cv, cul))
    return ("other", 0.0)  # empate o sin señales → incierto
