"""Demo de consola del agente de selección (sin Telegram ni Supabase).

Usos:
  uv run python scripts/demo.py            # entrevista interactiva (escribís vos)
  uv run python scripts/demo.py --alberto  # reproduce las 6 respuestas reales de Alberto

Usa el LLM real si hay OPENAI_API_KEY en el .env; si no, cae a un fake determinista.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from agente.graph import make_memory_runner  # noqa: E402
from agente.state import PHASE_FINISHED  # noqa: E402
from evaluation.scorecard import semaphore_emoji  # noqa: E402

# Vacante demo (espejo del seed supabase/migrations/0002_seed_demo.sql).
VACANCY = {
    "title": "Analista de Automatizaciones e IA",
    "intro_message": (
        "Hola 👋 Te habla SofIA, del equipo de Atracción de Talento.\n"
        "Aplicaste a la vacante de *Analista de Automatizaciones e IA*. ¿Deseas continuar? "
        "(responde Acepto / No interesado)"
    ),
    "company_info": (
        "Empresa del sector retail. Modalidad presencial en Santiago de Surco, Lima. "
        "El rol diseña e implementa automatizaciones e IA para optimizar procesos."
    ),
    "semaphore_thresholds": {"green_min": 75, "yellow_min": 50},
}

QUESTIONS = [
    ("¿Cuál es tu nivel de estudios y qué carrera cursaste?", "Formación afín (Ing. Sistemas/Software/Computación).", 1.0, 1),
    ("¿Cuánto tiempo de experiencia tienes en automatizaciones e IA?", "Mínimo 2 años de experiencia específica.", 1.5, 1),
    ("¿Estás disponible para trabajar presencial en Santiago de Surco, Lima?", "Disponibilidad presencial.", 1.0, 0),
    ("¿Cuál es tu dominio en RPA, programación, IA, cloud, BD, APIs, ágil, DevOps? Con ejemplos.", "Amplitud técnica sustentada con herramientas y proyectos.", 2.0, 1),
    ("Contame una automatización/IA reciente: problema, arquitectura, integración y resultados.", "Caso real end-to-end con impacto medible.", 2.0, 1),
    ("¿Cuál es tu pretensión salarial mínima (monto, bruto/neto, moneda)?", "Pretensión clara y razonable (informativo).", 0.5, 0),
]

ALBERTO_ANSWERS = [
    "Soy Ingeniero de Sistemas titulado de la Universidad Nacional San Luis Gonzaga de Ica.",
    "En automatización e IA casi 2 años. En BBVA automaticé validaciones de datos en pipelines "
    "financieros y rediseñé flujos As-Is/To-Be; hoy como freelance desarrollo agentes de IA, "
    "automatizaciones con n8n y sistemas RAG.",
    "Sí, sin problema.",
    "RPA y flujos con n8n (certificación oficial 2026): webhooks, conectores a Google Workspace, "
    "correo, Telegram y bases de datos; automaticé pipelines de ingesta y controles de calidad. "
    "Programación: Python (PySpark para datos y agentes de IA). Cloud y APIs, bases de datos, "
    "metodologías ágiles y control de versiones con Git.",
    "Un cliente perdía horas buscando en contratos y manuales. Construí un asistente RAG que sube "
    "PDF/Word/TXT y responde en lenguaje natural citando la página exacta. Arquitectura: Python, "
    "LangChain, Chroma con búsqueda híbrida (BM25 + semántica) y re-ranking con cross-encoder; "
    "inferencia con Groq + Qwen3. Resultado: consultas de minutos a segundos.",
    "Mi pretensión salarial es de S/4,500 brutos mensuales en soles.",
]


def _build_questions():
    return [
        {
            "question_id": f"q{i}",
            "position": i,
            "text": text,
            "criterion": criterion,
            "weight": weight,
            "max_follow_ups": mfu,
        }
        for i, (text, criterion, weight, mfu) in enumerate(QUESTIONS, start=1)
    ]


def _get_llm():
    import os

    if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_API_KEY") != "tu_api_key_aqui":
        from orquestacion.llm import build_default_llm

        print("· LLM real (OPENAI_API_BASE/MODEL del .env)\n")
        return build_default_llm()
    from tests.test_interview import FakeLLM

    print("· Sin OPENAI_API_KEY → usando FakeLLM determinista (score fijo)\n")
    return FakeLLM(score=88)


def _print_out(state):
    for msg in state.get("outbound", []):
        print(f"🤖 {msg}\n")
    if state.get("show_consent_buttons"):
        print("   [ Acepto ]  [ No interesado ]\n")


def _print_scorecard(state):
    sc = state.get("scorecard") or {}
    print("=" * 70)
    print(f"SCORECARD — {VACANCY['title']}")
    print(f"Total: {sc.get('total_score')}/100   "
          f"{semaphore_emoji(sc.get('semaphore', ''))} {sc.get('semaphore', '').upper()}")
    print("-" * 70)
    for c in sc.get("per_criterion", []):
        print(f"  [{c.get('score')}/100] {c.get('criterion')}")
        print(f"     {c.get('justification')}")
    print("-" * 70)
    print(f"Resumen: {sc.get('summary')}")
    print(f"Recomendación: {sc.get('recommendation')}")
    print("=" * 70)


_FOLLOW_UP_EXPANSION = (
    "Claro, te amplío con un ejemplo concreto: integré n8n con un modelo de IA y una base de datos "
    "para automatizar la validación y clasificación de documentos, orquestando webhooks, el LLM y "
    "Postgres en un mismo flujo, con notificaciones automáticas de resultados."
)


def run_alberto():
    runner = make_memory_runner(_get_llm())
    tid = "demo:alberto"
    _print_out(runner.start(tid, VACANCY, _build_questions()))
    print("👤 Acepto\n")
    _print_out(runner.send(tid, button="accept"))
    state = runner.get_state(tid)

    # Conducido por índice de pregunta: si el agente pide un follow-up (el índice no
    # avanza), respondemos con una ampliación en vez de consumir la respuesta siguiente.
    guard = 0
    while state.get("phase") != PHASE_FINISHED and guard < 30:
        guard += 1
        idx = state.get("current_idx", 0)
        answered = len(state.get("answers") or [])
        is_follow_up = answered < idx + 1 and state.get("current_answer_parts")
        if idx >= len(ALBERTO_ANSWERS):
            break
        reply = _FOLLOW_UP_EXPANSION if is_follow_up else ALBERTO_ANSWERS[idx]
        print(f"👤 {reply}\n")
        state = runner.send(tid, text=reply)
        _print_out(state)
    if state.get("phase") == PHASE_FINISHED:
        _print_scorecard(state)


def run_interactive():
    runner = make_memory_runner(_get_llm())
    tid = "demo:interactive"
    _print_out(runner.start(tid, VACANCY, _build_questions()))
    while True:
        try:
            text = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        button = "accept" if text.lower() == "acepto" else ("decline" if "no interes" in text.lower() else None)
        state = runner.send(tid, text=None if button else text, button=button)
        _print_out(state)
        if state.get("phase") == PHASE_FINISHED:
            _print_scorecard(state)
            break


if __name__ == "__main__":
    load_dotenv()
    if "--alberto" in sys.argv:
        run_alberto()
    else:
        run_interactive()
