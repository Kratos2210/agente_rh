from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from pathlib import Path
from typing import List

from langchain_chroma import Chroma
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import Settings, parse_pdf_paths
from .embeddings import get_embeddings
from .logging_config import get_logger

logger = get_logger(__name__)


def _chunk_id(doc: Document) -> str:
    """
    ID determinista por contenido: si un chunk no cambió, su id es el mismo entre
    ejecuciones. Esto permite indexación incremental (upsert) sin reprocesar todo.
    """
    source = str(doc.metadata.get("source", "unknown"))
    page = str(doc.metadata.get("page", "na"))
    raw = f"{source}|{page}|{doc.page_content}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def load_pdf_documents(settings: Settings) -> List[Document]:
    """
    Carga todos los PDFs definidos en PDF_PATHS o en la carpeta ./data.

    Cada página del PDF se convierte en un Document.
    """
    pdf_paths = parse_pdf_paths(settings)

    if not pdf_paths:
        raise FileNotFoundError(
            "No encontré PDFs. Definí PDF_PATHS en .env o colocá archivos PDF en ./data."
        )

    docs: List[Document] = []

    for pdf_path in pdf_paths:
        loader = PyPDFLoader(str(pdf_path))
        pages = loader.load()

        for page in pages:
            page.metadata["source"] = str(pdf_path)
            docs.append(page)

    return docs


def build_chunked_documents(settings: Settings) -> List[Document]:
    """
    Divide los PDFs en chunks más chicos usando un splitter rápido.

    Para una clase o demo es mejor que el semantic chunking pesado,
    porque indexa mucho más rápido y evita recalcular embeddings de oración
    por oración.
    """
    raw_docs = load_pdf_documents(settings)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunked_docs = splitter.split_documents(raw_docs)

    for idx, doc in enumerate(chunked_docs):
        doc.metadata["chunk_index"] = idx

    return chunked_docs


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt"}


def _run_ocr(path: Path, language: str) -> List[Document] | None:
    """Corre OCR sobre un PDF escaneado con ocrmypdf y devuelve sus páginas.

    ocrmypdf genera un PDF nuevo con capa de texto manteniendo las páginas, así que
    al recargarlo con PyPDFLoader conservamos la metadata para citar `[p. N]`.
    Devuelve None si ocrmypdf no está instalado (dependencia opcional del sistema).
    """
    try:
        import ocrmypdf  # import perezoso: dependencia opcional (requiere tesseract)
    except Exception:
        logger.warning("ocrmypdf no disponible; indexo el PDF sin OCR")
        return None

    t0 = time.time()
    out_path = Path(tempfile.mkdtemp()) / "ocr.pdf"
    try:
        # skip_text: OCR solo las páginas sin texto (no rompe si alguna ya lo tiene).
        ocrmypdf.ocr(str(path), str(out_path), language=language,
                     skip_text=True, progress_bar=False)
        docs = PyPDFLoader(str(out_path)).load()
        logger.info("OCR OK en %.1fs (%d páginas)", time.time() - t0, len(docs))
        return docs
    finally:
        out_path.unlink(missing_ok=True)


def _maybe_ocr_pdf(path: Path, docs: List[Document], settings: Settings) -> List[Document]:
    """Si el PDF parece escaneado (la mayoría de páginas casi sin texto), corre OCR.
    Falla en abierto: ante cualquier error devuelve las páginas originales."""
    if not settings.ocr_enabled or not docs:
        return docs
    empties = sum(1 for d in docs if len(d.page_content.strip()) < settings.ocr_min_chars_per_page)
    if empties <= len(docs) // 2:
        return docs  # hay texto suficiente: no es escaneado
    logger.info("PDF parece escaneado (%d/%d páginas sin texto); corriendo OCR…",
                empties, len(docs))
    try:
        ocred = _run_ocr(path, settings.ocr_language)
    except Exception:
        logger.warning("OCR falló; indexo el PDF tal cual", exc_info=True)
        return docs
    return ocred if ocred else docs


def _load_source_documents(
    path: Path, source_name: str | None, settings: Settings | None = None
) -> List[Document]:
    """
    Carga un archivo soportado como lista de Documents según su extensión.

    PDF conserva la metadata de página (citas [p. N]); DOCX y TXT no tienen
    páginas, así que sus chunks van sin `page` y el asistente no cita números
    (regla 4 del system prompt: sin [p. N] en el contexto no se cita).

    Para PDFs escaneados (imagen) sin texto extraíble, intenta OCR (si `settings`
    lo permite) para no indexar un documento vacío.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        docs = PyPDFLoader(str(path)).load()
        if settings is not None:
            docs = _maybe_ocr_pdf(path, docs, settings)
    elif suffix == ".docx":
        docs = Docx2txtLoader(str(path)).load()
    elif suffix == ".txt":
        try:
            docs = TextLoader(str(path), encoding="utf-8").load()
        except UnicodeDecodeError:
            docs = TextLoader(str(path), encoding="latin-1").load()
    else:
        raise ValueError(f"Formato no soportado: {suffix} (acepto PDF, DOCX y TXT)")
    for d in docs:
        d.metadata["source"] = source_name or path.name
    return docs


def index_document(
    pdf_path: Path,
    collection_name: str,
    settings: Settings,
    source_name: str | None = None,
) -> tuple[int, int]:
    """
    Indexa UN archivo (PDF/DOCX/TXT) en una colección Chroma propia (multi-doc).

    Devuelve (páginas, chunks). Reutiliza el splitter y los ids deterministas
    del flujo clásico, pero sin depender de PDF_PATHS ni de la colección global.
    `source_name` permite citar el nombre original del archivo subido (en disco
    se guarda con un nombre interno <id>.<ext>).
    """
    pages = _load_source_documents(pdf_path, source_name, settings)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunked_docs = splitter.split_documents(pages)
    for idx, doc in enumerate(chunked_docs):
        doc.metadata["chunk_index"] = idx

    embeddings = get_embeddings(settings.embedding_model)
    persist_dir = Path(settings.persist_directory)
    persist_dir.mkdir(parents=True, exist_ok=True)

    vectorstore = Chroma(
        collection_name=collection_name,
        persist_directory=str(persist_dir),
        embedding_function=embeddings,
    )

    ids, new_docs, new_ids = [_chunk_id(d) for d in chunked_docs], [], []
    seen: set = set()
    for doc, cid in zip(chunked_docs, ids):
        if cid in seen:
            continue
        seen.add(cid)
        new_docs.append(doc)
        new_ids.append(cid)
    if new_docs:
        vectorstore.add_documents(new_docs, ids=new_ids)

    return len(pages), len(new_docs)


# Alias retrocompatible (el flujo original era solo-PDF).
index_pdf = index_document


def build_vectorstore(settings: Settings, full_rebuild: bool = False) -> Chroma:
    """
    Construye (o actualiza) la base vectorial local en Chroma.

    - full_rebuild=True: borra el índice y lo reconstruye desde cero.
    - full_rebuild=False (por defecto): indexación INCREMENTAL. Solo agrega los
      chunks nuevos o modificados (identificados por hash de contenido), evitando
      recalcular embeddings de lo que no cambió.
    """
    embeddings = get_embeddings(settings.embedding_model)
    chunked_docs = build_chunked_documents(settings)

    persist_dir = Path(settings.persist_directory)

    if full_rebuild:
        try:
            shutil.rmtree(persist_dir)
        except FileNotFoundError:
            pass

    persist_dir.mkdir(parents=True, exist_ok=True)

    vectorstore = Chroma(
        collection_name=settings.collection_name,
        persist_directory=str(persist_dir),
        embedding_function=embeddings,
    )

    ids = [_chunk_id(doc) for doc in chunked_docs]

    # Ids ya presentes en la colección (vacío si el índice es nuevo).
    try:
        existing = set(vectorstore.get(include=[]).get("ids", []))
    except Exception:
        existing = set()

    new_docs, new_ids = [], []
    seen = set()
    for doc, cid in zip(chunked_docs, ids):
        if cid in existing or cid in seen:
            continue  # ya indexado o duplicado exacto dentro del mismo PDF
        seen.add(cid)
        new_docs.append(doc)
        new_ids.append(cid)

    if new_docs:
        vectorstore.add_documents(new_docs, ids=new_ids)

    logger.info(
        "[indexación] chunks totales=%d nuevos=%d ya_existentes=%d",
        len(chunked_docs), len(new_docs), len(chunked_docs) - len(new_docs),
    )
    return vectorstore