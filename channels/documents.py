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
