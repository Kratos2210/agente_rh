"""Tests del motor de entrevista (orquestación) con un LLM fake determinista.

Validan el flujo y la agregación, no la calidad de juicio del LLM real:
consentimiento, secuencia de preguntas, follow-ups, dudas del candidato y scorecard.
"""

from __future__ import annotations

import json
import re

import pytest

from agente.graph import make_memory_runner
from agente.state import (
    PHASE_AWAITING_DOCS,
    PHASE_CLOSED,
    PHASE_FINISHED,
    PHASE_INTERVIEWING,
)
from evaluation.scorecard import compute_semaphore, weighted_total


# ── LLM fake programable ────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *, score=85, classify=None, follow_up_on=None):
        self.score = score
        # classify: callable(message)->("answer"|"question"); por defecto siempre "answer".
        self.classify = classify or (lambda _m: "answer")
        self.follow_up_on = set(follow_up_on or [])
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if '"kind"' in prompt:
            # El mensaje va entre delimitadores anti-inyección (igual que evaluate).
            m = re.search(r"<<<respuesta>>>\n(.*?)\n<<<fin>>>", prompt, re.S)
            msg = m.group(1) if m else ""
            return json.dumps({"kind": self.classify(msg)})
        if "needs_follow_up" in prompt:
            # La respuesta va entre delimitadores anti-inyección (<<<respuesta>>> … <<<fin>>>).
            m = re.search(r"<<<respuesta>>>\n(.*?)\n<<<fin>>>", prompt, re.S)
            answer = m.group(1) if m else ""
            needs = any(s in answer for s in self.follow_up_on)
            return json.dumps(
                {
                    "score": self.score,
                    "justification": "ok",
                    "needs_follow_up": needs,
                    "follow_up_question": "¿Podrías ampliar con ejemplos?" if needs else "",
                    "ack": "Gracias.",
                }
            )
        if "recommendation" in prompt:
            return json.dumps({"summary": "Resumen.", "recommendation": "Recomendación."})
        if "Información disponible sobre el puesto" in prompt:
            return "Es presencial en Santiago de Surco."
        return "{}"


def _vacancy(green_min=75, yellow_min=50):
    return {
        "title": "Vacante demo",
        "intro_message": "Hola, ¿deseas continuar?",
        "company_info": "Empresa retail. Presencial en Surco.",
        "semaphore_thresholds": {"green_min": green_min, "yellow_min": yellow_min},
    }


def _questions(n=2, max_follow_ups=1):
    return [
        {
            "question_id": f"q{i}",
            "position": i,
            "text": f"Pregunta {i}?",
            "criterion": f"criterio {i}",
            "weight": 1.0,
            "max_follow_ups": max_follow_ups,
        }
        for i in range(1, n + 1)
    ]


# ── Tests de flujo ──────────────────────────────────────────────────────────────

def test_decline_closes_interview():
    runner = make_memory_runner(FakeLLM())
    tid = "test:1"
    s0 = runner.start(tid, _vacancy(), _questions())
    assert s0["show_consent_buttons"] is True

    s1 = runner.send(tid, button="decline")
    assert s1["phase"] == PHASE_CLOSED
    assert s1["consented"] is False
    assert s1["outbound"], "debe enviar un mensaje de cierre cordial"


def test_accept_shows_position_details_then_first_question():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:details"
    vac = {**_vacancy(), "details_message": "📌 Detalle del puesto: requisitos y beneficios."}
    runner.start(tid, vac, _questions(n=2, max_follow_ups=0))

    # Al aceptar: detalle del puesto + primera pregunta (sin paso de CUL).
    s1 = runner.send(tid, button="accept")
    assert s1["phase"] == PHASE_INTERVIEWING
    assert "Detalle del puesto" in s1["outbound"][0]
    assert "Pregunta 1 de 2" in " ".join(s1["outbound"][1:])


def test_revalidation_uses_cv_when_field_present():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:cv"
    questions = [{
        "question_id": "q1", "position": 1, "text": "¿Cuántos años de experiencia tienes?",
        "criterion": "experiencia", "weight": 1.0, "max_follow_ups": 0,
        "cv_field": "years_experience",
    }]
    cv = {"years_experience": 4, "skills": ["Python"]}
    runner.start(tid, _vacancy(), questions, cv_profile=cv)
    s = runner.send(tid, button="accept")
    joined = " ".join(s["outbound"])
    assert "Según tu CV" in joined
    assert "4 años" in joined


def test_full_flow_reaches_green_scorecard():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:2"
    runner.start(tid, _vacancy(), _questions(n=2, max_follow_ups=0))

    s1 = runner.send(tid, button="accept")
    assert s1["phase"] == PHASE_INTERVIEWING
    assert "Pregunta 1 de 2" in " ".join(s1["outbound"])

    s2 = runner.send(tid, text="Respuesta concreta y detallada uno.")
    assert "Pregunta 2 de 2" in " ".join(s2["outbound"])

    s3 = runner.send(tid, text="Respuesta concreta y detallada dos.")
    # Cumple el perfil (verde) → felicita y pasa a recolectar documentos.
    assert s3["phase"] == PHASE_AWAITING_DOCS
    assert len(s3["answers"]) == 2
    sc = s3["scorecard"]
    assert sc["semaphore"] == "green"
    assert sc["total_score"] == 90.0
    assert "Felicitaciones" in " ".join(s3["outbound"])
    assert "hoja de vida" in " ".join(s3["outbound"])


def test_document_collection_after_qualifying():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:docs"
    runner.start(tid, _vacancy(), _questions(n=1, max_follow_ups=0))
    runner.send(tid, button="accept")
    s_done = runner.send(tid, text="Respuesta concreta y detallada.")
    assert s_done["phase"] == PHASE_AWAITING_DOCS

    # Envía el CV (PDF) → se marca para persistir y pide el CUL.
    s_cv = runner.send(tid, document={"file_id": "f1", "filename": "cv.pdf"})
    assert s_cv["save_document"]["type"] == "cv"
    assert "Certificado Único Laboral" in " ".join(s_cv["outbound"])
    assert s_cv["phase"] == PHASE_AWAITING_DOCS

    # Envía el CUL → cierra el proceso.
    s_cul = runner.send(tid, document={"file_id": "f2", "filename": "cul.pdf"})
    assert s_cul["save_document"]["type"] == "cul"
    assert s_cul["phase"] == PHASE_FINISHED
    assert "Gracias por tu tiempo" in " ".join(s_cul["outbound"])


def test_documents_can_be_skipped():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:docskip"
    runner.start(tid, _vacancy(), _questions(n=1, max_follow_ups=0))
    runner.send(tid, button="accept")
    runner.send(tid, text="Respuesta concreta y detallada.")
    runner.send(tid, text="omitir")          # omite CV → pide CUL
    s = runner.send(tid, text="omitir")       # omite CUL → cierra
    assert s["phase"] == PHASE_FINISHED


def test_timeout_in_interview_closes_as_no_response():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:timeout"
    runner.start(tid, _vacancy(), _questions(n=2, max_follow_ups=0))
    runner.send(tid, button="accept")  # entra a interviewing
    s = runner.send(tid, timeout=True)
    assert s["phase"] == PHASE_CLOSED
    assert s["closed_reason"] == "no_response"
    assert s["outbound"], "debe enviar un mensaje de cierre por inactividad"


def test_timeout_in_docs_finishes_without_penalty():
    runner = make_memory_runner(FakeLLM(score=90))
    tid = "test:timeout-docs"
    runner.start(tid, _vacancy(), _questions(n=1, max_follow_ups=0))
    runner.send(tid, button="accept")
    s_done = runner.send(tid, text="Respuesta concreta y detallada.")
    assert s_done["phase"] == PHASE_AWAITING_DOCS
    # Timeout esperando documentos: cierra el proceso como 'finished' (ya calificó), sin penalizar.
    s = runner.send(tid, timeout=True)
    assert s["phase"] == PHASE_FINISHED
    assert s.get("closed_reason") != "no_response"


def test_follow_up_then_advance():
    llm = FakeLLM(score=80, follow_up_on=["corto"])
    runner = make_memory_runner(llm)
    tid = "test:3"
    runner.start(tid, _vacancy(), _questions(n=1, max_follow_ups=1))
    runner.send(tid, button="accept")

    # Respuesta escueta → dispara follow-up, sigue en la misma pregunta.
    s_fu = runner.send(tid, text="corto")
    assert s_fu["phase"] == PHASE_INTERVIEWING
    assert s_fu["current_idx"] == 0
    assert "ampliar" in " ".join(s_fu["outbound"]).lower()
    assert s_fu["answers"] == []

    # Amplía → cierra la pregunta; al calificar (verde) pasa a recolectar documentos.
    s_done = runner.send(tid, text="ahora doy detalle suficiente y completo")
    assert s_done["phase"] == PHASE_AWAITING_DOCS
    assert len(s_done["answers"]) == 1
    # La respuesta acumulada conserva ambas partes.
    assert "corto" in s_done["answers"][0]["raw_answer"]


def test_candidate_question_is_answered_without_advancing():
    classify = lambda msg: "question" if msg.strip().endswith("?") else "answer"
    runner = make_memory_runner(FakeLLM(classify=classify))
    tid = "test:4"
    runner.start(tid, _vacancy(), _questions(n=1, max_follow_ups=0))
    runner.send(tid, button="accept")

    s_q = runner.send(tid, text="¿El puesto es presencial?")
    assert s_q["phase"] == PHASE_INTERVIEWING
    assert s_q["current_idx"] == 0
    assert s_q["answers"] == []
    joined = " ".join(s_q["outbound"])
    assert "presencial" in joined.lower()  # respondió la duda
    assert "Volviendo" in joined           # re-preguntó

    s_a = runner.send(tid, text="Mi respuesta concreta.")
    assert s_a["phase"] == PHASE_AWAITING_DOCS
    assert len(s_a["answers"]) == 1


# ── Tests unitarios de scorecard ─────────────────────────────────────────────────

def test_weighted_total_and_semaphore():
    answers = [
        {"score": 90, "weight": 2.0},
        {"score": 60, "weight": 1.0},
        {"score": None, "weight": 1.0},  # sin score → se ignora
    ]
    total = weighted_total(answers)
    assert total == pytest.approx(80.0)  # (90*2 + 60*1) / 3
    assert compute_semaphore(total, green_min=75, yellow_min=50) == "green"
    assert compute_semaphore(60, green_min=75, yellow_min=50) == "yellow"
    assert compute_semaphore(30, green_min=75, yellow_min=50) == "red"
