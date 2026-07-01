"""Verificación end-to-end del proceso multi-etapa contra la DB real (sin Telegram).

Drivea el flujo completo por el MISMO `InterviewService` que usa el bot (checkpointer
Postgres real + repositorios Supabase reales + SimulatedScheduler), simulando al candidato
con `InboundMessage` y a RR.HH. con las mismas operaciones que hacen los endpoints
(`initiate_scheduling` / `set_meeting_attendance` / `save_stage_feedback` / `update_candidate`).

Recorre: entrevista → scorecard → Fase 1 (RR.HH., virtual) → Fase 2 (líder, presencial) →
Fase 3 (gerencia, presencial) → contratado. Al final imprime el estado y limpia el candidato.

Uso:  uv run python scripts/verify_multistage.py
Requiere Supabase local arriba + DATABASE_URL + (opcional) OPENAI_API_KEY (Groq) en .env.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.graph import make_postgres_runner  # noqa: E402
from agent.service import InterviewService  # noqa: E402
from agent.state import PHASE_FINISHED  # noqa: E402
from channels.base import CHANNEL_TELEGRAM, InboundMessage  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.client import get_database_url, get_supabase  # noqa: E402
from integrations.scheduling import SimulatedScheduler  # noqa: E402
from src.config import get_settings  # noqa: E402

ANSWERS = [
    "Soy bachiller en Ingeniería de Sistemas.",
    "Tengo 4 años automatizando procesos con n8n, Python y LLMs.",
    "Sí, tengo disponibilidad inmediata para modalidad presencial en Lima.",
    "Implementé un flujo n8n + LLM + Postgres que clasifica y valida documentos con notificaciones automáticas, reduciendo el tiempo de revisión en 70%.",
    "Uso n8n, LangChain, Supabase, Docker y las APIs de OpenAI/Anthropic.",
    "Mi pretensión es S/ 6000 mensuales, bruto.",
]
FOLLOW_UP = ("Te amplío con un caso concreto: orquesté webhooks, el LLM y Postgres en un mismo "
             "flujo de n8n con manejo de errores y reintentos, y notificaciones a Slack.")


def _get_llm():
    # La key vive en el .env → la lee Settings (pydantic), no os.getenv.
    key = get_settings().openai_api_key
    if key and key not in ("tu_api_key_aqui", "lm-studio"):
        from agent.llm import build_default_llm
        print(f"· LLM real ({get_settings().openai_model} vía {get_settings().openai_api_base})\n")
        return build_default_llm()
    from tests.test_interview import FakeLLM
    print("· Sin OPENAI_API_KEY real → FakeLLM determinista (score 88)\n")
    return FakeLLM(score=88)


def _run_interview(service: InterviewService, chat_id: str, name: str):
    ch = CHANNEL_TELEGRAM
    im = lambda **kw: InboundMessage(channel=ch, chat_id=chat_id, display_name=name, **kw)  # noqa: E731
    print("→ Primer contacto"); service.process(im())
    print("→ Acepto"); service.process(im(button="accept"))
    tid = f"{ch}:{chat_id}"
    guard = 0
    while guard < 40:
        guard += 1
        st = service.runner.get_state(tid)
        if st.get("phase") == PHASE_FINISHED:
            break
        idx = st.get("current_idx", 0)
        answered = len(st.get("answers") or [])
        is_follow_up = answered < idx + 1 and st.get("current_answer_parts")
        if idx >= len(ANSWERS) and not is_follow_up:
            break
        reply = FOLLOW_UP if is_follow_up else ANSWERS[min(idx, len(ANSWERS) - 1)]
        service.process(im(text=reply))
    return service.runner.get_state(tid)


def _pick_slot(service, chat_id, name):
    """Simula que el candidato elige el primer horario propuesto."""
    service.process(InboundMessage(channel=CHANNEL_TELEGRAM, chat_id=chat_id, display_name=name, text="1"))


def _advance(service, candidate_id, vacancy, stage, next_stage, modality, name):
    """Réplica de POST /advance-stage (aprobar): feedback + agenda la etapa siguiente."""
    conv = repo.get_conversation_by_candidate(candidate_id)
    repo.save_stage_feedback({
        "candidate_id": candidate_id, "conversation_id": conv["id"], "stage": stage,
        "feedback": f"Buen desempeño en la entrevista de {stage}.", "decision": "approved",
        "decided_email": "rrhh@sifrah.com",
    })
    cand = repo.get_candidate(candidate_id)
    service.initiate_scheduling(cand, vacancy, stage=next_stage, modality=modality)
    _pick_slot(service, cand["channel_user_id"], name)


def _mark_attended(candidate_id, stage):
    conv = repo.get_conversation_by_candidate(candidate_id)
    m = repo.get_meeting_by_conversation_stage(conv["id"], stage)
    repo.set_meeting_attendance(m["id"], "attended")
    return m


def main():
    settings = get_settings()
    runner = make_postgres_runner(_get_llm(), get_database_url())
    service = InterviewService(runner, scheduler=SimulatedScheduler(), settings=settings)

    vacancy = repo.get_default_open_vacancy()
    assert vacancy, "No hay vacante abierta. Aplica los seeds."
    assert vacancy.get("lead_recruiter_id") and vacancy.get("manager_recruiter_id"), \
        "La vacante demo no tiene líder/gerencia asignados (aplica 0019)."

    chat_id = f"9{uuid.uuid4().int % 10**8:08d}"  # chat numérico ficticio (no se envía a Telegram real)
    name = "Verif MultiEtapa"
    print(f"Candidato de prueba: {name} (chat {chat_id})\n")

    st = _run_interview(service, chat_id, name)
    sc = st.get("scorecard") or {}
    cand = repo.get_or_create_candidate(vacancy["id"], CHANNEL_TELEGRAM, chat_id, name)
    cid = cand["id"]
    print(f"✓ Entrevista completa · scorecard {sc.get('total_score')}/100 {sc.get('semaphore')} · status={repo.get_candidate(cid)['status']}\n")

    # Fase 1 (RR.HH., virtual): RR.HH. "Continuar" → agenda → candidato elige horario.
    service.initiate_scheduling(cand, vacancy, stage="hr", modality="virtual")
    _pick_slot(service, chat_id, name)
    print(f"✓ Fase 1 agendada · status={repo.get_candidate(cid)['status']}")
    _mark_attended(cid, "hr")

    # Fase 2 (líder, presencial): aprobar hr → agenda lead → candidato elige.
    _advance(service, cid, vacancy, "hr", "lead", "onsite", name)
    print(f"✓ Fase 2 agendada · status={repo.get_candidate(cid)['status']}")
    _mark_attended(cid, "lead")

    # Fase 3 (gerencia, presencial): aprobar lead → agenda manager → candidato elige.
    _advance(service, cid, vacancy, "lead", "manager", "onsite", name)
    print(f"✓ Fase 3 agendada · status={repo.get_candidate(cid)['status']}")
    _mark_attended(cid, "manager")

    # Decisión final: aprobar manager → contratado.
    conv = repo.get_conversation_by_candidate(cid)
    repo.save_stage_feedback({
        "candidate_id": cid, "conversation_id": conv["id"], "stage": "manager",
        "feedback": "Aprobado por gerencia.", "decision": "approved", "decided_email": "gerencia@sifrah.com",
    })
    repo.update_candidate(cid, {"status": "hired"})
    print(f"✓ Decisión final · status={repo.get_candidate(cid)['status']}\n")

    # ── Aserciones sobre el estado en la DB real ────────────────────────────────
    meetings = {m["stage"]: m for m in repo.list_meetings_by_candidate(cid)}
    print("Reuniones creadas:")
    for stg in ("hr", "lead", "manager"):
        m = meetings.get(stg, {})
        print(f"  {stg:8} modality={m.get('modality'):8} meet_link={'sí' if m.get('meet_link') else 'no':3} "
              f"location={m.get('location') or '—'} attendance={m.get('attendance')}")
    fb = [(f["stage"], f["decision"]) for f in repo.list_stage_feedback(cid)]
    print(f"Feedback por etapa: {fb}")

    ok = (
        repo.get_candidate(cid)["status"] == "hired"
        and set(meetings) == {"hr", "lead", "manager"}
        and bool(meetings["hr"]["meet_link"]) and meetings["hr"]["modality"] == "virtual"
        and not meetings["lead"]["meet_link"] and meetings["lead"]["modality"] == "onsite"
        and meetings["lead"]["location"]
        and meetings["manager"]["modality"] == "onsite"
        and all(m["attendance"] == "attended" for m in meetings.values())
        and fb == [("hr", "approved"), ("lead", "approved"), ("manager", "approved")]
    )
    print("\n" + ("✅ END-TO-END OK" if ok else "❌ FALLÓ alguna aserción"))

    # ── Limpieza (borra el candidato de prueba + checkpoint) ─────────────────────
    if conv.get("langgraph_thread_id"):
        try:
            repo.delete_langgraph_checkpoint(conv["langgraph_thread_id"])
        except Exception:  # noqa: BLE001
            pass
    get_supabase().table("candidates").delete().eq("id", cid).execute()
    print("🧹 Candidato de prueba eliminado.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
