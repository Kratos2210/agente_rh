# `agente/` — Agente cíclico (LangGraph)

Componente **Agente cíclico** de la rúbrica: la máquina de estados de la entrevista, durable
(checkpointer Postgres, hilo `canal:chat`). Lógica pura y testeable; los canales/DB/LLM son adaptadores.

| Archivo | Rol |
|---|---|
| `state.py` | Estado de la conversación + fases (`greeting`/`interviewing`/`awaiting_docs`/`scheduling`/`scheduled`/`closed`/`finished`) + `QuestionSpec`. |
| `graph.py` | Grafo LangGraph + runners (`make_memory_runner` tests, `make_postgres_runner` prod). |
| `nodes.py` | Nodos: turno de entrevista, follow-ups, dudas (tope + caché), agendamiento, timeouts, límites de iteración. |
| `prompts.py` | Todos los prompts + `PROMPT_VERSION` (gate en CI) + few-shot + marcos anti-inyección. |
| `service.py` | `InterviewService`: núcleo agnóstico de canal; proyecta el estado a Supabase, notifica, agenda. |
| `sourcing_service.py` | Importa postulantes, pre-filtra (gate de CV) y contacta a los aptos. |

Flujos cíclicos: repregunta ante respuestas vagas, resuelve dudas del candidato (RAG), reintenta la
elección de horario con escalamiento, y encadena las 3 etapas de agendamiento (RR.HH. → Líder → Gerencia).

**Cómo ejecutar (sin infra):** `uv run python scripts/demo.py --alberto`.
