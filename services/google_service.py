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
]

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "google_token.json")


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

    def _on_stop(self) -> None:
        self._creds = None
        self._calendar = None
        self._gmail = None
        self._docs = None
        self._slides = None

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
