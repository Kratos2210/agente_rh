"""Fixtures compartidas de los tests del backend.

Mantienen los tests rápidos y sin modelos pesados: el registry corre sobre una
DB SQLite temporal y el cliente de la API mockea embeddings/indexación para no
cargar torch ni llamar al LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Igual que pytest.ini (pythonpath=.), por si se corre el archivo suelto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import registry  # noqa: E402


@pytest.fixture
def temp_db(tmp_path):
    """Inicializa el registry sobre una DB temporal y lo resetea al terminar."""
    registry.init_db(tmp_path / "registry.db")
    yield registry
    registry._DB_PATH = None


@pytest.fixture
def sample_doc(temp_db):
    """Un documento listo en la DB temporal (para colgarle hilos/mensajes/caché)."""
    return temp_db.create_document(
        filename="contrato.pdf",
        title="Contrato de arrendamiento",
        collection="doc_test",
        domain="contratos",
        terms="cláusula, arrendador, arrendatario",
        content_hash="hash-original",
        status="ready",
    )
