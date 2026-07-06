"""Pre-filtro automático del CV contra los requisitos de la vacante.

Se ejecuta al importar postulantes (sourcing): decide si el CV se acerca a lo
solicitado y produce un puntaje (gate) + verdict. Solo los aptos se contactan.
Degrada con gracia: sin LLM o JSON inválido usa una heurística determinista.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from orquestacion.llm import LLM, complete_staged, parse_json_object
from agente.prompts import PRESCREEN_CV_PROMPT

# ── Minimización de PII antes de salir al proveedor LLM (auditoría v4, R1 · Ley 29733) ──
# El gate evalúa FIT contra la vacante, no identidad: los identificadores de contacto no
# aportan señal y no deben viajar al proveedor. Se quitan las claves directas y se
# enmascaran correos/teléfonos incrustados en el texto libre del CV. Limitación declarada:
# el nombre dentro de raw_cv_text no se detecta (NER quedaría para una iteración futura).
_PII_DIRECT_KEYS = frozenset({"name", "full_name", "email", "phone", "external_id", "telegram", "chat_id"})
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Solo secuencias con ≥9 dígitos (celular peruano): no confundir con rangos de años (8).
_PHONE_CANDIDATE_RE = re.compile(r"\+?[\d(][\d\s().-]{6,}\d")


def _mask_contact_text(text: str) -> str:
    text = _EMAIL_RE.sub("[correo]", text)

    def _maybe_phone(m: re.Match[str]) -> str:
        return "[teléfono]" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0)

    return _PHONE_CANDIDATE_RE.sub(_maybe_phone, text)


def profile_for_llm(cv_profile: dict[str, Any]) -> dict[str, Any]:
    """Copia del cv_profile SIN identificadores de contacto, apta para el prompt."""
    clean = {k: v for k, v in cv_profile.items() if k not in _PII_DIRECT_KEYS}
    for key, value in clean.items():
        if isinstance(value, str):
            clean[key] = _mask_contact_text(value)
    return clean

# Carreras/áreas afines (heurística de respaldo).
_TECH_CAREERS = (
    "sistemas", "software", "computaci", "informát", "industrial", "electrón",
    "telecomunic", "datos", "mecatrón",
)
_TECH_SKILLS = (
    "python", "rpa", "uipath", "power automate", "blue prism", "ia", "ml", "llm",
    "api", "cloud", "azure", "aws", "gcp", "sql", "n8n", "javascript", "devops",
)


@dataclass
class PrescreenResult:
    pre_score: float
    verdict: str  # pass | borderline | reject
    summary: str
    per_requirement: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pre_score": self.pre_score,
            "verdict": self.verdict,
            "summary": self.summary,
            "per_requirement": self.per_requirement,
        }

    @property
    def is_fit(self) -> bool:
        return self.verdict != "reject"


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _verdict_from_score(score: float, pass_min: int) -> str:
    if score >= pass_min + 15:
        return "pass"
    if score >= pass_min:
        return "borderline"
    return "reject"


def _heuristic(cv_profile: dict[str, Any], pass_min: int) -> PrescreenResult:
    """Respaldo determinista: años de experiencia + afinidad de carrera/skills."""
    years = cv_profile.get("years_experience") or 0
    try:
        years = float(years)
    except (TypeError, ValueError):
        years = 0.0
    career = str((cv_profile.get("education") or {}).get("career", "")).lower()
    skills = " ".join(str(s).lower() for s in (cv_profile.get("skills") or []))

    career_ok = any(k in career for k in _TECH_CAREERS)
    skills_hits = sum(1 for k in _TECH_SKILLS if k in skills)

    score = 0.0
    score += min(years, 4) / 4 * 40        # hasta 40 pts por experiencia (tope 4 años)
    score += 25 if career_ok else 0         # 25 pts por carrera afín
    score += min(skills_hits, 5) / 5 * 35   # hasta 35 pts por skills relevantes
    score = round(score)

    return PrescreenResult(
        pre_score=score,
        verdict=_verdict_from_score(score, pass_min),
        summary=(
            f"Evaluación heurística (sin LLM): {years:.0f} año(s) de experiencia, "
            f"carrera {'afín' if career_ok else 'no afín'}, {skills_hits} skill(s) relevante(s)."
        ),
        per_requirement=[
            {"requirement": "Experiencia mínima", "met": years >= 2, "note": f"{years:.0f} año(s)"},
            {"requirement": "Formación afín", "met": career_ok, "note": career or "—"},
            {"requirement": "Habilidades técnicas", "met": skills_hits >= 2, "note": f"{skills_hits} relevantes"},
        ],
    )


def prescreen_cv(
    llm: LLM | None,
    *,
    vacancy: dict[str, Any],
    cv_profile: dict[str, Any],
    criteria: list[str] | None = None,
    pass_min: int = 60,
) -> PrescreenResult:
    """Evalúa el CV contra la vacante y devuelve puntaje + verdict (apto si verdict != reject)."""
    if llm is None:
        return _heuristic(cv_profile, pass_min)
    try:
        crit_text = "\n".join(f"- {c}" for c in (criteria or []) if c) or "- (no especificados)"
        raw = complete_staged(
            llm,
            PRESCREEN_CV_PROMPT.format(
                vacancy_title=vacancy.get("title", ""),
                requirements=vacancy.get("requirements", "") or "(no especificados)",
                criteria=crit_text,
                cv_profile=json.dumps(profile_for_llm(cv_profile), ensure_ascii=False, indent=2),
            ),
            "prescreen",
        )
        data = parse_json_object(raw)
        score = _clamp(data.get("pre_score"))
        per_req = data.get("per_requirement") or []
        if not isinstance(per_req, list):
            per_req = []
        return PrescreenResult(
            pre_score=score,
            verdict=_verdict_from_score(score, pass_min),
            summary=str(data.get("summary", "")).strip()[:600] or "Sin resumen.",
            per_requirement=per_req[:12],
        )
    except Exception:
        return _heuristic(cv_profile, pass_min)
