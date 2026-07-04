"""Tests de configuración y localización de PDFs (no requieren modelos)."""
from __future__ import annotations

from core.config import Settings, parse_pdf_paths


def test_parse_pdf_paths_glob_data_dir(tmp_path):
    # Crea un PDF válido y un archivo no-PDF en una carpeta temporal.
    (tmp_path / "doc.pdf").write_text("contenido")
    (tmp_path / "nota.txt").write_text("no es pdf")

    settings = Settings(pdf_dir=str(tmp_path), pdf_paths_raw="")
    found = parse_pdf_paths(settings)

    assert len(found) == 1
    assert found[0].name == "doc.pdf"


def test_parse_pdf_paths_explicit_filters_missing(tmp_path):
    real = tmp_path / "real.pdf"
    real.write_text("x")
    missing = tmp_path / "no_existe.pdf"

    settings = Settings(pdf_paths_raw=f"{real};{missing}")
    found = parse_pdf_paths(settings)

    # Solo debe quedar el que existe de verdad.
    assert [p.name for p in found] == ["real.pdf"]


def test_settings_defaults_and_types():
    settings = Settings(pdf_paths_raw="")
    # Tipos correctos (pydantic los valida/convierte).
    assert isinstance(settings.retrieve_k, int)
    assert isinstance(settings.final_k, int)
    assert settings.retrieve_k >= settings.final_k
    assert settings.reranker in {"cross", "heuristic"}
