"""
Skills: Calendar
Read and create events on Junior's Google Calendar.
Creating an event WITH attendees always returns a draft — core/agent.py
requires Junior's explicit confirmation before it is actually inserted
and invitations are sent. Without attendees (a purely personal event),
it is inserted directly since nobody else is notified.
"""

from datetime import datetime, timedelta
from skills.base import Skill


class ListCalendarEventsSkill(Skill):
    name = "list_calendar_events"
    description = (
        "Lista o busca eventos del calendario de Junior en un rango de fechas (por defecto, hoy). "
        "Acepta un filtro de texto en el título y/o un filtro por el estado de respuesta de los "
        "invitados (accepted/declined/needsAction/tentative) — úsalos cuando haya muchos eventos "
        "similares (ej. varias citas del mismo tipo en un día) en vez de traerlos todos sin filtrar."
    )
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, start_date: str = None, end_date: str = None, query: str = None, attendee_status: str = None) -> dict:
        if not self._google or not self._google.calendar:
            return {"success": False, "error": "Google Calendar no está conectado. Corre setup_google_auth.py"}

        tz = datetime.now().astimezone().tzinfo
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)

        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz)
        else:
            start = today

        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=1)
        else:
            end = start + timedelta(days=1)

        events_result = self._google.calendar.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        total_in_range = len(events)
        summary = []
        for e in events:
            title = e.get("summary", "(Sin título)")
            if query and query.lower() not in title.lower():
                continue

            attendees = [
                {"email": a.get("email"), "responseStatus": a.get("responseStatus")}
                for a in e.get("attendees", [])
            ]
            if attendee_status and not any(a["responseStatus"] == attendee_status for a in attendees):
                continue

            start_time = e.get("start", {}).get("dateTime", e.get("start", {}).get("date"))
            summary.append({
                "title": title,
                "start": start_time,
                "attendees": attendees,
                "link": e.get("htmlLink"),
                "eventId": e.get("id"),
                "meetLink": e.get("hangoutLink"),
            })

        filters_note = ""
        if query:
            filters_note += f" con título que contiene '{query}'"
        if attendee_status:
            filters_note += f" con al menos un invitado en estado '{attendee_status}'"

        return {
            "result": summary,
            "totalInRange": total_in_range,
            "decision": (
                f"Consulté el calendario del {start.strftime('%Y-%m-%d')} al "
                f"{(end - timedelta(days=1)).strftime('%Y-%m-%d')}{filters_note} — "
                f"{len(summary)} de {total_in_range} evento(s) coinciden"
            ),
            "reasoning": "Junior pidió revisar o filtrar su agenda en ese rango de fechas.",
        }


class CreateCalendarEventSkill(Skill):
    name = "create_calendar_event"
    description = "Crea un evento en el calendario de Junior."
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, summary: str, start: str, end: str, attendees: list = None, description: str = "") -> dict:
        if not self._google or not self._google.calendar:
            return {"success": False, "error": "Google Calendar no está conectado. Corre setup_google_auth.py"}

        event_body = {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if attendees:
            event_body["attendees"] = [{"email": a} for a in attendees]

        created = self._google.calendar.events().insert(
            calendarId="primary",
            body=event_body,
            sendUpdates="all" if attendees else "none",
        ).execute()

        return {
            "result": {
                "id": created.get("id"),
                "link": created.get("htmlLink"),
                "summary": summary,
                "start": start,
                "end": end,
                "attendees": attendees or [],
            },
            "decision": f"Creé el evento '{summary}' el {start}",
            "reasoning": "Junior solicitó agendar este evento.",
        }
