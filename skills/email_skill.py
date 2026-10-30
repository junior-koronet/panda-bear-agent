"""
Skills: Email
Read-only access to Junior's Gmail, plus drafting (never sending — that
would need a Gmail send/compose OAuth scope we don't have; Junior copies
the draft himself).
"""

import os
from groq import Groq
from skills.base import Skill

# Groq retired llama-3.3-70b-versatile; the model is configurable (.env GROQ_MODEL).
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None


class ListPendingEmailsSkill(Skill):
    name = "list_pending_emails"
    description = "Lista los correos no leídos en la bandeja de entrada de Junior."
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, max_results: int = 10) -> dict:
        if not self._google or not self._google.gmail:
            return {"success": False, "error": "Gmail no está conectado. Corre setup_google_auth.py"}

        resp = self._google.gmail.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=max_results
        ).execute()

        messages = resp.get("messages", [])
        summary = []
        for m in messages:
            msg = self._google.gmail.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summary.append({
                "from": headers.get("From", "Desconocido"),
                "subject": headers.get("Subject", "(Sin asunto)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })

        return {
            "result": summary,
            "decision": f"Consulté correos pendientes — {len(summary)} sin leer",
            "reasoning": "Junior pidió revisar su correo pendiente.",
        }


class SearchEmailsSkill(Skill):
    name = "search_emails"
    description = (
        "Busca correos en Gmail usando una consulta con operadores de búsqueda de Gmail "
        "(is:unread, is:starred, from:persona@dominio.com, after:YYYY/MM/DD, before:YYYY/MM/DD, "
        "newer_than:7d, -from:me, subject:palabra, etc.)."
    )
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, query: str, max_results: int = 10) -> dict:
        if not self._google or not self._google.gmail:
            return {"success": False, "error": "Gmail no está conectado. Corre setup_google_auth.py"}

        resp = self._google.gmail.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = resp.get("messages", [])
        summary = []
        for m in messages:
            msg = self._google.gmail.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summary.append({
                "from": headers.get("From", "Desconocido"),
                "subject": headers.get("Subject", "(Sin asunto)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "unread": "UNREAD" in msg.get("labelIds", []),
            })

        return {
            "result": summary,
            "decision": f"Busqué correos con la consulta '{query}' — {len(summary)} resultado(s)",
            "reasoning": "Junior pidió buscar correos con un criterio específico.",
        }


class ListEmailsNeedingReplySkill(Skill):
    name = "list_emails_needing_reply"
    description = (
        "Encuentra correos donde el último mensaje del hilo NO lo envió Junior — "
        "es decir, correos que le escribieron y todavía no ha respondido."
    )
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, days_back: int = 14, max_results: int = 15) -> dict:
        if not self._google or not self._google.gmail:
            return {"success": False, "error": "Gmail no está conectado. Corre setup_google_auth.py"}

        profile = self._google.gmail.users().getProfile(userId="me").execute()
        my_email = (profile.get("emailAddress") or "").lower()

        query = f"in:inbox newer_than:{days_back}d -from:me"
        resp = self._google.gmail.users().threads().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        threads = resp.get("threads", [])

        pending = []
        for t in threads:
            thread = self._google.gmail.users().threads().get(
                userId="me", id=t["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            msgs = thread.get("messages", [])
            if not msgs:
                continue
            last = msgs[-1]
            headers = {h["name"]: h["value"] for h in last.get("payload", {}).get("headers", [])}
            last_from = headers.get("From", "").lower()
            if my_email and my_email in last_from:
                continue  # Junior sent the last message — nothing pending on this thread
            pending.append({
                "from": headers.get("From", "Desconocido"),
                "subject": headers.get("Subject", "(Sin asunto)"),
                "date": headers.get("Date", ""),
                "snippet": last.get("snippet", ""),
                "messagesInThread": len(msgs),
            })

        return {
            "result": pending,
            "decision": f"Revisé correos de los últimos {days_back} días — {len(pending)} sin responder",
            "reasoning": "Un correo 'sin responder' es aquel donde el último mensaje del hilo no lo envió Junior.",
        }


class DraftEmailSkill(Skill):
    name = "draft_email"
    description = (
        "Redacta el texto de un correo para que Junior lo copie y lo envíe él mismo — Panda Bear no "
        "tiene permiso para enviar correos, solo para redactarlos."
    )
    category = "personal_assistant"

    def _run(self, purpose: str, recipient_name: str = "", tone: str = "profesional", language: str = "es") -> dict:
        if not groq_client:
            return {"success": False, "error": "Groq no está configurado"}

        prompt = (
            f"Redacta un correo en {'español' if language == 'es' else 'inglés'}, tono {tone}.\n"
            f"Destinatario: {recipient_name or '(sin nombre especificado)'}\n"
            f"Propósito del correo: {purpose}\n\n"
            "Devuelve SOLO el texto del correo (asunto en la primera línea como 'Asunto: ...', "
            "luego el cuerpo), sin explicaciones adicionales."
        )
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=600,
        )
        draft = response.choices[0].message.content

        return {
            "result": draft,
            "decision": f"Redacté un borrador de correo para {recipient_name or 'el destinatario'} sobre: {purpose}",
            "reasoning": "Junior pidió ayuda para redactar un correo. No se envía — solo queda el borrador para que él lo copie.",
        }
