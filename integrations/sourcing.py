"""Conectores de sourcing: traen postulantes (con su CV ya parseado) de una
plataforma de empleo.

`SimulatedConnector` lee un fixture JSON (postulantes de "Bumeran"). El contrato
`SourcingConnector` permite reemplazarlo por un cliente real (API de Bumeran/
LinkedIn) sin tocar el resto del flujo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_FIXTURE = Path(__file__).parent / "fixtures" / "bumeran_applicants.json"


@dataclass
class ApplicantProfile:
    """Postulante traído de la plataforma, con su CV ya parseado."""

    external_id: str
    name: str
    cv_profile: dict[str, Any] = field(default_factory=dict)
    # chat_id de Telegram si la plataforma lo provee (para contacto real en demo).
    channel_user_id: str = ""

    @property
    def platform_user_id(self) -> str:
        """Id usado como channel_user_id del candidato (chat real si existe, si no el id de plataforma)."""
        return self.channel_user_id or self.external_id


class SourcingConnector(Protocol):
    name: str

    def fetch_applicants(self, vacancy: dict[str, Any]) -> list[ApplicantProfile]: ...


class SimulatedConnector:
    """Conector de demo: postulantes desde un fixture, filtrados por título de vacante.

    No fabrica chats reales: el `channel_user_id` es el id de plataforma. El redirect a un
    chat real para el demo se resuelve al **contactar** (no al importar), para no duplicar
    candidatos.
    """

    name = "bumeran"

    def __init__(self, fixture: Path = _FIXTURE) -> None:
        self._fixture = fixture

    def fetch_applicants(self, vacancy: dict[str, Any]) -> list[ApplicantProfile]:
        data = json.loads(self._fixture.read_text(encoding="utf-8"))
        rows = data.get(vacancy.get("title", ""), [])
        applicants: list[ApplicantProfile] = []
        for i, row in enumerate(rows):
            cv = {
                "name": row.get("name", ""),
                "email": row.get("email", ""),
                "phone": row.get("phone", ""),
                "headline": row.get("headline", ""),
                "education": row.get("education", {}),
                "years_experience": row.get("years_experience"),
                "skills": row.get("skills", []),
                "location": row.get("location", ""),
                "salary_expectation": row.get("salary_expectation", ""),
                "raw_cv_text": row.get("raw_cv_text", ""),
            }
            applicants.append(
                ApplicantProfile(
                    external_id=row.get("external_id", f"sim-{i}"),
                    name=row.get("name", ""),
                    cv_profile=cv,
                    channel_user_id=str(row.get("channel_user_id") or ""),
                )
            )
        return applicants


def get_connector(settings: Any) -> SourcingConnector:
    """Factory del conector según la config (default: simulado)."""
    return SimulatedConnector()
