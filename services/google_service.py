"""
Service: Google (Calendar + Gmail)
Provides Junior's personal Google Calendar and Gmail access for the
personal-assistant skills. Calendar writes always go through the
confirmation workflow in core/agent.py before anything that notifies
other people is sent. Gmail: read access plus gmail.compose — compose
only allows creating/sending drafts, never reading or sending arbitrary
mail, and onboarding emails are created as drafts in Junior's own
Gmail account for HIM to send. Panda Bear never sends email itself.
"""

import os
import re
from services.base import ServiceBase

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/presentations",
    # NEW HIRES- KORONET sheet: Jeisson writes each hire's corporate email there.
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

NEW_HIRES_SHEET_ID = os.getenv("NEW_HIRES_SHEET_ID", "1xxtiUZH9rw2_fBmeBzCF0fGbSTQOwmgrkKi-yqMkZ04")

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "google_token.json")


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


class GoogleService(ServiceBase):
    name = "google_service"
    description = "Conexión OAuth a Google Calendar y Gmail para el asistente personal de Junior."

    def __init__(self):
        super().__init__()
        self._creds = None
        self._calendar = None
        self._gmail = None
        self._docs = None
        self._slides = None
        self._sheets = None

    def _on_start(self) -> None:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not os.path.exists(TOKEN_PATH):
            raise RuntimeError(
                f"No hay token de Google en {TOKEN_PATH}. "
                f"Corre 'python setup_google_auth.py' una vez para autorizar el acceso."
            )

        # Load with the scopes the token was actually granted, not SCOPES —
        # requesting a scope the token never got (e.g. gmail.settings.basic
        # added after the last setup_google_auth.py run) makes every refresh
        # fail with invalid_scope, which silently disconnects Gmail.
        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        missing = set(SCOPES) - set(creds.scopes or [])
        if missing:
            print(
                f"[google_service] AVISO: token sin scopes {sorted(missing)} - "
                f"corre 'python setup_google_auth.py' para habilitarlos."
            )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        self._creds = creds
        self._calendar = build("calendar", "v3", credentials=creds)
        self._gmail = build("gmail", "v1", credentials=creds)
        self._docs = build("docs", "v1", credentials=creds)
        self._slides = build("slides", "v1", credentials=creds)
        self._sheets = build("sheets", "v4", credentials=creds)

    def _on_stop(self) -> None:
        self._creds = None
        self._calendar = None
        self._gmail = None
        self._docs = None
        self._slides = None
        self._sheets = None

    def _check_alive(self) -> bool:
        return self._creds is not None and self._calendar is not None

    def _health_details(self) -> dict:
        return {"authenticated": self._creds is not None}

    def find_personal_email(self, full_name: str, exclude_domain: str = "koronet.com") -> str | None:
        """
        BambooHR's workEmail is empty for a candidate/contractor who hasn't
        started yet — they're first reached on their personal address. Finds
        it by searching Junior's own Gmail for a thread mentioning the
        candidate's name and returning the first address involved that
        isn't a @exclude_domain address (i.e. not Junior/a colleague).
        """
        if not self._gmail or not full_name:
            return None
        try:
            resp = self._gmail.users().messages().list(
                userId="me", q=f'"{full_name}"', maxResults=10
            ).execute()
        except Exception:
            return None

        for m in resp.get("messages", []):
            try:
                msg = self._gmail.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "To"],
                ).execute()
            except Exception:
                continue
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            for header_value in (headers.get("From", ""), headers.get("To", "")):
                for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", header_value):
                    if f"@{exclude_domain}" not in address.lower():
                        return address
        return None

    def find_corporate_email(self, full_name: str, hire_date: str = "") -> dict:
        """
        Looks the hire up in the NEW HIRES- KORONET sheet (tab = hire year),
        where Jeisson writes the corporate address, and returns
        {"email", "source"} or {"email": None, "error"}. Matches on the
        accent-insensitive "Name Surname"; with several matches the one whose
        Start Date equals hire_date (YYYY-MM-DD) wins.
        """
        if not self._sheets:
            return {"email": None, "error": "Google Sheets no está conectado"}
        year = (hire_date or "")[:4] or str(__import__("datetime").date.today().year)
        try:
            rows = self._sheets.spreadsheets().values().get(
                spreadsheetId=NEW_HIRES_SHEET_ID, range=f"'{year}'!A1:P300"
            ).execute().get("values", [])
        except Exception as e:
            msg = str(e)
            if "SERVICE_DISABLED" in msg or "has not been used in project" in msg:
                msg = ("La Google Sheets API está desactivada en el proyecto de Google Cloud de "
                       "Panda Bear. Actívala en console.cloud.google.com (APIs y servicios → "
                       "Google Sheets API → Habilitar) y reintenta en unos minutos.")
            elif "insufficient authentication scopes" in msg:
                msg = ("Panda Bear no tiene permiso para leer el sheet NEW HIRES. "
                       "Corre 'python setup_google_auth.py' una vez para autorizarlo.")
            elif "403" in msg or "404" in msg:
                msg = ("Tu cuenta no tiene acceso al sheet NEW HIRES- KORONET "
                       f"({NEW_HIRES_SHEET_ID}). Pide acceso de lectura a quien lo administra.")
            return {"email": None, "error": msg}

        header_idx = next((i for i, r in enumerate(rows)
                           if any("mail koronet" in (c or "").lower() for c in r)), None)
        if header_idx is None:
            return {"email": None, "error": f"No encontré la columna 'Mail Koronet' en la pestaña {year}"}
        header = [(c or "").lower() for c in rows[header_idx]]

        def col(*keys):
            return next((i for i, h in enumerate(header) if any(k in h for k in keys)), None)

        c_name, c_surname = col("name", "nombre"), col("surname", "apellido")
        c_start, c_mail = col("start date", "fecha de ingreso"), col("mail koronet")
        target = _norm(full_name)
        want_date = ""
        if hire_date:
            y, m, d = hire_date.split("-")
            want_date = f"{int(d)}/{int(m)}/{y}"

        matches = []
        for r in rows[header_idx + 1:]:
            cell = lambda i: (r[i] if i is not None and i < len(r) else "") or ""
            name = _norm(f"{cell(c_name)} {cell(c_surname)}")
            if name and name == target and "@" in cell(c_mail):
                parts = cell(c_start).strip().split("/")
                start = "/".join(str(int(p)) if p.isdigit() else p for p in parts)
                matches.append((start, cell(c_mail).strip()))
        if not matches:
            return {"email": None, "error": f"{full_name} no aparece todavía en NEW HIRES ({year}) con Mail Koronet"}
        email = next((m for st, m in matches if want_date and st == want_date), matches[-1][1])
        return {"email": email, "source": f"NEW HIRES- KORONET (pestaña {year})"}

    def create_onboarding_event(self, event_id: str, title: str, start_iso: str, end_iso: str,
                                time_zone: str, attendees: list, description: str = "") -> dict:
        """
        Inserts the onboarding session on Junior's primary calendar with a Meet
        link and invites the attendees. event_id is fixed per meeting, so a
        retry after a timeout finds the event already there instead of creating
        a second one.
        """
        if not self._calendar:
            return {"success": False, "error": "Google Calendar no está conectado"}
        body = {
            "id": event_id,
            "summary": title,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": time_zone},
            "end": {"dateTime": end_iso, "timeZone": time_zone},
            "attendees": [{"email": a} for a in attendees],
            "conferenceData": {"createRequest": {
                "requestId": event_id, "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        }
        try:
            ev = self._calendar.events().insert(
                calendarId="primary", body=body, sendUpdates="all", conferenceDataVersion=1
            ).execute()
        except Exception as e:
            if "409" not in str(e) and "already exists" not in str(e).lower():
                return {"success": False, "error": str(e)}
            ev = self._calendar.events().get(calendarId="primary", eventId=event_id).execute()
            if ev.get("status") == "cancelled":
                return {"success": False, "error": "El evento de este ingreso fue borrado del calendario"}
        return {"success": True, "eventId": ev.get("id"), "link": ev.get("htmlLink"),
                "meetLink": ev.get("hangoutLink")}

    def busy_slots(self, emails: list, time_min: str, time_max: str, time_zone: str) -> dict:
        """Free/busy per email ({email: [(start, end), ...]}), or {email: None} when Google can't tell."""
        if not self._calendar or not emails:
            return {e: None for e in emails}
        try:
            res = self._calendar.freebusy().query(body={
                "timeMin": time_min, "timeMax": time_max, "timeZone": time_zone,
                "items": [{"id": e} for e in emails]}).execute()
        except Exception:
            return {e: None for e in emails}
        out = {}
        for e in emails:
            cal = res.get("calendars", {}).get(e, {})
            out[e] = None if cal.get("errors") else [(b["start"], b["end"]) for b in cal.get("busy", [])]
        return out

    def list_events(self, time_min: str, time_max: str, query: str = None) -> list:
        """Single (expanded) events on Junior's primary calendar in a window."""
        if not self._calendar:
            return []
        kw = {"calendarId": "primary", "timeMin": time_min, "timeMax": time_max, "singleEvents": True,
              "orderBy": "startTime", "maxResults": 250}
        if query:
            kw["q"] = query
        try:
            return self._calendar.events().list(**kw).execute().get("items", [])
        except Exception:
            return []

    def get_event(self, event_id: str) -> dict | None:
        if not self._calendar:
            return None
        try:
            return self._calendar.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception:
            return None

    def set_attendee(self, event_id: str, email: str, present: bool) -> dict:
        """Adds/removes one guest on one event (or one occurrence of a series) and notifies the guests."""
        ev = self.get_event(event_id)
        if not ev:
            return {"success": False, "error": "No encontré ese evento en tu calendario"}
        attendees = [a for a in ev.get("attendees", []) if a.get("email", "").lower() != email.lower()]
        if present:
            attendees.append({"email": email})
        try:
            ev = self._calendar.events().patch(
                calendarId="primary", eventId=event_id, body={"attendees": attendees}, sendUpdates="all"
            ).execute()
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "eventId": ev.get("id"), "link": ev.get("htmlLink"), "meetLink": ev.get("hangoutLink")}

    def move_event(self, event_id: str, start_iso: str, end_iso: str, time_zone: str) -> dict:
        """Reschedules an event Junior organizes; guests get the update."""
        try:
            ev = self._calendar.events().patch(
                calendarId="primary", eventId=event_id, sendUpdates="all",
                body={"start": {"dateTime": start_iso, "timeZone": time_zone},
                      "end": {"dateTime": end_iso, "timeZone": time_zone}}).execute()
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "eventId": ev.get("id"), "link": ev.get("htmlLink"), "meetLink": ev.get("hangoutLink")}

    def get_signature(self) -> str:
        """
        Fetches Junior's default Gmail signature (HTML) via the Gmail
        settings API. Needs the gmail.settings.basic scope — returns "" if
        the token predates it (or any other lookup failure) rather than
        raising, so a signature miss never blocks draft creation.
        """
        if not self._gmail:
            return ""
        try:
            resp = self._gmail.users().settings().sendAs().list(userId="me").execute()
        except Exception:
            return ""
        send_as = resp.get("sendAs", [])
        default = next((s for s in send_as if s.get("isDefault")), send_as[0] if send_as else None)
        return (default or {}).get("signature") or ""

    def create_draft(self, to: str, subject: str, body_text: str, image_path: str = None,
                     cc: str = None) -> dict:
        """
        Creates a real Gmail draft in Junior's own Drafts folder — never sends.
        Junior reviews it in Gmail and clicks Send himself. Includes his
        default Gmail signature (fetched fresh each time, in case he changes
        it) as an HTML alternative part — a drafts.create call is raw MIME,
        so Gmail's client-side auto-signature never applies to it otherwise.
        """
        import base64
        import mimetypes
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.image import MIMEImage

        if not self._gmail:
            return {"success": False, "error": "Gmail no está conectado"}
        if not to:
            return {"success": False, "error": "El empleado no tiene email registrado en BambooHR"}

        try:
            signature_html = self.get_signature()

            message = MIMEMultipart("mixed")
            message["to"] = to
            if cc:
                message["cc"] = cc
            message["subject"] = subject

            body_alt = MIMEMultipart("alternative")
            body_alt.attach(MIMEText(body_text, "plain"))
            if signature_html:
                html_body = body_text.replace("\n", "<br>") + "<br><br>" + signature_html
                body_alt.attach(MIMEText(html_body, "html"))
            message.attach(body_alt)

            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as f:
                    mime_type, _ = mimetypes.guess_type(image_path)
                    img = MIMEImage(f.read(), _subtype=(mime_type or "png").split("/")[-1])
                    img.add_header("Content-Disposition", "attachment", filename=os.path.basename(image_path))
                    message.attach(img)

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            draft = self._gmail.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}
            ).execute()
            draft_id = draft.get("id")
            if not draft_id:
                return {"success": False, "error": "Gmail no devolvió un id de borrador"}

            # Read it back: a draft only counts as created once Gmail can
            # actually return it — never trust the create response alone.
            self._gmail.users().drafts().get(userId="me", id=draft_id, format="minimal").execute()

            return {
                "success": True,
                "draftId": draft_id,
                "cc": cc,
                "note": "Borrador creado en Gmail — revísalo y dale Enviar.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @property
    def calendar(self):
        return self._calendar

    @property
    def gmail(self):
        return self._gmail

    @property
    def docs(self):
        return self._docs

    @property
    def slides(self):
        return self._slides

    @property
    def sheets(self):
        return self._sheets
