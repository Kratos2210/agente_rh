"""Reporte del scorecard al reclutador por email (SMTP).

Patrón de envío tomado de ../qrs (Gmail SMTP con STARTTLS). Genera un correo
HTML con el semáforo, el puntaje total, el desglose por criterio, el resumen y la
recomendación. Degrada con gracia: si falta configuración SMTP, no hace nada.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _esc
from typing import Any

from evaluation.scorecard import semaphore_emoji
from core.config import Settings
from core.logging_config import get_logger

logger = get_logger(__name__)

_SEMAPHORE_COLOR = {"green": "#16a34a", "yellow": "#d97706", "red": "#dc2626"}
_SEMAPHORE_LABEL = {"green": "AVANZA", "yellow": "REVISAR", "red": "NO AVANZA"}


def _score_txt(score: Any) -> str:
    return f"{score:.0f}" if isinstance(score, (int, float)) else "s/d"


def _render_html(vacancy: dict, candidate: dict, scorecard: dict) -> str:
    semaphore = scorecard.get("semaphore", "")
    color = _SEMAPHORE_COLOR.get(semaphore, "#6b7280")
    label = _SEMAPHORE_LABEL.get(semaphore, "—")
    # Seguridad (audit F3): escapar TODO dato dinámico interpolado en el HTML. Las
    # justificaciones/resumen/recomendación las genera el LLM y el nombre/vacante vienen del
    # CV/reclutador: sin escapar, un `<`/`>`/markup rompería o inyectaría el HTML del correo.
    rows = "".join(
        f"""<tr>
              <td style="padding:8px;border-bottom:1px solid #eee;font-weight:600;text-align:center">{_esc(_score_txt(c.get('score')))}</td>
              <td style="padding:8px;border-bottom:1px solid #eee">
                <div style="font-weight:600">{_esc(str(c.get('criterion','')))}</div>
                <div style="color:#555;font-size:13px;margin-top:2px">{_esc(str(c.get('justification','')))}</div>
              </td>
            </tr>"""
        for c in scorecard.get("per_criterion", [])
    )
    name = _esc(str(candidate.get("name") or "Candidato"))
    title = _esc(str(vacancy.get("title", "")))
    total = _esc(str(scorecard.get("total_score", "")))
    summary = _esc(str(scorecard.get("summary", "")))
    recommendation = _esc(str(scorecard.get("recommendation", "")))
    return f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:{color};color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Scorecard de selección</div>
          <div style="font-size:22px;font-weight:700">{semaphore_emoji(semaphore)} {label} · {total}/100</div>
        </div>
        <div style="padding:24px">
          <p style="margin:0 0 4px"><b>Candidato:</b> {name}</p>
          <p style="margin:0 0 4px"><b>Vacante:</b> {title}</p>
          <h3 style="margin:20px 0 8px">Evaluación por criterio</h3>
          <table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>
          <h3 style="margin:20px 0 8px">Resumen</h3>
          <p style="margin:0;color:#333;line-height:1.5">{summary}</p>
          <h3 style="margin:20px 0 8px">Recomendación</h3>
          <p style="margin:0;color:#333;line-height:1.5;font-weight:600">{recommendation}</p>
        </div>
      </div>
    </body></html>"""


def _render_text(vacancy: dict, candidate: dict, scorecard: dict) -> str:
    lines = [
        f"Scorecard — {vacancy.get('title','')}",
        f"Candidato: {candidate.get('name') or 'Candidato'}",
        f"Total: {scorecard.get('total_score','')}/100  "
        f"[{semaphore_emoji(scorecard.get('semaphore',''))} {_SEMAPHORE_LABEL.get(scorecard.get('semaphore',''),'—')}]",
        "",
        "Por criterio:",
    ]
    for c in scorecard.get("per_criterion", []):
        lines.append(f"  [{_score_txt(c.get('score'))}/100] {c.get('criterion','')}")
        lines.append(f"     {c.get('justification','')}")
    lines += ["", f"Resumen: {scorecard.get('summary','')}", f"Recomendación: {scorecard.get('recommendation','')}"]
    return "\n".join(lines)


# ── Primitiva de envío (LANZA ante fallo → habilita el reintento del outbox) ──────

def send_email(settings: Settings, recipients: list[str], subject: str, text: str, html: str) -> None:
    """Envía un correo multipart (texto + HTML). LANZA si falla (SMTP caído, auth, etc.)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.starttls()
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, recipients, msg.as_string())


def build_scorecard_email(
    settings: Settings, vacancy: dict, candidate: dict, scorecard: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) del scorecard, o None si SMTP/recruiter_email sin configurar."""
    if not (settings.smtp_host and settings.recruiter_email and settings.smtp_from):
        return None
    name = candidate.get("name") or "candidato"
    semaphore = scorecard.get("semaphore", "")
    subject = (
        f"[{_SEMAPHORE_LABEL.get(semaphore, '—')}] {name} · {vacancy.get('title', '')} "
        f"({scorecard.get('total_score', '')}/100)"
    )
    return (
        [settings.recruiter_email],
        subject,
        _render_text(vacancy, candidate, scorecard),
        _render_html(vacancy, candidate, scorecard),
    )


def send_scorecard_email(
    settings: Settings, vacancy: dict, candidate: dict, conversation: dict, scorecard: dict
) -> None:
    """Envía el scorecard al reclutador. No lanza: registra y sigue ante fallo.

    (El camino robusto con reintentos es notifications.outbox; esta función queda como
    envío directo para usos puntuales/tests.)"""
    built = build_scorecard_email(settings, vacancy, candidate, scorecard)
    if built is None:
        logger.info("SMTP/recruiter_email sin configurar: se omite el envío del scorecard")
        return
    try:
        send_email(settings, *built)
        logger.info("Scorecard enviado al reclutador (%s)", built[0])
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el scorecard por email")


# ── Confirmación de la reunión agendada (candidato + reclutador) ──────────────────

def _meeting_when(meeting: dict) -> str:
    """Fecha/hora legible de la reunión (a partir de scheduled_at ISO)."""
    from datetime import datetime

    from integrations.scheduling import human_slot_long

    raw = meeting.get("scheduled_at")
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        return human_slot_long(dt)
    except Exception:  # noqa: BLE001
        return str(raw or "")


def build_meeting_email(
    settings: Settings, vacancy: dict, candidate: dict, meeting: dict, recruiter: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) de la confirmación de reunión, o None si no aplica."""
    recipients = [e for e in {meeting.get("candidate_email", ""), meeting.get("recruiter_email", "")} if e]
    if not (settings.smtp_host and settings.smtp_from and recipients):
        return None

    when = _meeting_when(meeting)
    link = meeting.get("meet_link") or ""
    title = vacancy.get("title", "")
    name = candidate.get("name") or "Candidato"
    rec_name = recruiter.get("name") or "el equipo de Talento"
    cand_email = meeting.get("candidate_email") or "—"
    cand_phone = meeting.get("candidate_phone") or "—"
    rec_email = meeting.get("recruiter_email") or recruiter.get("email") or "—"
    rec_phone = meeting.get("recruiter_phone") or recruiter.get("phone") or "—"
    subject = f"Entrevista agendada · {title} · {when}"

    text = "\n".join([
        f"Entrevista agendada — {title}",
        f"Fecha y hora: {when}",
        f"Enlace de la reunión: {link}",
        "",
        f"Candidato: {name} · {cand_email} · {cand_phone}",
        f"Reclutador: {rec_name} · {rec_email} · {rec_phone}",
    ])
    # Seguridad (audit F3): escapar los datos dinámicos antes de interpolarlos en el HTML
    # (nombre/vacante/contactos vienen del CV/reclutador). `_esc` escapa también las comillas,
    # así el `link` no puede romper el atributo href.
    h_title, h_when, h_name = _esc(title), _esc(when), _esc(name)
    h_cand_email, h_cand_phone = _esc(cand_email), _esc(cand_phone)
    h_rec_name, h_rec_email, h_rec_phone = _esc(rec_name), _esc(rec_email), _esc(rec_phone)
    h_link = _esc(link)
    html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:#16a34a;color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Entrevista agendada</div>
          <div style="font-size:22px;font-weight:700">{h_title}</div>
        </div>
        <div style="padding:24px;line-height:1.6">
          <p style="margin:0 0 4px"><b>Fecha y hora:</b> {h_when}</p>
          <p style="margin:0 0 4px"><b>Candidato:</b> {h_name} &middot; {h_cand_email} &middot; {h_cand_phone}</p>
          <p style="margin:0 0 4px"><b>Reclutador:</b> {h_rec_name} &middot; {h_rec_email} &middot; {h_rec_phone}</p>
          <p style="margin:16px 0 4px"><b>Enlace de la reunión:</b></p>
          <p style="margin:0"><a href="{h_link}" style="color:#2563eb">{h_link}</a></p>
        </div>
      </div>
    </body></html>"""

    return (recipients, subject, text, html)


def send_meeting_email(
    settings: Settings, vacancy: dict, candidate: dict, meeting: dict, recruiter: dict
) -> None:
    """Confirmación de la entrevista al candidato + reclutador. No lanza (envío directo).

    El camino robusto con reintentos es notifications.outbox."""
    built = build_meeting_email(settings, vacancy, candidate, meeting, recruiter)
    if built is None:
        logger.info("SMTP/destinatarios sin configurar: se omite el correo de la reunión")
        return
    try:
        send_email(settings, *built)
        logger.info("Confirmación de entrevista enviada a %s", built[0])
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el correo de la reunión")


# ── Exámenes psicológicos (Fase 1: link + código + clave de la plataforma externa) ─

def build_psych_exam_email(
    settings: Settings, vacancy: dict, candidate: dict, exam: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) del correo de exámenes psicológicos, o None si no aplica.

    RR.HH. obtiene link/código/clave de la plataforma externa (p.ej. Multitest) y los pega;
    este correo se los reenvía al candidato con el formato de la empresa."""
    cand_email = str((candidate.get("cv_profile") or {}).get("email", "")).strip()
    if not (settings.smtp_host and settings.smtp_from and cand_email):
        return None
    link = str(exam.get("link", "")).strip()
    code = str(exam.get("code", "")).strip()
    key = str(exam.get("key", "")).strip()
    name = candidate.get("name") or "Candidato"
    title = vacancy.get("title", "")
    subject = f"Evaluación psicológica · {title}".strip(" ·")

    text = "\n".join([
        f"Estimad@ {name},",
        "",
        "A continuación encontrarás un link con pruebas que deberás resolver para que podamos "
        "evaluar tu potencial.",
        "",
        "Te pedimos las resuelvas en un lugar ajeno a ruidos y distracciones, a fin de obtener los "
        "mejores resultados. Una vez iniciado el proceso, no deberás detenerte hasta culminar todas "
        "las pruebas ya que tiene un tiempo determinado de duración.",
        "",
        f"Link de la evaluación: {link}",
        f"Código de acceso: {code}",
        f"Clave de acceso: {key}",
        "",
        "*Por favor abre la prueba en tu navegador Google Chrome.",
        "",
        "¡Éxitos!",
    ])
    h_name, h_title = _esc(str(name)), _esc(str(title))
    h_link, h_code, h_key = _esc(link), _esc(code), _esc(key)
    html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:#2563eb;color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Evaluación psicológica</div>
          <div style="font-size:22px;font-weight:700">{h_title}</div>
        </div>
        <div style="padding:24px;line-height:1.6;color:#333">
          <p style="margin:0 0 12px">Estimad@ <b>{h_name}</b>,</p>
          <p style="margin:0 0 12px">A continuación encontrarás un link con pruebas que deberás resolver para que podamos evaluar tu potencial.</p>
          <p style="margin:0 0 12px">Te pedimos las resuelvas en un lugar ajeno a ruidos y distracciones. Una vez iniciado el proceso, no deberás detenerte hasta culminar todas las pruebas, ya que tienen un tiempo determinado de duración.</p>
          <p style="margin:0 0 6px"><b>Link de la evaluación:</b> <a href="{h_link}" style="color:#2563eb">{h_link}</a></p>
          <p style="margin:0 0 6px"><b>Código de acceso:</b> <span style="color:#d97706;font-weight:700">{h_code}</span></p>
          <p style="margin:0 0 12px"><b>Clave de acceso:</b> <span style="color:#d97706;font-weight:700">{h_key}</span></p>
          <p style="margin:0;color:#555">*Por favor abre la prueba en tu navegador Google Chrome. ¡Éxitos!</p>
        </div>
      </div>
    </body></html>"""
    return ([cand_email], subject, text, html)


def send_psych_exam_email(settings: Settings, vacancy: dict, candidate: dict, exam: dict) -> None:
    """Envía el correo de exámenes psicológicos al candidato. No lanza (envío directo)."""
    built = build_psych_exam_email(settings, vacancy, candidate, exam)
    if built is None:
        logger.info("SMTP/email del candidato sin configurar: se omite el correo de exámenes")
        return
    try:
        send_email(settings, *built)
        logger.info("Correo de exámenes psicológicos enviado a %s", built[0])
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el correo de exámenes psicológicos")


# ── Examen médico pre-contratación (cita en clínica programada por RR.HH.) ─────────

def build_medical_exam_email(
    settings: Settings, vacancy: dict, candidate: dict, exam: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) del correo de la cita del examen médico, o None si no aplica.

    RR.HH. programa fecha + clínica en el dashboard; este correo se lo comunica al candidato.
    El resultado NO viaja por correo (dato sensible de salud, Ley 29733): solo la cita."""
    cand_email = str((candidate.get("cv_profile") or {}).get("email", "")).strip()
    if not (settings.smtp_host and settings.smtp_from and cand_email):
        return None
    clinic = str(exam.get("clinic", "")).strip()
    address = str(exam.get("address", "")).strip()
    scheduled_at = str(exam.get("scheduled_at", "")).strip()
    instructions = str(exam.get("instructions", "")).strip()
    name = candidate.get("name") or "Candidato"
    title = vacancy.get("title", "")
    subject = f"Examen médico ocupacional · {title}".strip(" ·")

    text = "\n".join([
        f"Estimad@ {name},",
        "",
        "Como último paso del proceso de selección, tu examen médico ocupacional quedó programado:",
        "",
        f"Clínica: {clinic}",
        f"Dirección: {address}",
        f"Fecha y hora: {scheduled_at}",
        *([f"Indicaciones: {instructions}"] if instructions else []),
        "",
        "Por favor lleva tu DNI. Con el resultado del examen confirmaremos tu contratación.",
        "",
        "¡Éxitos!",
    ])
    h_name, h_title = _esc(str(name)), _esc(str(title))
    h_clinic, h_address, h_when, h_instr = _esc(clinic), _esc(address), _esc(scheduled_at), _esc(instructions)
    instr_html = f'<p style="margin:0 0 6px"><b>Indicaciones:</b> {h_instr}</p>' if instructions else ""
    html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:#0e9d8e;color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Examen médico ocupacional</div>
          <div style="font-size:22px;font-weight:700">{h_title}</div>
        </div>
        <div style="padding:24px;line-height:1.6;color:#333">
          <p style="margin:0 0 12px">Estimad@ <b>{h_name}</b>,</p>
          <p style="margin:0 0 12px">Como último paso del proceso de selección, tu examen médico ocupacional quedó programado:</p>
          <p style="margin:0 0 6px"><b>Clínica:</b> {h_clinic}</p>
          <p style="margin:0 0 6px"><b>Dirección:</b> {h_address}</p>
          <p style="margin:0 0 6px"><b>Fecha y hora:</b> <span style="color:#0e9d8e;font-weight:700">{h_when}</span></p>
          {instr_html}
          <p style="margin:12px 0 0;color:#555">Por favor lleva tu DNI. Con el resultado del examen confirmaremos tu contratación. ¡Éxitos!</p>
        </div>
      </div>
    </body></html>"""
    return ([cand_email], subject, text, html)


# ── Contratación (auditoría v4, corto plazo #4: hired ganaba solo Telegram) ────────

def build_hired_email(
    settings: Settings, vacancy: dict, candidate: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) del correo de contratación, o None si no aplica."""
    cand_email = str((candidate.get("cv_profile") or {}).get("email", "")).strip()
    if not (settings.smtp_host and settings.smtp_from and cand_email):
        return None
    name = candidate.get("name") or "Candidato"
    title = vacancy.get("title", "")
    subject = f"¡Felicitaciones! Fuiste seleccionad@ · {title}".strip(" ·")

    text = "\n".join([
        f"Estimad@ {name},",
        "",
        f"¡Felicitaciones! Completaste con éxito el proceso de selección para el puesto de {title}.",
        "",
        "El equipo de Recursos Humanos se pondrá en contacto contigo para coordinar los detalles",
        "de tu incorporación (fecha de ingreso, documentación y materiales de bienvenida).",
        "",
        "¡Bienvenid@ al equipo!",
    ])
    h_name, h_title = _esc(str(name)), _esc(str(title))
    html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:#4f46e5;color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Proceso de selección completado</div>
          <div style="font-size:22px;font-weight:700">{h_title}</div>
        </div>
        <div style="padding:24px;line-height:1.6;color:#333">
          <p style="margin:0 0 12px">Estimad@ <b>{h_name}</b>,</p>
          <p style="margin:0 0 12px">¡Felicitaciones! Completaste con éxito el proceso de selección para el puesto de <b>{h_title}</b>.</p>
          <p style="margin:0 0 12px">El equipo de Recursos Humanos se pondrá en contacto contigo para coordinar los detalles de tu incorporación (fecha de ingreso, documentación y materiales de bienvenida).</p>
          <p style="margin:12px 0 0;color:#4f46e5;font-weight:700">¡Bienvenid@ al equipo! 🎉</p>
        </div>
      </div>
    </body></html>"""
    return ([cand_email], subject, text, html)


# ── Kit de onboarding (materiales y guías del primer día de trabajo) ───────────────

def render_kit_materials_text(kit: dict) -> str:
    """Lista de materiales del kit como líneas '• título — url (nota)'. Reusada por Telegram."""
    lines = []
    for m in kit.get("materials") or []:
        parts = [str(m.get("title", "")).strip() or "Material"]
        if str(m.get("url", "")).strip():
            parts.append(str(m["url"]).strip())
        line = "• " + " — ".join(parts)
        if str(m.get("note", "")).strip():
            line += f" ({str(m['note']).strip()})"
        lines.append(line)
    return "\n".join(lines) or "• El equipo de RR.HH. te entregará los materiales en persona."


def build_onboarding_email(
    settings: Settings, vacancy: dict, candidate: dict, kit: dict
) -> tuple[list[str], str, str, str] | None:
    """(recipients, subject, text, html) del correo del kit de onboarding, o None si no aplica."""
    cand_email = str((candidate.get("cv_profile") or {}).get("email", "")).strip()
    if not (settings.smtp_host and settings.smtp_from and cand_email):
        return None
    name = candidate.get("name") or "Candidato"
    title = vacancy.get("title", "")
    welcome = str(kit.get("welcome", "")).strip() or (
        "Hoy comienza tu primer día con nosotros. Preparamos estos materiales para que "
        "conozcas la empresa y tu puesto a detalle."
    )
    subject = f"¡Bienvenid@! Tu kit de onboarding · {title}".strip(" ·")

    text = "\n".join([
        f"¡Bienvenid@ al equipo, {name}!",
        "",
        welcome,
        "",
        "Materiales y guías para tu primer día:",
        render_kit_materials_text(kit),
        "",
        "Cualquier duda, el equipo de RR.HH. está para ayudarte. ¡Mucho éxito!",
    ])
    items_html = ""
    for m in kit.get("materials") or []:
        h_t = _esc(str(m.get("title", "")).strip() or "Material")
        url = str(m.get("url", "")).strip()
        h_note = _esc(str(m.get("note", "")).strip())
        body = f'<a href="{_esc(url)}" style="color:#2563eb">{h_t}</a>' if url else h_t
        note_html = f' — <span style="color:#555">{h_note}</span>' if h_note else ""
        items_html += f'<li style="margin:0 0 8px">{body}{note_html}</li>'
    if not items_html:
        items_html = '<li style="margin:0 0 8px">El equipo de RR.HH. te entregará los materiales en persona.</li>'
    h_name, h_title, h_welcome = _esc(str(name)), _esc(str(title)), _esc(welcome)
    html = f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#111;background:#f6f7f9;padding:24px">
      <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
        <div style="background:#6366f1;color:#fff;padding:20px 24px">
          <div style="font-size:13px;opacity:.9">Kit de onboarding</div>
          <div style="font-size:22px;font-weight:700">¡Bienvenid@, {h_name}!</div>
        </div>
        <div style="padding:24px;line-height:1.6;color:#333">
          <p style="margin:0 0 4px"><b>Puesto:</b> {h_title}</p>
          <p style="margin:12px 0">{h_welcome}</p>
          <h3 style="margin:16px 0 8px">Materiales y guías</h3>
          <ul style="margin:0;padding-left:20px">{items_html}</ul>
          <p style="margin:16px 0 0;color:#555">Cualquier duda, el equipo de RR.HH. está para ayudarte. ¡Mucho éxito!</p>
        </div>
      </div>
    </body></html>"""
    return ([cand_email], subject, text, html)
