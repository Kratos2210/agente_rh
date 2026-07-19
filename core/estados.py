"""Catálogo ÚNICO de estados del candidato (auditoría v4, R5).

Los `status` son strings libres en la DB (columna text) y se emiten desde varios
sitios (endpoints, `_sync_business`, scheduler, sourcing). Antes de este módulo un
typo compilaba y rompía silenciosamente el kanban y los barridos. Ahora TODO write
de `candidates.status` pasa por `ensure_valid_status` (guard en
`db.repositories.update_candidate`): un estado desconocido lanza ValueError en el
punto de escritura, donde el stack trace señala al culpable.

Espejo frontend: `frontend/src/lib/stages.ts` (ALL_KNOWN + KANBAN_COLUMNS). Si se
agrega un estado aquí, agregarlo también allá (y viceversa).
"""

from __future__ import annotations

# ── Sourcing / pre-filtro ────────────────────────────────────────────────────────
STATUS_PENDING = "pending"                     # legado: creado sin pasar por sourcing
STATUS_SOURCED = "sourced"                     # importado del portal, aún sin gate
STATUS_PRESCREEN_PASSED = "prescreen_passed"   # apto CV · por contactar
STATUS_PRESCREEN_REJECTED = "prescreen_rejected"

# ── Contacto y entrevista ────────────────────────────────────────────────────────
STATUS_INVITED = "invited"                     # saludo + botones enviados
STATUS_CONSENTED = "consented"                 # aceptó (transitorio; el motor avanza)
STATUS_INTERVIEWING = "interviewing"
STATUS_FINISHED = "finished"                   # scorecard generado (docs pueden faltar)

# ── Decisión RR.HH. + agendamiento multi-etapa ───────────────────────────────────
STATUS_ADVANCED = "advanced"                   # avanzar sin agendamiento activo
STATUS_SCHEDULING = "scheduling"               # coordinando horario con RR.HH. (hr)
STATUS_SCHEDULED = "scheduled"
STATUS_LEAD_SCHEDULING = "lead_scheduling"     # etapa líder del proyecto
STATUS_LEAD_SCHEDULED = "lead_scheduled"
STATUS_MGR_SCHEDULING = "mgr_scheduling"       # etapa gerencia
STATUS_MGR_SCHEDULED = "mgr_scheduled"

# ── Cierre del proceso ───────────────────────────────────────────────────────────
STATUS_MEDICAL_PENDING = "medical_pending"     # aprobó gerencia; falta cita médica
STATUS_MEDICAL_SCHEDULED = "medical_scheduled"
STATUS_HIRED = "hired"                         # terminal (el onboarding no cambia status)
STATUS_REJECTED = "rejected"

# ── Salidas fuera del camino feliz ───────────────────────────────────────────────
STATUS_DECLINED = "declined"                   # rechazó la invitación explícitamente
STATUS_NO_RESPONSE = "no_response"             # cerrado por inactividad
STATUS_NO_SHOW = "no_show"                     # no asistió a la entrevista agendada

CANDIDATE_STATUSES: frozenset[str] = frozenset({
    STATUS_PENDING, STATUS_SOURCED, STATUS_PRESCREEN_PASSED, STATUS_PRESCREEN_REJECTED,
    STATUS_INVITED, STATUS_CONSENTED, STATUS_INTERVIEWING, STATUS_FINISHED,
    STATUS_ADVANCED, STATUS_SCHEDULING, STATUS_SCHEDULED,
    STATUS_LEAD_SCHEDULING, STATUS_LEAD_SCHEDULED,
    STATUS_MGR_SCHEDULING, STATUS_MGR_SCHEDULED,
    STATUS_MEDICAL_PENDING, STATUS_MEDICAL_SCHEDULED,
    STATUS_HIRED, STATUS_REJECTED,
    STATUS_DECLINED, STATUS_NO_RESPONSE, STATUS_NO_SHOW,
})


def ensure_valid_status(status: str) -> str:
    """Devuelve el status si es del catálogo; ValueError si no (typo → falla ruidosa)."""
    if status not in CANDIDATE_STATUSES:
        raise ValueError(
            f"Estado de candidato desconocido: {status!r}. "
            f"Catálogo en core/estados.py (espejo: frontend/src/lib/stages.ts)."
        )
    return status
