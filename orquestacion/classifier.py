"""LLM-based document classification: infers title, domain and key terms.

Used at upload time so the RAG system prompt adapts to the document type
(contract, regulation, manual, book...) without manual configuration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypedDict

from langchain_openai import ChatOpenAI
from pypdf import PdfReader

from core.config import Settings


CATEGORIES = ["Ley", "Reglamento", "Norma", "Manual", "Contrato", "Estudio", "Procedimiento", "General"]


class DocumentProfile(TypedDict):
    title: str
    domain: str
    terms: str
    category: str
    questions: list[str]


_CLASSIFY_PROMPT = """Sos un bibliotecario experto. Analizá el comienzo de un documento y devolvé
SOLO un JSON (sin markdown, sin explicaciones) con esta forma exacta:

{{"title": "...", "domain": "...", "terms": "...", "category": "...", "questions": ["...", "...", "...", "...", "..."]}}

- "title": título corto y legible del documento (máx. 60 caracteres). Si el texto lo menciona, usalo;
  si no, inferilo del contenido.
- "domain": el área de especialidad para un asistente experto, p. ej. "Contratos y derecho civil",
  "Normativa tributaria peruana", "Manuales técnicos de software", "Gobierno de Datos (DAMA-DMBOK)".
- "terms": 4-6 términos clave del dominio separados por coma, en español, p. ej.
  "cláusulas, partes, obligaciones, penalidades, resolución".
- "category": UNA sola categoría de esta lista exacta (sin variantes):
  "Ley", "Reglamento", "Norma", "Manual", "Contrato", "Estudio", "Procedimiento", "General".
  Elegí la que mejor describe el tipo de documento.
- "questions": EXACTAMENTE 5 preguntas CLAVE que cualquier persona debería hacerle a este documento
  antes de firmarlo o usarlo, redactadas como preguntas completas y concretas en español (no títulos).
  Deben poder responderse con el documento. Ejemplo para un contrato laboral:
  ["¿Cuánto dura el contrato?", "¿Cuál es la remuneración?", "¿Cuáles son las causales de despido?",
   "¿Qué obligaciones tiene el trabajador?", "¿Hay cláusula de confidencialidad?"].

Comienzo del documento:
---
{text}
---
JSON:"""


_QUESTIONS_PROMPT = """Sos un asesor experto. A partir de la información de un documento, devolvé
SOLO un JSON (sin markdown) con 5 preguntas CLAVE que cualquier persona debería hacerle antes de
firmarlo o usarlo, redactadas como preguntas completas y concretas en español (no títulos), que
puedan responderse con ese documento:

{{"questions": ["...", "...", "...", "...", "..."]}}

Documento — dominio: {domain}
Términos clave: {terms}
{extra}
JSON:"""


def extract_first_pages(pdf_path: Path, max_pages: int = 3, max_chars: int = 6000) -> str:
    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)[:max_chars]


def extract_intro_text(path: Path, max_chars: int = 6000) -> str:
    """Texto inicial del documento para clasificarlo, según su formato."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return extract_first_pages(path, max_chars=max_chars)
        if suffix == ".docx":
            import docx2txt

            return (docx2txt.process(str(path)) or "")[:max_chars]
        if suffix == ".txt":
            raw = path.read_bytes()[: max_chars * 4]
            try:
                return raw.decode("utf-8")[:max_chars]
            except UnicodeDecodeError:
                return raw.decode("latin-1", errors="replace")[:max_chars]
    except Exception:
        pass
    return ""


def _fallback_questions(terms: str) -> list[str]:
    """Preguntas genéricas derivadas de los términos clave (si el LLM falla)."""
    chips = [t.strip() for t in (terms or "").split(",") if t.strip()][:3]
    qs = [f"¿Qué dice el documento sobre {t}?" for t in chips]
    qs.append("Resumí el documento en 5 puntos")
    qs.append("¿Cuáles son los puntos clave del documento?")
    return qs[:5]


def _fallback(filename: str) -> DocumentProfile:
    title = Path(filename).stem.replace("_", " ").replace("-", " ").strip() or "Documento"
    terms = "conceptos clave, definiciones, secciones del documento"
    return {
        "title": title[:60],
        "domain": "Documentos generales",
        "terms": terms,
        "category": "General",
        "questions": _fallback_questions(terms),
    }


def _make_llm(settings: Settings) -> ChatOpenAI:
    llm_kwargs = dict(
        model=settings.openai_model,
        base_url=settings.openai_api_base,
        api_key=settings.openai_api_key,
        timeout=60,
        max_retries=1,
        temperature=0,
    )
    if "qwen3" in settings.openai_model.lower():
        llm_kwargs["extra_body"] = {"reasoning_effort": "none"}
    return ChatOpenAI(**llm_kwargs)


def _clean_questions(raw: object) -> list[str]:
    """Normaliza la lista de preguntas del LLM: strings no vacíos, máx. 5."""
    if not isinstance(raw, list):
        return []
    out = []
    for q in raw:
        s = str(q).strip()[:200]
        if s:
            out.append(s)
    return out[:5]


def classify_document(text: str, settings: Settings, filename: str) -> DocumentProfile:
    """Clasifica el documento con el LLM; ante cualquier fallo devuelve un perfil genérico."""
    if not text.strip():
        return _fallback(filename)

    try:
        resp = _make_llm(settings).invoke(_CLASSIFY_PROMPT.format(text=text))
        raw = resp.content if isinstance(resp.content, str) else str(resp.content)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        title = str(data.get("title", "")).strip()[:60]
        domain = str(data.get("domain", "")).strip()[:120]
        terms = str(data.get("terms", "")).strip()[:300]
        if not (title and domain and terms):
            raise ValueError("clasificación incompleta")
        raw_cat = str(data.get("category", "")).strip()
        category = raw_cat if raw_cat in CATEGORIES else "General"
        questions = _clean_questions(data.get("questions")) or _fallback_questions(terms)
        return {"title": title, "domain": domain, "terms": terms, "category": category, "questions": questions}
    except Exception:
        return _fallback(filename)


def suggest_questions(
    settings: Settings, *, text: str = "", domain: str = "", terms: str = ""
) -> list[str]:
    """Genera 5 preguntas clave para un documento ya existente (generación bajo demanda).

    Usa el texto inicial si está disponible; si no, se apoya en dominio + términos.
    Ante cualquier fallo, cae a preguntas genéricas derivadas de los términos.
    """
    try:
        extra = f"Comienzo del documento:\n---\n{text[:4000]}\n---" if text.strip() else ""
        prompt = _QUESTIONS_PROMPT.format(domain=domain or "documento", terms=terms or "", extra=extra)
        resp = _make_llm(settings).invoke(prompt)
        raw = resp.content if isinstance(resp.content, str) else str(resp.content)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        return _clean_questions(data.get("questions")) or _fallback_questions(terms)
    except Exception:
        return _fallback_questions(terms)
