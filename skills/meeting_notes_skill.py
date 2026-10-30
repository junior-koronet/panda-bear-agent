"""
Skills: Meeting Notes Output
Turns a meeting's summary + commitments (from the Fathom webhook, or from
the agent's own join-and-record flow) into a Google Doc or a Google Slides
deck, whichever Junior asked for.
"""

import json
import os
from groq import Groq
from skills.base import Skill

# Groq retired llama-3.3-70b-versatile; the model is configurable (.env GROQ_MODEL).
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None


def _format_commitments_text(commitments: list) -> str:
    lines = []
    for c in commitments or []:
        owner = f" — {c.get('owner')}" if c.get("owner") else ""
        due = f" (para {c.get('dueHint')})" if c.get("dueHint") else ""
        lines.append(f"• {c.get('description', '')}{owner}{due}")
    return "\n".join(lines) if lines else "(Sin compromisos detectados)"


class SummarizeTranscriptSkill(Skill):
    name = "summarize_transcript"
    description = (
        "Resume una transcripción de reunión y extrae compromisos — en el mismo formato que antes "
        "entregaba Fathom. Se usa cuando el propio agente grabó la reunión y solo tiene el texto crudo."
    )
    category = "personal_assistant"

    def _run(self, transcript: str, title: str = "") -> dict:
        if not groq_client:
            return {"success": False, "error": "Groq no está configurado"}

        prompt = (
            "Analiza esta transcripción de una reunión. Devuelve SOLO un JSON con esta forma exacta, "
            "sin texto adicional:\n"
            '{"summary": "resumen breve en español", "action_items": '
            '[{"description": "...", "owner": "nombre o null", "due": "fecha/plazo mencionado o null"}]}\n\n'
            f"Título de la reunión: {title}\n\nTranscripción:\n{transcript}"
        )
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "result": {"summary": raw, "action_items": []},
                "reasoning": "El modelo no devolvió JSON válido — se usó el texto crudo como resumen.",
            }

        action_items = parsed.get("action_items", [])
        return {
            "result": {"summary": parsed.get("summary", ""), "action_items": action_items},
            "decision": f"Resumí la transcripción de '{title}' — {len(action_items)} compromiso(s) detectado(s)",
            "reasoning": "Junior le pidió al agente que grabara y tomara nota de esta reunión.",
        }


class CreateSummaryDocSkill(Skill):
    name = "create_summary_doc"
    description = "Crea un Google Doc con el resumen y los compromisos de una reunión."
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, title: str, summary: str = "", commitments: list = None) -> dict:
        if not self._google or not self._google.docs:
            return {"success": False, "error": "Google Docs no está conectado. Corre setup_google_auth.py"}

        doc = self._google.docs.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]

        lines = [title, "", "Resumen", summary or "(Sin resumen disponible)", "", "Compromisos",
                 _format_commitments_text(commitments)]
        body_text = "\n".join(lines)

        self._google.docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": body_text}}]},
        ).execute()

        link = f"https://docs.google.com/document/d/{doc_id}/edit"
        return {
            "result": {"id": doc_id, "link": link},
            "decision": f"Creé el documento '{title}'",
            "reasoning": "Junior eligió documento como formato de las notas de la reunión.",
        }


class CreateSummarySlidesSkill(Skill):
    name = "create_summary_slides"
    description = "Crea una presentación de Google Slides con el resumen y los compromisos de una reunión."
    category = "personal_assistant"

    def __init__(self, memory=None, google_service=None):
        super().__init__(memory)
        self._google = google_service

    def _run(self, title: str, summary: str = "", commitments: list = None) -> dict:
        if not self._google or not self._google.slides:
            return {"success": False, "error": "Google Slides no está conectado. Corre setup_google_auth.py"}

        presentation = self._google.slides.presentations().create(body={"title": title}).execute()
        presentation_id = presentation["presentationId"]

        requests = []

        # Populate the default title slide created automatically
        title_placeholder = None
        subtitle_placeholder = None
        for el in presentation["slides"][0].get("pageElements", []):
            ph = el.get("shape", {}).get("placeholder", {})
            if ph.get("type") in ("CENTERED_TITLE", "TITLE"):
                title_placeholder = el["objectId"]
            elif ph.get("type") == "SUBTITLE":
                subtitle_placeholder = el["objectId"]
        if title_placeholder:
            requests.append({"insertText": {"objectId": title_placeholder, "text": title}})
        if subtitle_placeholder:
            requests.append({"insertText": {"objectId": subtitle_placeholder, "text": "Notas generadas por Panda Bear"}})

        # Summary slide
        requests.append({
            "createSlide": {
                "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                "placeholderIdMappings": [
                    {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "summarySlideTitle"},
                    {"layoutPlaceholder": {"type": "BODY"}, "objectId": "summarySlideBody"},
                ],
            }
        })
        requests.append({"insertText": {"objectId": "summarySlideTitle", "text": "Resumen"}})
        requests.append({"insertText": {"objectId": "summarySlideBody", "text": summary or "(Sin resumen disponible)"}})

        # Commitments slide
        requests.append({
            "createSlide": {
                "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                "placeholderIdMappings": [
                    {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "commitSlideTitle"},
                    {"layoutPlaceholder": {"type": "BODY"}, "objectId": "commitSlideBody"},
                ],
            }
        })
        requests.append({"insertText": {"objectId": "commitSlideTitle", "text": "Compromisos"}})
        requests.append({"insertText": {"objectId": "commitSlideBody", "text": _format_commitments_text(commitments)}})

        self._google.slides.presentations().batchUpdate(
            presentationId=presentation_id, body={"requests": requests}
        ).execute()

        link = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        return {
            "result": {"id": presentation_id, "link": link},
            "decision": f"Creé la presentación '{title}'",
            "reasoning": "Junior eligió presentación como formato de las notas de la reunión.",
        }
