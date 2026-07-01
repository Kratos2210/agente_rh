"""Backend de agendamiento de entrevista: disponibilidad del reclutador + reunión.

Mismo patrón que `integrations/sourcing.py` (Protocol + factory + Simulated*):
`SimulatedScheduler` (default) hace correr y testear todo el flujo sin credenciales;
`GoogleScheduler` consulta el Google Calendar del reclutador (free/busy), crea el
evento con enlace Meet e inserta una fila en Google Sheets. El cómputo de los huecos
libres (`compute_free_slots`) es puro y testeable (sin I/O).
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

# Nombres en español para formatear los horarios sin depender del locale del sistema.
_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _tz(name: str):
    """Zona horaria por nombre; cae a UTC-5 (Lima) si no hay tzdata."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        from datetime import timezone

        return timezone(timedelta(hours=-5))


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = str(value).split(":")
        return int(h), int(m)
    except Exception:  # noqa: BLE001
        return default


def human_slot(dt: datetime) -> str:
    """Representación legible en español: 'lunes 23/06 a las 09:00'."""
    return f"{_WEEKDAYS[dt.weekday()]} {dt.day:02d}/{dt.month:02d} a las {dt:%H:%M}"


def human_slot_long(dt: datetime) -> str:
    """Versión larga para correos: 'lunes 23 de junio, 09:00'."""
    return f"{_WEEKDAYS[dt.weekday()]} {dt.day} de {_MONTHS[dt.month - 1]}, {dt:%H:%M}"


@dataclass
class MeetingResult:
    """Resultado de crear la reunión (evento + enlace)."""

    start: datetime
    end: datetime
    meet_link: str = ""
    event_id: str = ""
    sheet_row: str = ""


def _overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    for bs, be in busy:
        if bs < end and start < be:
            return True
    return False


def compute_free_slots(
    busy: list[tuple[datetime, datetime]],
    cfg: dict[str, Any],
    *,
    now: datetime | None = None,
    count: int | None = None,
) -> list[datetime]:
    """Próximos N huecos libres del reclutador (puro, sin I/O).

    Respeta días hábiles (ISO 1=lunes..7=domingo), horario de oficina, zona horaria,
    duración del slot y horizonte de búsqueda; descarta los `busy` (ocupados)."""
    tz = _tz(cfg.get("timezone", "America/Lima"))
    dur = int(cfg.get("slot_minutes", 45) or 45)
    work_days = set(cfg.get("work_days") or [1, 2, 3, 4, 5])
    ws_h, ws_m = _parse_hhmm(cfg.get("work_start", "09:00"), (9, 0))
    we_h, we_m = _parse_hhmm(cfg.get("work_end", "18:00"), (18, 0))
    horizon = int(cfg.get("horizon_days", 7) or 7)
    count = int(count if count is not None else cfg.get("options", 3) or 3)

    now = (now or datetime.now(tz)).astimezone(tz)
    busy = [(bs.astimezone(tz), be.astimezone(tz)) for bs, be in busy]

    slots: list[datetime] = []
    for d in range(horizon + 1):
        day = (now + timedelta(days=d)).date()
        if day.isoweekday() not in work_days:
            continue
        s = datetime(day.year, day.month, day.day, ws_h, ws_m, tzinfo=tz)
        window_end = datetime(day.year, day.month, day.day, we_h, we_m, tzinfo=tz)
        while s + timedelta(minutes=dur) <= window_end:
            e = s + timedelta(minutes=dur)
            if s > now + timedelta(minutes=1) and not _overlaps(s, e, busy):
                slots.append(s)
                if len(slots) >= count:
                    return slots
            s = e
    return slots


# ── Backend (interfaz) ───────────────────────────────────────────────────────────

class SchedulingBackend(Protocol):
    name: str

    def busy_intervals(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]: ...

    def create_meeting(
        self,
        *,
        calendar_id: str,
        summary: str,
        start: datetime,
        end: datetime,
        attendees: list[str],
        description: str = "",
    ) -> MeetingResult: ...

    def append_sheet_row(self, sheet_id: str, tab: str, row: list[str]) -> str: ...


# ── Implementación simulada (default, sin credenciales) ───────────────────────────

class SimulatedScheduler:
    """Backend de demo: reclutador siempre libre, enlace Meet falso, "Sheet" local.

    Permite correr y testear el flujo completo (propuesta → elección → reunión) sin
    credenciales de Google. Las filas se anexan a `uploads/meetings.csv`.
    """

    name = "simulated"

    def __init__(self, sheet_path: Path | None = None) -> None:
        self._sheet_path = sheet_path or (Path("uploads") / "meetings.csv")

    def busy_intervals(self, calendar_id, start, end):  # noqa: ANN001
        return []

    def create_meeting(self, *, calendar_id, summary, start, end, attendees, description=""):  # noqa: ANN001
        token = uuid.uuid4().hex[:11]
        link = f"https://meet.google.com/sim-{token[:4]}-{token[4:7]}-{token[7:11]}"
        return MeetingResult(
            start=start, end=end, meet_link=link, event_id=f"sim-{token}"
        )

    def append_sheet_row(self, sheet_id, tab, row):  # noqa: ANN001
        try:
            self._sheet_path.parent.mkdir(parents=True, exist_ok=True)
            with self._sheet_path.open("a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(row)
            return f"local:{self._sheet_path}"
        except Exception:  # noqa: BLE001 — el registro no debe tumbar el agendamiento
            return ""


# ── Implementación Google (Calendar free/busy + evento Meet + Sheets) ─────────────

class GoogleScheduler:
    """Backend real con cuenta de servicio de Google (import perezoso).

    Requiere compartir el calendario del reclutador y el Google Sheet con el email de
    la cuenta de servicio. Lee la disponibilidad (freebusy), crea el evento con un
    enlace Meet (conferenceData) e inserta una fila en la hoja indicada.
    """

    name = "google"
    _SCOPES = [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/spreadsheets",
    ]

    def __init__(self, credentials_path: str = "", *, oauth_token_path: str = "") -> None:
        from googleapiclient.discovery import build  # type: ignore

        creds = (
            self._oauth_credentials(oauth_token_path)
            if oauth_token_path
            else self._service_account_credentials(credentials_path)
        )
        self._calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self._sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _service_account_credentials(self, credentials_path: str):
        """Cuenta de servicio (Workspace + Domain-Wide Delegation)."""
        from google.oauth2 import service_account  # type: ignore

        return service_account.Credentials.from_service_account_file(
            credentials_path, scopes=self._SCOPES
        )

    def _oauth_credentials(self, token_path: str):
        """Credenciales OAuth de usuario (Gmail personal). Refresca y persiste si caducaron.

        El token lo genera `scripts/google_oauth.py` (autorización one-time en el navegador).
        Como el usuario es el organizador real, el evento puede crear un enlace Meet de verdad
        y enviar invitaciones por correo (lo que una cuenta de servicio no puede en Gmail personal)."""
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore

        creds = Credentials.from_authorized_user_file(token_path, self._SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                Path(token_path).write_text(creds.to_json(), encoding="utf-8")
            else:
                raise RuntimeError(
                    f"Token OAuth inválido o sin refresh_token en {token_path}. "
                    "Vuelve a correr `uv run python scripts/google_oauth.py`."
                )
        return creds

    def busy_intervals(self, calendar_id, start, end):  # noqa: ANN001
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": calendar_id}],
        }
        resp = self._calendar.freebusy().query(body=body).execute()
        cal = (resp.get("calendars") or {}).get(calendar_id, {})
        out: list[tuple[datetime, datetime]] = []
        for b in cal.get("busy", []) or []:
            out.append((datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])))
        return out

    def create_meeting(self, *, calendar_id, summary, start, end, attendees, description=""):  # noqa: ANN001
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "attendees": [{"email": a} for a in attendees if a],
            "conferenceData": {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        created = (
            self._calendar.events()
            .insert(
                calendarId=calendar_id,
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",
            )
            .execute()
        )
        return MeetingResult(
            start=start,
            end=end,
            meet_link=created.get("hangoutLink", ""),
            event_id=created.get("id", ""),
        )

    def append_sheet_row(self, sheet_id, tab, row):  # noqa: ANN001
        resp = (
            self._sheets.spreadsheets()
            .values()
            .append(
                spreadsheetId=sheet_id,
                range=f"{tab}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )
        return (resp.get("updates") or {}).get("updatedRange", "")


def get_scheduler(settings: Any) -> SchedulingBackend:
    """Factory del backend según la config (default: simulado, sin credenciales).

    Con provider="google" prioriza OAuth de usuario (Gmail personal: Meet real + invitaciones);
    si solo hay cuenta de servicio (Workspace), la usa. Sin credenciales cae a simulado."""
    provider = getattr(settings, "scheduling_provider", "simulated")
    if provider == "google":
        token = getattr(settings, "google_oauth_token_path", "")
        if token:
            return GoogleScheduler(oauth_token_path=token)
        sa = getattr(settings, "google_credentials_path", "")
        if sa:
            return GoogleScheduler(sa)
    return SimulatedScheduler()
