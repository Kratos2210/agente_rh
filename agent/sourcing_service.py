"""Servicio de sourcing: importa postulantes de la plataforma, los pre-filtra por
CV y deja contactados (o simula el contacto) solo a los aptos.

Lo dispara el dashboard (botón "Sincronizar postulantes"). Es síncrono (LLM +
supabase-py); el endpoint lo corre en un hilo worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from channels.base import CHANNEL_TELEGRAM
from db import repositories
from evaluation.prescreen import prescreen_cv
from integrations.sourcing import SourcingConnector
from src.logging_config import get_logger

logger = get_logger(__name__)

# Contacta a un candidato apto (envía el primer mensaje). Devuelve True si se envió de verdad.
ContactFn = Callable[[dict], bool]

# Fases ya posteriores al pre-filtro: el sync nunca las retrocede (idempotencia).
_ADVANCED_STATES = {
    "invited", "consented", "interviewing", "finished", "advanced", "rejected", "declined",
}


@dataclass
class SyncReport:
    imported: int = 0
    passed: int = 0
    rejected: int = 0
    contacted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "imported": self.imported,
            "passed": self.passed,
            "rejected": self.rejected,
            "contacted": self.contacted,
        }


def _criteria(vacancy_id: str) -> list[str]:
    return [q.get("criterion", "") for q in repositories.get_vacancy_questions(vacancy_id)]


def sync_applicants(
    vacancy_id: str,
    *,
    llm: Any,
    connector: SourcingConnector,
    channel: str = CHANNEL_TELEGRAM,
    pass_min: int = 60,
    contact_fn: Optional[ContactFn] = None,
) -> SyncReport:
    """Importa postulantes, corre el pre-filtro del CV y contacta a los aptos."""
    vacancy = repositories.get_vacancy(vacancy_id)
    if not vacancy:
        raise ValueError("Vacante no encontrada")

    criteria = _criteria(vacancy_id)
    report = SyncReport()

    for applicant in connector.fetch_applicants(vacancy):
        candidate = repositories.get_or_create_candidate(
            vacancy_id, channel, applicant.platform_user_id, applicant.name, source=connector.name
        )
        existing_status = candidate.get("status")
        repositories.update_candidate(
            candidate["id"], {"cv_profile": applicant.cv_profile, "name": applicant.name}
        )
        report.imported += 1

        result = prescreen_cv(
            llm, vacancy=vacancy, cv_profile=applicant.cv_profile, criteria=criteria, pass_min=pass_min
        )
        _record_prescreen_usage(llm, vacancy_id, candidate["id"])
        if result.is_fit:
            report.passed += 1
        else:
            report.rejected += 1

        # Idempotencia: si ya avanzó de fase (contactado o más), solo refresca el prescreen
        # informativo; nunca lo retrocede ni lo re-contacta.
        if existing_status in _ADVANCED_STATES:
            repositories.update_candidate(candidate["id"], {"prescreen": result.to_dict()})
            continue

        new_status = "prescreen_passed" if result.is_fit else "prescreen_rejected"
        repositories.update_candidate(
            candidate["id"], {"prescreen": result.to_dict(), "status": new_status}
        )

        # Contacto automático opcional (solo aptos recién pasados).
        if result.is_fit and contact_fn:
            try:
                # Relee el candidato con su estado/prescreen ya actualizado.
                fresh = repositories.get_candidate(candidate["id"]) or candidate
                if contact_fn(fresh):
                    report.contacted += 1
            except Exception:  # noqa: BLE001 — el contacto no debe tumbar la sincronización
                logger.exception("Fallo al contactar postulante %s", candidate.get("id"))

    logger.info("Sourcing vacante %s: %s", vacancy_id, report.as_dict())
    return report


def _record_prescreen_usage(llm: Any, vacancy_id: str, candidate_id: str) -> None:
    drain = getattr(llm, "drain", None)
    if not callable(drain):
        return
    model = getattr(llm, "model", "")
    for stage, tokens in (drain() or {}).items():
        repositories.record_usage(
            stage, model, tokens, vacancy_id=vacancy_id, candidate_id=candidate_id
        )
