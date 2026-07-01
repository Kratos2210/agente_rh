"""Envoltura LangGraph del motor de entrevista + runner durable por candidato.

El grafo tiene un único nodo "turn" que, según `mode`, hace el primer contacto
(`start`) o procesa un mensaje (`handle_turn`). El estado se persiste con un
checkpointer (PostgresSaver en producción, MemorySaver en tests) keyed por
thread_id = "{channel}:{chat_id}" → la conversación es reanudable y asíncrona.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, StateGraph

from agent import nodes
from agent.llm import LLM
from agent.state import InterviewState, QuestionSpec, new_state


def build_interview_graph(llm: LLM, checkpointer: Optional[Any] = None):
    """Compila el grafo de entrevista con el LLM y checkpointer dados."""

    def _turn(state: InterviewState) -> InterviewState:
        mode = state.get("mode")
        if mode == "start":
            out = nodes.start(state)
        elif mode == "schedule_start":
            out = nodes.start_scheduling(state)
        else:
            out = nodes.handle_turn(state, llm)
        out["mode"] = ""
        return out

    g = StateGraph(InterviewState)
    g.add_node("turn", _turn)
    g.set_entry_point("turn")
    g.add_edge("turn", END)
    return g.compile(checkpointer=checkpointer)


class InterviewRunner:
    """Driver de alto nivel: un grafo compilado + un checkpointer durable."""

    def __init__(self, llm: LLM, checkpointer: Any) -> None:
        self.llm = llm
        self.graph = build_interview_graph(llm, checkpointer)

    @staticmethod
    def _cfg(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def start(
        self,
        thread_id: str,
        vacancy: dict[str, Any],
        questions: list[QuestionSpec],
        cv_profile: Optional[dict[str, Any]] = None,
    ) -> InterviewState:
        """Primer contacto. Inicializa el estado de la entrevista."""
        init = new_state(vacancy, questions, cv_profile)
        init["mode"] = "start"
        return self.graph.invoke(init, self._cfg(thread_id))

    def send(
        self,
        thread_id: str,
        *,
        text: Optional[str] = None,
        button: Optional[str] = None,
        document: Optional[dict[str, Any]] = None,
        timeout: bool = False,
        start_scheduling: Optional[list[str]] = None,
        recruiter: Optional[dict[str, Any]] = None,
    ) -> InterviewState:
        """Procesa un mensaje del candidato y avanza la entrevista un turno.

        `timeout=True` cierra la conversación por inactividad (sin respuesta del candidato).
        `start_scheduling` (lista de horarios ISO) abre la fase de coordinación de entrevista."""
        if start_scheduling is not None:
            payload: InterviewState = {
                "mode": "schedule_start",
                "proposed_slots": start_scheduling,
                "recruiter": recruiter or {},
            }
            return self.graph.invoke(payload, self._cfg(thread_id))
        payload = {
            "mode": "turn",
            "pending_input": text,
            "pending_button": button,
            "pending_document": document,
            "pending_timeout": timeout,
        }
        return self.graph.invoke(payload, self._cfg(thread_id))

    def get_state(self, thread_id: str) -> InterviewState:
        snapshot = self.graph.get_state(self._cfg(thread_id))
        return snapshot.values if snapshot else {}


def make_memory_runner(llm: LLM) -> InterviewRunner:
    """Runner con checkpointer en memoria (tests / consola)."""
    from langgraph.checkpoint.memory import MemorySaver

    return InterviewRunner(llm, MemorySaver())


def make_postgres_runner(llm: LLM, db_url: str) -> InterviewRunner:
    """Runner con checkpointer durable en Postgres (Supabase) — para producción.

    Crea las tablas de checkpoints la primera vez (`setup()`). El pool queda vivo
    durante toda la vida del proceso (el bot lo usa en cada mensaje entrante).
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=db_url,
        max_size=10,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()
    return InterviewRunner(llm, checkpointer)
