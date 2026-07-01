"""
Tests de la lógica de puntuación del reranker heurístico.

Se evita cargar el modelo de embeddings construyendo la instancia con
object.__new__ (saltea __init__), porque las funciones bajo prueba son puras
y no dependen del modelo.
"""
from __future__ import annotations

from src.reranker import RerankConfig, SemanticReranker


def _make_reranker() -> SemanticReranker:
    r = SemanticReranker.__new__(SemanticReranker)  # no carga el modelo
    r.config = RerankConfig()
    return r


def test_tokens_ignora_palabras_cortas_y_normaliza():
    toks = SemanticReranker._tokens("La función PRINT es útil")
    assert "función" in toks
    assert "print" in toks  # minúsculas
    assert "la" not in toks  # <= 2 caracteres se descarta
    assert "es" not in toks


def test_lexical_score_con_y_sin_solape():
    r = _make_reranker()
    assert r._lexical_score("definir una función", "cómo definir una función") > 0
    assert r._lexical_score("recursividad", "tabla de multiplicar") == 0.0


def test_structure_score_premia_codigo_y_castiga_indices():
    r = _make_reranker()
    con_codigo = r._structure_score("def saludar(): print('hola') return None")
    indice = r._structure_score("índice de contenido prólogo")
    assert con_codigo > 0
    assert indice < 0
    assert con_codigo > indice
