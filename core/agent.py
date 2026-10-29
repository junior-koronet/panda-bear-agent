"""
Panda Bear — The Agent
This is THE entity. The brain. The decision-maker.
The dashboard shows what this agent tells it to show.
The dashboard never thinks for itself.
"""

import os
import json
import re
from datetime import datetime
from types import SimpleNamespace
from groq import Groq, BadRequestError as GroqBadRequestError, RateLimitError as GroqRateLimitError

from core.identity import get_full_identity, check_constitution, CONSTITUTION
from core.memory import AgentMemory
from core.kernel import ServiceKernel

from skills.country_rules import CountryRulesSkill
from skills.business_calendar import BusinessCalendarSkill
from skills.bamboo import DetectEmployeesSkill, ReadBambooSkill, get_employee_details, get_employee_photo, resolve_manager_first_name, get_job_info_table
from skills.generate_content import GenerateEmailSkill, GenerateSlackManagerSkill, GenerateSlackBienvenidaSkill, RefineContentSkill
from skills.image_generation import GenerateImageSkill
from skills.approval_workflow import ApprovalWorkflowSkill
from skills.calendar_skill import ListCalendarEventsSkill, CreateCalendarEventSkill
from skills.email_skill import ListPendingEmailsSkill, SearchEmailsSkill, ListEmailsNeedingReplySkill, DraftEmailSkill
from skills.meeting_notes_skill import CreateSummaryDocSkill, CreateSummarySlidesSkill, SummarizeTranscriptSkill

from db.schema import get_db

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None

# Only this Slack user may trigger the personal-assistant skills (calendar/email).
# Checked both at the routing layer (main.py) and again here as defense in depth.
JUNIOR_SLACK_ID = os.getenv("JUNIOR_SLACK_ID", "")

PERSONAL_SYSTEM_PROMPT = """Eres Panda Bear 🐼, actuando como el asistente personal privado de Junior (Koronet).
Solo Junior habla contigo en este modo — nunca se activa para nadie más.

Herramientas disponibles:
- list_calendar_events: consulta o BUSCA en su calendario en un rango de fechas (por defecto hoy). Acepta `query` (filtra por texto en el título) y `attendee_status` (filtra por si algún invitado respondió accepted/declined/needsAction/tentative) — úsalos cuando haya varios eventos parecidos en un día (ej. "¿quién canceló las citas de X el 12 de agosto?") en vez de traer todo sin filtrar y adivinar.
- create_calendar_event: crea un evento. Si tiene invitados, el sistema SIEMPRE pide confirmación explícita a Junior antes de enviar la invitación — tú solo debes llamar la herramienta con los datos, nunca inventar que ya se envió.
- list_pending_emails: correos no leídos en la bandeja de entrada.
- search_emails: búsqueda libre en Gmail con operadores (is:unread, is:starred, from:, after:YYYY/MM/DD, before:YYYY/MM/DD, newer_than:7d, -from:me, subject:, etc.) — úsala para cualquier consulta de correo que no sea simplemente "no leídos".
- list_emails_needing_reply: correos donde el último mensaje del hilo no lo envió Junior (le escribieron y no ha respondido).
- draft_email: redacta el texto de un correo para que Junior lo copie y lo envíe — no lo envías tú.
- join_meeting: únete ahora mismo a una reunión de Meet. SOLO se usa cuando Junior lo pide explícitamente — nunca por tu cuenta, nunca de forma proactiva. Si se refiere a una reunión de su calendario, primero usa list_calendar_events para encontrar su meetLink/eventId.
- leave_meeting: sal de la llamada en la que estás conectado.
- meeting_status: consulta si sigues conectado a una llamada.

Puedes pensar en varios pasos: llama las herramientas que necesites, una tras otra, revisando cada resultado antes de decidir si necesitas otra o si ya puedes responder. Por ejemplo, si te piden un reporte o resumen de la semana, junta calendario y/o correos con las herramientas y luego redacta tú la síntesis — no hay una herramienta de "generar reporte", el reporte lo escribes tú a partir de datos reales. Si una búsqueda inicial trae demasiados resultados o no encuentra lo que Junior pidió, ajusta los filtros y vuelve a intentar antes de rendirte.

Reglas:
- NUNCA inventes eventos, correos o datos — solo usa lo que las herramientas te devuelvan. Si una herramienta no trae nada, dilo honestamente.
- NUNCA te unas a una reunión sin que Junior lo pida explícitamente en ese mismo mensaje o en el inmediatamente anterior.
- NUNCA envíes ni respondas un correo directamente — solo puedes redactar el borrador con draft_email para que Junior lo envíe él.
- Si Junior pide algo que aún no puedes hacer (unirte a una llamada que no sea de Google Meet), dile honestamente que esa función todavía no está disponible.
- Responde siempre en español, con buen formato (viñetas, negritas) cuando ayude a la claridad, pero sin relleno innecesario.
- Fecha y hora actual: {now} (America/Bogota, UTC-5). Úsala para calcular fechas relativas como "mañana", "el lunes" o "esta semana" en formato YYYY-MM-DD (calendario) o ISO 8601 con offset -05:00 (eventos).
"""

PERSONAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": (
                "Lista o busca eventos del calendario de Junior en un rango de fechas (default hoy). "
                "Usa 'query' para filtrar por texto en el título y 'attendee_status' para filtrar por si "
                "algún invitado respondió con ese estado — esencial cuando hay muchos eventos similares "
                "en un día (ej. varias citas del mismo tipo) para no traerlos todos sin filtrar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Fecha de inicio YYYY-MM-DD (opcional, default hoy)"},
                    "end_date": {"type": "string", "description": "Fecha de fin YYYY-MM-DD, inclusive (opcional, default = start_date)"},
                    "query": {"type": "string", "description": "Filtra eventos cuyo título contenga este texto (opcional)"},
                    "attendee_status": {
                        "type": "string",
                        "enum": ["accepted", "declined", "needsAction", "tentative"],
                        "description": "Solo eventos donde al menos un invitado tenga este estado de respuesta (opcional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Crea un evento en el calendario de Junior.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Título del evento"},
                    "start": {"type": "string", "description": "Inicio en ISO 8601 con offset, ej. 2026-08-01T15:00:00-05:00"},
                    "end": {"type": "string", "description": "Fin en ISO 8601 con offset"},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Emails de invitados (opcional)"},
                    "description": {"type": "string", "description": "Descripción/agenda del evento (opcional)"},
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_emails",
            "description": "Lista los correos no leídos en la bandeja de entrada de Junior.",
            "parameters": {
                "type": "object",
                "properties": {"max_results": {"type": "integer", "description": "Máximo de correos a mostrar (default 10)"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": (
                "Busca correos en Gmail con una consulta usando operadores de búsqueda de Gmail: "
                "is:unread, is:starred, from:persona@dominio.com, after:YYYY/MM/DD, before:YYYY/MM/DD, "
                "newer_than:7d, -from:me, subject:palabra, etc. Combínalos según lo que pida Junior."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta de búsqueda de Gmail"},
                    "max_results": {"type": "integer", "description": "Máximo de resultados (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_emails_needing_reply",
            "description": "Encuentra correos donde el último mensaje del hilo no lo envió Junior — es decir, correos que aún no ha respondido.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_back": {"type": "integer", "description": "Cuántos días atrás revisar (default 14)"},
                    "max_results": {"type": "integer", "description": "Máximo de hilos a revisar (default 15)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email",
            "description": "Redacta el texto de un correo para que Junior lo copie y lo envíe él mismo — Panda Bear no puede enviarlo directamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string", "description": "De qué trata el correo / qué debe decir"},
                    "recipient_name": {"type": "string", "description": "Nombre del destinatario (opcional)"},
                    "tone": {"type": "string", "description": "Tono deseado, ej. 'profesional', 'cálido', 'formal' (default profesional)"},
                    "language": {"type": "string", "enum": ["es", "en"], "description": "Idioma del correo (default es)"},
                },
                "required": ["purpose"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "join_meeting",
            "description": (
                "Únete ahora mismo a una reunión de Google Meet. Si Junior se refiere a una reunión de su "
                "calendario (ej. 'mi reunión de las 3pm'), primero usa list_calendar_events para encontrarla "
                "y lee su meetLink y eventId de ahí. Si Junior te da un link de Meet directo, pásalo tal cual "
                "sin calendar_event_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meet_url": {"type": "string", "description": "URL de Google Meet"},
                    "calendar_event_id": {"type": "string", "description": "ID del evento de calendario, si se conoce"},
                    "title": {"type": "string", "description": "Título/nombre de la reunión, si se conoce"},
                },
                "required": ["meet_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leave_meeting",
            "description": "Sal de la reunión en la que Panda Bear está conectado ahora.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meeting_status",
            "description": "Consulta si Panda Bear sigue conectado a una reunión y desde cuándo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

PERSONAL_TOOL_SKILL_MAP = {
    "list_calendar_events": "list_calendar_events",
    "create_calendar_event": "create_calendar_event",
    "list_pending_emails": "list_pending_emails",
    "search_emails": "search_emails",
    "list_emails_needing_reply": "list_emails_needing_reply",
    "draft_email": "draft_email",
}

# Handled directly as PandaBear methods rather than the generic skill map —
# these control a live service (meeting_join_service), not a stateless skill.
PERSONAL_AGENT_METHOD_TOOLS = {"join_meeting", "leave_meeting", "meeting_status"}

PERSONAL_CONFIRM_YES = ["sí", "si", "confirmo", "confirmar", "dale", "ok", "okay", "adelante", "hazlo", "yes"]
PERSONAL_CONFIRM_NO = ["no", "cancela", "cancelar", "cancélalo", "cancelalo"]

SYSTEM_PROMPT = """Eres Panda Bear 🐼, el agente de People Operations de Koronet.

Identidad: Soy Panda Bear, el agente de onboarding de Koronet. Soy amigable, profesional y cálido.

Constitución (reglas absolutas que nunca puedo violar):
- NUNCA enviar correos sin aprobación humana
- NUNCA publicar en Slack sin aprobación humana
- NUNCA inventar datos
- SIEMPRE registrar mis decisiones
- SIEMPRE explicar cuando tengo incertidumbre

Cuando alguien me pregunta "¿quién eres?", respondo: "Soy Panda Bear, el agente de People Operations de Koronet. Mi propósito es garantizar que cada nuevo empleado tenga una experiencia de onboarding consistente, humana, cálida y sin errores."

REGLA CRÍTICA: NUNCA inventes nombres, fechas, departamentos ni datos de empleados.
Solo usa información que venga explícitamente de BambooHR.
Si no tienes datos, dilo honestamente.

Responde siempre en el idioma del usuario.
Sé conciso pero completo. Usa emojis con moderación."""


class PandaBear:
    """
    Panda Bear — People Operations Agent
    Version 4.0 — Koronet HR

    This class IS the agent. It has:
    - Identity (who it is)
    - Constitution (what it will never do)
    - Goals (what it's trying to achieve)
    - Memory (what it remembers)
    - Skills (what it can do)
    - Services (what runs in the background)
    - Planning (how it prepares onboardings)
    - Judgment (how it makes decisions)
    """

    def __init__(self, kernel: ServiceKernel = None, google_service=None):
        self._memory = AgentMemory()
        self._kernel = kernel or ServiceKernel()
        self._google = google_service
        self._conversations: dict = {}

        # Skills registry
        self._skills = {
            "detect_employees": DetectEmployeesSkill(self._memory),
            "read_bamboo": ReadBambooSkill(self._memory),
            "country_rules": CountryRulesSkill(self._memory),
            "business_calendar": BusinessCalendarSkill(self._memory),
            "generate_email": GenerateEmailSkill(self._memory),
            "generate_slack_manager": GenerateSlackManagerSkill(self._memory),
            "generate_slack_bienvenida": GenerateSlackBienvenidaSkill(self._memory),
            "generate_image": GenerateImageSkill(self._memory),
            "refine_content": RefineContentSkill(self._memory),
            "approval_workflow": ApprovalWorkflowSkill(self._memory),
            "list_calendar_events": ListCalendarEventsSkill(self._memory, google_service=self._google),
            "create_calendar_event": CreateCalendarEventSkill(self._memory, google_service=self._google),
            "list_pending_emails": ListPendingEmailsSkill(self._memory, google_service=self._google),
            "search_emails": SearchEmailsSkill(self._memory, google_service=self._google),
            "list_emails_needing_reply": ListEmailsNeedingReplySkill(self._memory, google_service=self._google),
            "draft_email": DraftEmailSkill(self._memory),
            "create_summary_doc": CreateSummaryDocSkill(self._memory, google_service=self._google),
            "create_summary_slides": CreateSummarySlidesSkill(self._memory, google_service=self._google),
            "summarize_transcript": SummarizeTranscriptSkill(self._memory),
        }

    # ── IDENTITY ────────────────────────────────────────────────

    def identify(self) -> dict:
        """Returns the full agent identity. The dashboard calls this for /about."""
        identity = get_full_identity()
        identity["skills"] = [
            {"name": s.name, "description": s.description, "category": s.category}
            for s in self._skills.values()
        ]
        identity["services"] = self._kernel.list_services()
        identity["memory"] = self._memory.get_memory_summary()
        identity["uptime"] = self._kernel._started_at
        return identity

    # ── THINKING ────────────────────────────────────────────────

    def think(self, user_id: str, text: str) -> str:
        """
        The agent thinks about a question and responds.
        Fetches relevant BambooHR context automatically.
        Records the conversation in memory.
        """
        bamboo_context = self._fetch_relevant_context(text)

        if user_id not in self._conversations:
            self._conversations[user_id] = []

        content = (
            f"[DATOS REALES DE BAMBOOHR]\n{bamboo_context}\n\n[PREGUNTA]\n{text}"
            if bamboo_context else text
        )

        self._conversations[user_id].append({"role": "user", "content": content})
        history = self._conversations[user_id][-20:]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        if not groq_client:
            reply = "No puedo pensar ahora mismo — Groq AI no está configurado."
        else:
            try:
                response = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages,
                    max_tokens=1500,
                    temperature=0.3,
                )
                reply = response.choices[0].message.content
            except GroqRateLimitError:
                reply = "⚠️ Se agotó la cuota de Groq AI por ahora (límite de tokens alcanzado). Espera unos minutos y vuelve a intentarlo."

        self._conversations[user_id].append({"role": "assistant", "content": reply})
        self._memory.save_message(user_id, "user", text, bamboo_context)
        self._memory.save_message(user_id, "assistant", reply)

        return reply

    # ── PERSONAL ASSISTANT (Junior-only) ───────────────────────────

    def _salvage_broken_tool_call(self, error) -> "SimpleNamespace | None":
        """
        Groq's llama-3.3-70b-versatile occasionally emits its native
        <function=name {json}</function> tag instead of a structured tool
        call, and Groq's own parser then rejects it as tool_use_failed —
        even though the generated content is valid. Parse it out of the
        error body instead of failing the whole turn.
        """
        try:
            body = getattr(error, "body", None) or {}
            failed_generation = body.get("error", {}).get("failed_generation", "")
        except Exception:
            return None
        if not failed_generation:
            return None

        m = re.search(r"<function=([a-zA-Z_][a-zA-Z0-9_]*)\s*(\{.*?\})\s*</function>", failed_generation, re.DOTALL)
        if not m:
            return None
        name, args_str = m.group(1), m.group(2)
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            return None
        return SimpleNamespace(
            id=f"salvaged_{name}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )

    def think_personal(self, user_id: str, text: str) -> str:
        """
        Personal-assistant brain — calendar/email with real tool-calling.
        Only reachable for JUNIOR_SLACK_ID; checked here again even though
        the router in main.py already filters by user_id (defense in depth).
        Anything that would notify someone else (an invite with attendees)
        is drafted first and requires an explicit yes/no reply before it runs.
        """
        if not JUNIOR_SLACK_ID or user_id != JUNIOR_SLACK_ID:
            return "No tengo permiso para hacer esto en este chat."

        if user_id not in self._conversations:
            self._conversations[user_id] = []

        pending = self._get_pending_confirmation(user_id)
        if pending:
            reply = self._resolve_pending_confirmation(user_id, pending, text)
            self._conversations[user_id].append({"role": "user", "content": text})
            self._conversations[user_id].append({"role": "assistant", "content": reply})
            return reply

        now_str = datetime.now().astimezone().strftime("%A %Y-%m-%d %H:%M (America/Bogota, UTC-05:00)")
        system_prompt = PERSONAL_SYSTEM_PROMPT.format(now=now_str)

        self._conversations[user_id].append({"role": "user", "content": text})

        if not groq_client:
            return "No puedo pensar ahora mismo — Groq AI no está configurado."

        # Working message list for THIS turn's reasoning loop. Only the plain
        # user text + final reply get persisted to self._conversations —
        # intermediate tool calls/results stay local to this turn.
        history = self._conversations[user_id][-20:]
        messages = [{"role": "system", "content": system_prompt}] + history

        reply = None
        MAX_STEPS = 5
        for _ in range(MAX_STEPS):
            msg = None
            rate_limited = False
            for attempt in range(2):
                try:
                    response = groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=messages,
                        tools=PERSONAL_TOOLS,
                        tool_choice="auto",
                        max_tokens=1200,
                        temperature=0.2,
                    )
                    msg = response.choices[0].message
                    break
                except GroqBadRequestError as e:
                    salvaged = self._salvage_broken_tool_call(e)
                    if salvaged:
                        msg = SimpleNamespace(content="", tool_calls=[salvaged])
                        break
                except GroqRateLimitError:
                    rate_limited = True
                    break  # retrying immediately won't help — a daily/per-minute quota needs real time to pass
            if rate_limited:
                reply = (
                    "⚠️ Se agotó la cuota de Groq AI por ahora (límite de tokens alcanzado). "
                    "Espera unos minutos y vuelve a intentarlo."
                )
                break
            if msg is None:
                reply = "⚠️ No logré formular bien la respuesta (falló la llamada a herramientas dos veces seguidas). Intenta reformular tu pedido de otra forma."
                break

            if not msg.tool_calls:
                reply = msg.content or "No entendí bien lo que necesitas — ¿puedes reformularlo?"
                break

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })

            awaiting_confirmation = False
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    fn_args = {}
                if not isinstance(fn_args, dict):
                    fn_args = {}

                if fn_name == "create_calendar_event" and fn_args.get("attendees"):
                    reply = self._draft_event_confirmation(user_id, fn_args)
                    awaiting_confirmation = True
                    break  # a human confirmation is needed — stop reasoning here

                if fn_name in PERSONAL_AGENT_METHOD_TOOLS:
                    result = getattr(self, fn_name)(**fn_args)
                else:
                    skill_name = PERSONAL_TOOL_SKILL_MAP.get(fn_name)
                    result = self.invoke_skill(skill_name, **fn_args) if skill_name else {"success": False, "error": f"Herramienta desconocida: {fn_name}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, default=str, ensure_ascii=False)[:6000],
                })

            if awaiting_confirmation:
                break
        else:
            reply = "Necesité demasiados pasos para resolver esto — ¿puedes ser más específico?"

        self._conversations[user_id].append({"role": "assistant", "content": reply})
        self._memory.save_message(user_id, "user", text)
        self._memory.save_message(user_id, "assistant", reply)
        return reply

    def _draft_event_confirmation(self, user_id: str, fn_args: dict) -> str:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO pending_confirmations (userId, actionType, payload, summary, status, createdAt) "
                "VALUES (?, 'create_calendar_event', ?, ?, 'pending', ?)",
                (user_id, json.dumps(fn_args), fn_args.get("summary", ""), datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        attendees = ", ".join(fn_args.get("attendees", []))
        return (
            "📅 *Borrador de evento — pendiente de tu confirmación:*\n\n"
            f"*{fn_args.get('summary')}*\n"
            f"🕐 {fn_args.get('start')} → {fn_args.get('end')}\n"
            f"👥 Invitados: {attendees}\n"
            + (f"📝 {fn_args.get('description')}\n" if fn_args.get("description") else "")
            + "\n¿Confirmas que lo agende y les envíe la invitación? Responde *sí* o *no*."
        )

    def _get_pending_confirmation(self, user_id: str) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM pending_confirmations WHERE userId=? AND status='pending' ORDER BY createdAt DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _resolve_pending_confirmation(self, user_id: str, pending: dict, text: str) -> str:
        action_type = pending.get("actionType")
        if action_type == "meeting_output_choice":
            return self._resolve_meeting_output_choice(pending, text)
        return self._resolve_calendar_confirmation(pending, text)

    def _resolve_calendar_confirmation(self, pending: dict, text: str) -> str:
        text_lower = text.strip().lower()
        conn = get_db()
        try:
            if any(w == text_lower or w in text_lower for w in PERSONAL_CONFIRM_YES):
                fn_args = json.loads(pending["payload"])
                result = self.invoke_skill("create_calendar_event", **fn_args)
                conn.execute(
                    "UPDATE pending_confirmations SET status='confirmed', resolvedAt=? WHERE id=?",
                    (datetime.now().isoformat(), pending["id"]),
                )
                conn.commit()
                if result.get("success", True) and result.get("result"):
                    link = result["result"].get("link", "")
                    return f"✅ Listo — evento creado y invitaciones enviadas.\n{link}"
                return f"⚠️ No pude crear el evento: {result.get('error', 'error desconocido')}"

            if any(w == text_lower or w in text_lower for w in PERSONAL_CONFIRM_NO):
                conn.execute(
                    "UPDATE pending_confirmations SET status='cancelled', resolvedAt=? WHERE id=?",
                    (datetime.now().isoformat(), pending["id"]),
                )
                conn.commit()
                return "Cancelado — no se creó el evento ni se envió ninguna invitación."

            summary = json.loads(pending["payload"]).get("summary", "")
            return (
                f"Tienes un evento pendiente de confirmación: *{summary}*.\n"
                "Responde *sí* para confirmarlo o *no* para cancelarlo."
            )
        finally:
            conn.close()

    def _resolve_meeting_output_choice(self, pending: dict, text: str) -> str:
        text_lower = text.strip().lower()
        payload = json.loads(pending["payload"])
        meeting_note_id = payload["meetingNoteId"]

        if any(w in text_lower for w in ["1", "compromiso", "compromisos"]):
            output_format = "commitments"
        elif any(w in text_lower for w in ["2", "documento", "doc", "resumen"]):
            output_format = "document"
        elif any(w in text_lower for w in ["3", "presentación", "presentacion", "slides"]):
            output_format = "slides"
        else:
            return (
                "No entendí qué formato quieres para esas notas de reunión. "
                "Responde *1* (solo compromisos), *2* (resumen en documento) o *3* (presentación)."
            )

        conn = get_db()
        try:
            conn.execute(
                "UPDATE pending_confirmations SET status='confirmed', resolvedAt=? WHERE id=?",
                (datetime.now().isoformat(), pending["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        return self.generate_meeting_output(meeting_note_id, output_format)

    # ── MEETING NOTES (Fathom webhook OR agent-joined call → Slack ask → generate) ──

    def ingest_meeting_content(self, title: str, transcript: str, summary: str,
                                action_items: list, fathom_meeting_id: str = None) -> int:
        """
        Single write path for 'a meeting produced content' — used by both the
        Fathom webhook and the agent's own join-and-record flow. Writes
        meeting_notes + commitments, then asks Junior how he wants it.
        """
        conn = get_db()
        try:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO meeting_notes
                (title, fathomMeetingId, transcriptText, summaryText, rawActionItems, status, createdAt)
                VALUES (?, ?, ?, ?, ?, 'awaiting_format_choice', ?)
                """,
                (title, fathom_meeting_id, transcript, summary, json.dumps(action_items, ensure_ascii=False),
                 datetime.now().isoformat()),
            )
            meeting_note_id = c.lastrowid

            for item in action_items:
                description = item.get("description") if isinstance(item, dict) else str(item)
                owner = item.get("owner") if isinstance(item, dict) else None
                due_hint = item.get("due") if isinstance(item, dict) else None
                c.execute(
                    "INSERT INTO commitments (meetingNoteId, description, owner, dueHint, status, createdAt) "
                    "VALUES (?, ?, ?, ?, 'open', ?)",
                    (meeting_note_id, description, owner, due_hint, datetime.now().isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

        self.notify_meeting_note_ready(meeting_note_id, title, len(action_items))
        return meeting_note_id

    def process_recorded_meeting(self, title: str, transcript: str, meet_url: str = None) -> None:
        """Called by meeting_join_service once a call it recorded has ended and been transcribed."""
        result = self.invoke_skill("summarize_transcript", transcript=transcript, title=title)
        if result.get("success", True) and result.get("result"):
            summary = result["result"].get("summary", "")
            action_items = result["result"].get("action_items", [])
        else:
            summary, action_items = "", []  # never drop the transcript even if summarization fails
        self.ingest_meeting_content(title=title, transcript=transcript, summary=summary, action_items=action_items)

    # ── MEETING JOIN CONTROL (Junior-only, Slack or dashboard) ──────

    def join_meeting(self, meet_url: str, calendar_event_id: str = None, title: str = None) -> dict:
        svc = self._kernel.get_service("meeting_join_service")
        if not svc:
            return {"success": False, "error": "meeting_join_service no está registrado"}
        return svc.start_join(meet_url=meet_url, calendar_event_id=calendar_event_id, title=title)

    def leave_meeting(self) -> dict:
        svc = self._kernel.get_service("meeting_join_service")
        if not svc:
            return {"success": False, "error": "meeting_join_service no está registrado"}
        return svc.request_leave()

    def meeting_status(self) -> dict:
        svc = self._kernel.get_service("meeting_join_service")
        if not svc:
            return {"success": False, "error": "meeting_join_service no está registrado"}
        return svc.get_status()

    def notify_meeting_note_ready(self, meeting_note_id: int, title: str, action_items_count: int) -> None:
        """Called once a meeting's transcript/summary/action_items are ready — asks Junior the format."""
        if not JUNIOR_SLACK_ID:
            return
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO pending_confirmations (userId, actionType, payload, summary, status, createdAt) "
                "VALUES (?, 'meeting_output_choice', ?, ?, 'pending', ?)",
                (JUNIOR_SLACK_ID, json.dumps({"meetingNoteId": meeting_note_id}), title, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        text = (
            f"🐼 *Terminé de tomar nota de '{title}'* ({action_items_count} compromiso(s) detectado(s)).\n\n"
            "¿Cómo quieres el resultado?\n"
            "1️⃣ Solo los compromisos\n"
            "2️⃣ Resumen en documento\n"
            "3️⃣ Presentación\n\n"
            "Responde con el número o la palabra."
        )
        slack_svc = self._kernel.get_service("slack_service")
        if slack_svc and slack_svc.client:
            slack_svc.send_notification(JUNIOR_SLACK_ID, text)

    def generate_meeting_output(self, meeting_note_id: int, output_format: str) -> str:
        conn = get_db()
        try:
            note = conn.execute("SELECT * FROM meeting_notes WHERE id=?", (meeting_note_id,)).fetchone()
            if not note:
                return "⚠️ No encontré esas notas de reunión."
            note = dict(note)
            commitments = [
                dict(c) for c in conn.execute(
                    "SELECT * FROM commitments WHERE meetingNoteId=? ORDER BY id", (meeting_note_id,)
                ).fetchall()
            ]
        finally:
            conn.close()

        if output_format == "commitments":
            reply = self._format_commitments_reply(note["title"], commitments)
            self._mark_meeting_note_done(meeting_note_id, "commitments")
            return reply

        if output_format == "document":
            result = self.invoke_skill(
                "create_summary_doc", title=note["title"], summary=note["summaryText"], commitments=commitments
            )
            if not result.get("success", True):
                return f"⚠️ No pude crear el documento: {result.get('error')}"
            link = result["result"]["link"]
            self._mark_meeting_note_done(meeting_note_id, "document", doc_link=link)
            return f"✅ Listo — resumen creado:\n{link}"

        if output_format == "slides":
            result = self.invoke_skill(
                "create_summary_slides", title=note["title"], summary=note["summaryText"], commitments=commitments
            )
            if not result.get("success", True):
                return f"⚠️ No pude crear la presentación: {result.get('error')}"
            link = result["result"]["link"]
            self._mark_meeting_note_done(meeting_note_id, "slides", slides_link=link)
            return f"✅ Listo — presentación creada:\n{link}"

        return "Formato no reconocido."

    def _format_commitments_reply(self, title: str, commitments: list) -> str:
        if not commitments:
            return f"No detecté compromisos claros en '{title}'."
        lines = [f"📋 *Compromisos de '{title}':*\n"]
        for c in commitments:
            owner = f" — {c['owner']}" if c.get("owner") else ""
            due = f" (para {c['dueHint']})" if c.get("dueHint") else ""
            lines.append(f"• {c['description']}{owner}{due}")
        return "\n".join(lines)

    def _mark_meeting_note_done(self, meeting_note_id: int, output_format: str, doc_link: str = None, slides_link: str = None) -> None:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE meeting_notes SET status='done', outputFormat=?, docLink=?, slidesLink=? WHERE id=?",
                (output_format, doc_link, slides_link, meeting_note_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _fetch_relevant_context(self, text: str) -> str:
        """Decides what BambooHR data to fetch based on the user's question."""
        from skills.bamboo import _bamboo_get, _bamboo_report, get_employee_details
        text_lower = text.lower()

        if any(w in text_lower for w in ["directorio", "directory", "todos los empleados", "cuántos"]):
            data = _bamboo_get("employees/directory")
            if data:
                employees = data.get("employees", [])
                lines = [f"Directorio BambooHR ({len(employees)} empleados):"]
                for emp in employees[:50]:
                    name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                    lines.append(f"• {name} — {emp.get('jobTitle', 'N/A')} ({emp.get('department', 'N/A')})")
                return "\n".join(lines)

        if any(w in text_lower for w in ["próximo ingreso", "próximos ingresos", "upcoming", "va a entrar", "quién entra"]):
            result = self._skills["detect_employees"].execute(lookback_days=60)
            if result.get("result"):
                lines = [f"Próximos ingresos ({result['count']}):"]
                for emp, hire_date in result["result"]:
                    name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                    lines.append(
                        f"• {name} — {emp.get('jobTitle', 'N/A')} | "
                        f"Inicia: {hire_date.strftime('%d/%m/%Y')} | "
                        f"Manager: {emp.get('supervisor', 'N/A')} | "
                        f"País: {emp.get('country', 'N/A')}"
                    )
                return "\n".join(lines)

        if any(w in text_lower for w in ["ausente", "ausencia", "time off", "quien falta"]):
            today = datetime.today().strftime("%Y-%m-%d")
            data = _bamboo_get(f"time_off/whos_out/?start={today}&end={today}")
            if data:
                lines = ["Ausencias hoy:"]
                for item in data:
                    lines.append(f"• {item.get('name', 'Desconocido')} — {item.get('type', {}).get('name', 'Ausencia')}")
                return "\n".join(lines) if len(lines) > 1 else "No hay ausencias registradas hoy."

        for trigger in ["información de", "datos de", "busca a", "quién es", "dime sobre"]:
            if trigger in text_lower:
                name_candidate = text_lower.split(trigger)[-1].strip().rstrip("?").strip()
                if len(name_candidate) > 2:
                    employees = _bamboo_report()
                    matches = [
                        emp for emp in employees
                        if name_candidate in f"{emp.get('firstName', '')} {emp.get('lastName', '')}".lower()
                    ]
                    if matches:
                        lines = []
                        for emp in matches[:3]:
                            name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                            lines.append(
                                f"• {name} — {emp.get('jobTitle', 'N/A')} | "
                                f"{emp.get('department', 'N/A')} | "
                                f"Manager: {emp.get('supervisor', 'N/A')} | "
                                f"Ingresó: {emp.get('hireDate', 'N/A')}"
                            )
                        return "\n".join(lines)
                break

        return ""

    # ── PLANNING ────────────────────────────────────────────────

    def plan_onboarding(self, emp: dict, hire_date) -> dict:
        """
        Plans the complete onboarding for one employee.
        Consults memory for lessons learned. Returns a structured plan.
        """
        location = emp.get("location", "") or emp.get("country", "")
        country = emp.get("country", "")

        # Apply country rules
        country_result = self._skills["country_rules"].execute(
            location=location, country=country, hire_date=hire_date.strftime("%Y-%m-%d")
        )
        rules = country_result["result"]
        lang = rules["language"]
        onboarding_time = rules["onboardingTime"]
        cc_email = rules["ccEmail"]

        # Apply business calendar
        calendar_result = self._skills["business_calendar"].execute(
            hire_date=hire_date.strftime("%Y-%m-%d")
        )
        manager_notify_date = calendar_result["result"]["managerNotifyDate"]
        email_notify_date = calendar_result["result"]["emailNotifyDate"]
        koronet_notify_date = calendar_result["result"]["koronetNotifyDate"]

        # Check lessons learned for this country/department
        lessons = self._memory.get_lessons_for_context(country=country)
        lesson_insights = [l.get("whatWorked", "") for l in lessons if l.get("whatWorked")]

        plan = {
            "employeeName": f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip(),
            "hireDate": hire_date.strftime("%Y-%m-%d"),
            "language": lang,
            "onboardingTime": onboarding_time,
            "ccEmail": cc_email,
            "managerNotifyDate": manager_notify_date,
            "emailNotifyDate": email_notify_date,
            "koronetNotifyDate": koronet_notify_date,
            "steps": [
                "generate_welcome_image",
                "generate_employee_email",
                "generate_manager_slack",
                "generate_bienvenida_announcement",
                "queue_for_approval",
            ],
            "lessonsApplied": lesson_insights[:3],
            "planningReasoning": (
                f"Employee from {country or 'unknown country'} → language={lang}. "
                f"Manager notification scheduled for {manager_notify_date}. "
                f"Applied {len(lesson_insights)} lesson(s) from previous onboardings."
            ),
        }

        self._memory.record_decision(
            skill_name="plan_onboarding",
            decision=f"Planned onboarding for {plan['employeeName']}",
            reasoning=plan["planningReasoning"],
            context={"employee": emp.get("workEmail", ""), "country": country},
            confidence=0.95,
        )

        return plan

    # ── SYNC (CORE LOOP) ─────────────────────────────────────────

    def run_sync(self, dry_run: bool = False, lookback_days: int = 60) -> dict:
        """
        The main detection and preparation cycle.
        G1: Detect new hires
        G2: Prepare complete onboarding
        G3: Queue for manager coordination
        Never sends anything — only prepares for approval.
        """
        conn = get_db()
        c = conn.cursor()

        try:
            # Migración silenciosa
            try:
                c.execute("ALTER TABLE batches ADD COLUMN employeeName TEXT")
                conn.commit()
            except Exception:
                pass

            c.execute(
                "INSERT INTO runs (startedAt, status, dryRun) VALUES (?, 'running', ?)",
                (datetime.now().isoformat(), 1 if dry_run else 0),
            )
            run_id = c.lastrowid
            conn.commit()

            # G1: Detect
            # A list, not a set — record_decision() below JSON-serializes
            # every kwarg passed to a skill, and json.dumps can't handle a
            # set. That crash used to fire on every sync that successfully
            # reached BambooHR (the only path that hits record_decision).
            already_ids = list(dict.fromkeys(
                row[0] for row in c.execute(
                    "SELECT DISTINCT employeeId FROM messages"
                ).fetchall()
            ))
            detect_result = self._skills["detect_employees"].execute(
                lookback_days=lookback_days,
                already_processed=already_ids,
            )

            scanned = detect_result.get("scanned", 0)
            new_employees = detect_result.get("result", [])
            messages_created = 0
            batch_id = None

            if new_employees and not dry_run:
                for emp, hire_date in new_employees:
                    # Enrich with full details
                    bamboo_id = str(emp.get("id", ""))
                    if bamboo_id:
                        details = get_employee_details(bamboo_id)
                        for k, v in details.items():
                            if v and not emp.get(k):
                                emp[k] = v

                        # Job Information (manager, title, department...) is a
                        # dated table — the flat profile fields above only
                        # reflect whichever row BambooHR currently considers
                        # "current", which for a rehire can still be the
                        # *previous* stint's title/manager (same employee
                        # record, employmentHistoryStatus stuck on
                        # "Terminated" until their new row's date arrives).
                        # The dated row effective on/closest-before the hire
                        # date is the authoritative source for THIS hire, so
                        # it always overrides the flat snapshot — never just
                        # fills blanks — otherwise a rehire silently keeps
                        # the old manager/title from their last stint.
                        job_info_rows = get_job_info_table(bamboo_id)
                        dated_rows = sorted(
                            (r for r in job_info_rows if r.get("date")),
                            key=lambda r: r["date"],
                        )
                        hire_str_cmp = hire_date.strftime("%Y-%m-%d")
                        applicable = [r for r in dated_rows if r["date"] <= hire_str_cmp]
                        job_info = applicable[-1] if applicable else (dated_rows[-1] if dated_rows else {})
                        for k in ("reportsTo", "jobTitle", "department", "division", "location"):
                            v = job_info.get(k)
                            if v:
                                emp[k] = v
                        if job_info.get("reportsTo"):
                            # supervisorEId came from the stale flat profile
                            # (e.g. the previous stint's manager) — drop it so
                            # the name-based lookup below resolves the NEW
                            # manager from reportsTo instead of the old id.
                            emp["supervisorEId"] = ""

                    emp_name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                    emp_number = str(emp.get("employeeNumber", ""))
                    location = emp.get("location", "") or emp.get("country", "")
                    country = emp.get("country", "")
                    manager = emp.get("reportsTo") or emp.get("supervisor") or ""
                    manager_first_name = resolve_manager_first_name(emp.get("supervisorEId", ""), manager)

                    # G2: Plan
                    plan = self.plan_onboarding(emp, hire_date)
                    lang = plan["language"]
                    onboarding_time = plan["onboardingTime"]

                    # Generate the flyer ONLY if BambooHR has a photo — a flyer with a
                    # blank/black circle where the photo should be is worse than no
                    # flyer at all, so we never generate or save that version. If
                    # there's no photo yet, the batch is flagged and Junior is asked
                    # for one; the flyer gets created once he provides it.
                    photo_bytes = get_employee_photo(bamboo_id) if bamboo_id else None
                    image_path = None
                    if photo_bytes:
                        image_result = self._skills["generate_image"].execute(
                            name=emp_name,
                            job_title=emp.get("jobTitle", ""),
                            photo_bytes=photo_bytes,
                        )
                        image_path = image_result.get("imagePath") if image_result.get("success", True) else None
                    photo_status = "complete" if image_path else "missing"

                    # Create batch for this employee
                    c.execute(
                        "INSERT INTO batches (createdAt, messageCount, employeeName, photoStatus, bambooEmployeeId) VALUES (?, 3, ?, ?, ?)",
                        (datetime.now().isoformat(), emp_name, photo_status, bamboo_id),
                    )
                    batch_id = c.lastrowid
                    conn.commit()

                    # Content generation
                    email_result = self._skills["generate_email"].execute(
                        first_name=emp.get("firstName", ""),
                        hire_date=hire_date.strftime("%Y-%m-%d"),
                        language=lang,
                        onboarding_time=onboarding_time,
                    )
                    manager_result = self._skills["generate_slack_manager"].execute(
                        manager_name=manager_first_name,
                        employee_name=emp_name,
                        language=lang,
                    )
                    bienvenida_result = self._skills["generate_slack_bienvenida"].execute(
                        first_name=emp.get("firstName", ""),
                        last_name=emp.get("lastName", ""),
                        job_title=emp.get("jobTitle", "New Team Member"),
                        gender=emp.get("gender", ""),
                    )

                    hire_str = hire_date.strftime("%Y-%m-%d")

                    # Queue all 3 messages for approval (NEVER send directly)
                    for msg_type, content_result, extra_emp_id, img in [
                        ("employee_email", email_result, emp_number, image_path),
                        ("manager_slack", manager_result, emp_number + "_mgr", None),
                        ("bienvenidas_slack", bienvenida_result, emp_number + "_bvd", image_path),
                    ]:
                        manager_message_date = plan["managerNotifyDate"] if msg_type == "manager_slack" else None
                        email_message_date = plan["emailNotifyDate"] if msg_type == "employee_email" else None
                        koronet_message_date = plan["koronetNotifyDate"] if msg_type == "bienvenidas_slack" else None
                        c.execute("""
                            INSERT INTO messages
                            (runId, employeeId, employeeName, employeeJobTitle, employeeHireDate,
                             employeeEmail, employeeLocation, managerName, messageType, messageContent,
                             language, imagePath, status, createdAt, batchId, managerMessageDate,
                             emailMessageDate, koronetMessageDate, ccEmail)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?, ?)
                        """, (
                            run_id, extra_emp_id, emp_name, emp.get("jobTitle", ""),
                            hire_str, emp.get("workEmail", ""), location, manager,
                            msg_type, content_result.get("result", ""), lang,
                            img, datetime.now().isoformat(), batch_id, manager_message_date,
                            email_message_date, koronet_message_date,
                            plan["ccEmail"] if msg_type == "employee_email" else None,
                        ))
                        messages_created += 1

                    conn.commit()

                    if photo_status == "missing":
                        self._notify_missing_photo(emp_name, batch_id)

                    # Record employee in memory
                    self._memory.remember_employee({
                        "bambooId": bamboo_id,
                        "employeeNumber": emp_number,
                        "name": emp_name,
                        "email": emp.get("workEmail", ""),
                        "country": country,
                        "language": lang,
                        "hireDate": hire_str,
                        "jobTitle": emp.get("jobTitle", ""),
                        "department": emp.get("department", ""),
                        "managerName": manager,
                        "onboardingStatus": "prepared",
                        "batchId": batch_id,
                        "imagePath": image_path,
                    })

                    # Record manager in memory
                    if manager:
                        self._memory.remember_manager(manager)

                    # G3: Notify approver
                    self._notify_approver_via_slack(emp_name, emp, hire_date, plan)

            c.execute("""
                UPDATE runs SET completedAt=?, status='completed',
                employeesScanned=?, newEmployees=?, messagesSent=? WHERE id=?
            """, (datetime.now().isoformat(), scanned, len(new_employees), messages_created, run_id))
            conn.commit()

            return {
                "runId": run_id,
                "employeesScanned": scanned,
                "newEmployees": len(new_employees),
                "messagesSent": messages_created,
                "dryRun": dry_run,
                "batchId": batch_id,
                "agentNote": (
                    f"Detecté {len(new_employees)} empleado(s) nuevo(s). "
                    f"Preparé {messages_created} mensaje(s). "
                    f"Todos están en cola de aprobación — nada fue enviado todavía."
                ),
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            c.execute(
                "UPDATE runs SET status='failed', completedAt=? WHERE id=?",
                (datetime.now().isoformat(), run_id),
            )
            conn.commit()
            raise e
        finally:
            conn.close()

    def _notify_approver_via_slack(self, emp_name: str, emp: dict, hire_date, plan: dict) -> None:
        """Notifies Junior via Slack when a new batch is ready for approval."""
        slack_svc = self._kernel.get_service("slack_service")
        if not slack_svc or not slack_svc.client:
            return
        try:
            juni_id = slack_svc.find_user_by_name("junior")
            if not juni_id:
                return
            send_date = plan.get("managerNotifyDate", "pronto")
            text = (
                f"🐼 *Panda Bear* — Nuevo ingreso detectado!\n\n"
                f"👤 *{emp_name}*\n"
                f"💼 {emp.get('jobTitle', 'N/A')}\n"
                f"📍 {emp.get('location', '') or emp.get('country', 'N/A')}\n"
                f"📅 Inicia el {hire_date.strftime('%d/%m/%Y')}\n"
                f"👔 Manager: {emp.get('supervisor', 'N/A')}\n"
                f"📨 Mensaje al manager: {send_date}\n\n"
                f"👉 Abre el dashboard para aprobar los mensajes."
            )
            slack_svc.send_notification(juni_id, text)
        except Exception as e:
            print(f"[Agent] Could not notify approver: {e}")

    def _notify_missing_photo(self, emp_name: str, batch_id: int) -> None:
        """BambooHR has no photo for this hire yet — ask Junior for one instead of
        generating a flyer with a blank/black circle where the photo should be."""
        slack_svc = self._kernel.get_service("slack_service")
        if not slack_svc or not slack_svc.client:
            return
        try:
            juni_id = slack_svc.find_user_by_name("junior")
            if not juni_id:
                return
            text = (
                f"🐼 *{emp_name}* no tiene foto cargada en BambooHR — no genero el flyer "
                f"con el círculo en negro. Los mensajes de texto ya están listos para revisar "
                f"en el dashboard (batch #{batch_id}). Subí la foto cuando la tengas y yo armo "
                f"el flyer: `POST /api/agent/batches/{batch_id}/photo` con la imagen, o pedime "
                f"que lo haga por vos."
            )
            slack_svc.send_notification(juni_id, text)
        except Exception as e:
            print(f"[Agent] Could not notify missing photo: {e}")

    def attach_employee_photo(self, batch_id: int, photo_bytes: bytes) -> dict:
        """
        Generates the flyer now that a photo is available and attaches it to every
        message in the batch that carries an image (employee_email, bienvenidas_slack).
        """
        conn = get_db()
        try:
            batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                return {"success": False, "error": f"Batch {batch_id} not found"}

            msgs = conn.execute("SELECT * FROM messages WHERE batchId=?", (batch_id,)).fetchall()
            if not msgs:
                return {"success": False, "error": f"No messages found for batch {batch_id}"}
            ref = dict(msgs[0])

            image_result = self._skills["generate_image"].execute(
                name=ref["employeeName"],
                job_title=ref.get("employeeJobTitle", ""),
                photo_bytes=photo_bytes,
            )
            if not image_result.get("success", True):
                return {"success": False, "error": image_result.get("error", "Image generation failed")}

            image_path = image_result["imagePath"]
            conn.execute(
                "UPDATE messages SET imagePath=? WHERE batchId=? AND messageType IN ('employee_email','bienvenidas_slack')",
                (image_path, batch_id),
            )
            conn.execute("UPDATE batches SET photoStatus='complete' WHERE id=?", (batch_id,))
            conn.commit()

            return {"success": True, "imagePath": image_path}
        finally:
            conn.close()

    # ── MORNING BRIEF ────────────────────────────────────────────

    def morning_brief(self) -> dict:
        """
        Generates the Morning Brief — the agent's summary of the current state.
        The dashboard shows exactly what the agent returns here.
        """
        conn = get_db()
        try:
            pending_batches = conn.execute(
                "SELECT * FROM batches WHERE status='pending_approval'"
            ).fetchall()
            pending_msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE status='pending_approval'"
            ).fetchone()[0]
            ready_batches = conn.execute(
                "SELECT COUNT(*) FROM batches WHERE status='approved'"
            ).fetchone()[0]
            total_sent = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE status='sent'"
            ).fetchone()[0]
            total_failed = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE status='failed'"
            ).fetchone()[0]
            last_run = conn.execute(
                "SELECT * FROM runs ORDER BY startedAt DESC LIMIT 1"
            ).fetchone()

            # Upcoming hires
            from skills.bamboo import _bamboo_report
            employees = _bamboo_report()
            today = datetime.today()
            upcoming_today = [
                emp for emp in employees
                if emp.get("hireDate") == today.strftime("%Y-%m-%d")
            ]
            def _days_until_hire(emp):
                # BambooHR returns '0000-00-00' for hires with no date set
                try:
                    return (datetime.strptime(emp.get("hireDate") or "", "%Y-%m-%d") - today).days
                except ValueError:
                    return None

            upcoming_week = [
                emp for emp in employees
                if (d := _days_until_hire(emp)) is not None and 0 <= d <= 7
            ]

            pending_managers = set()
            for batch in pending_batches:
                row = conn.execute(
                    "SELECT DISTINCT managerName FROM messages WHERE batchId=?",
                    (batch["id"],),
                ).fetchone()
                if row and row[0]:
                    pending_managers.add(row[0])

            narrative = self._generate_brief_narrative(
                today=len(upcoming_today),
                this_week=len(upcoming_week),
                pending_approval=len(pending_batches),
                pending_messages=pending_msgs,
                managers_pending=len(pending_managers),
                total_sent=total_sent,
                total_failed=total_failed,
            )

            return {
                "narrative": narrative,
                "date": today.strftime("%A, %d de %B de %Y"),
                "stats": {
                    "hiresToday": len(upcoming_today),
                    "hiresThisWeek": len(upcoming_week),
                    "pendingApproval": len(pending_batches),
                    "pendingMessages": pending_msgs,
                    "managersNotResponded": len(pending_managers),
                    "totalSent": total_sent,
                    "totalFailed": total_failed,
                    "readyBatches": ready_batches,
                },
                "pendingBatches": [dict(b) for b in pending_batches],
                "upcomingToday": [
                    {"name": f"{e.get('firstName', '')} {e.get('lastName', '')}".strip(),
                     "jobTitle": e.get("jobTitle", ""), "department": e.get("department", "")}
                    for e in upcoming_today
                ],
                "lastRun": dict(last_run) if last_run else None,
                "agentStatus": "active",
            }
        finally:
            conn.close()

    def _generate_brief_narrative(self, today: int, this_week: int, pending_approval: int,
                                   pending_messages: int, managers_pending: int,
                                   total_sent: int, total_failed: int) -> str:
        parts = []
        if today > 0:
            parts.append(f"Hoy ingresan {today} persona(s).")
        if this_week > today:
            parts.append(f"Esta semana entran {this_week} en total.")
        if pending_approval == 0:
            parts.append("No hay mensajes pendientes de aprobación.")
        elif pending_approval == 1:
            parts.append(f"Hay 1 batch pendiente con {pending_messages} mensaje(s) para revisar.")
        else:
            parts.append(f"Hay {pending_approval} batches pendientes con {pending_messages} mensajes para revisar.")
        if managers_pending > 0:
            parts.append(f"{managers_pending} manager(s) aún no han recibido su notificación.")
        if total_failed > 0:
            parts.append(f"⚠️ {total_failed} mensaje(s) fallaron y requieren atención.")
        if not parts:
            parts.append("Todo está en orden. No hay acciones pendientes.")
        return " ".join(parts)

    # ── LEARNING ────────────────────────────────────────────────

    def learn_from_batch(self, batch_id: int) -> dict:
        """
        After a batch is completed, records lessons learned.
        G5: Learn continuously from HR feedback.
        """
        conn = get_db()
        try:
            msgs = conn.execute("SELECT * FROM messages WHERE batchId=?", (batch_id,)).fetchall()
            if not msgs:
                return {"learned": False, "reason": "No messages found for this batch"}

            approved = sum(1 for m in msgs if m["status"] in ("sent", "approved", "draft_created", "ready_for_slack_draft"))
            edited = sum(1 for m in msgs if m["status"] == "sent")
            rejected = sum(1 for m in msgs if m["status"] == "rejected")
            skipped = sum(1 for m in msgs if m["status"] == "skipped")

            approvals = conn.execute(
                "SELECT * FROM approval_memory WHERE messageId IN (SELECT id FROM messages WHERE batchId=?)",
                (batch_id,),
            ).fetchall()

            what_worked = []
            what_failed = []
            for a in approvals:
                if a["action"] == "approved":
                    what_worked.append(f"Message type '{a['messageType']}' was approved without changes")
                elif a["action"] in ("rejected", "skipped"):
                    what_failed.append(f"Message type '{a['messageType']}' was {a['action']}")

            emp_name = msgs[0]["employeeName"] if msgs else "Unknown"
            emp_id = msgs[0]["employeeId"] if msgs else ""
            country = msgs[0]["employeeLocation"] if msgs else ""

            self._memory.record_lesson(
                employee_name=emp_name,
                employee_id=emp_id,
                country=country,
                department="",
                approved=approved,
                edited=edited,
                rejected=rejected,
                what_worked="; ".join(what_worked) if what_worked else "No notes",
                what_failed="; ".join(what_failed) if what_failed else "No notes",
                insights={
                    "approvalRate": round(approved / len(msgs), 2) if msgs else 0,
                    "batchId": batch_id,
                },
            )

            self._memory.record_decision(
                skill_name="learn_from_batch",
                decision=f"Recorded lessons from batch {batch_id} (employee: {emp_name})",
                reasoning=(
                    f"Batch completed: {approved} approved, {rejected} rejected, {skipped} skipped. "
                    f"Approval rate: {round(approved / len(msgs) * 100)}%."
                ),
                context={"batchId": batch_id, "employee": emp_name},
                outcome="learned",
            )

            return {
                "learned": True,
                "employee": emp_name,
                "approved": approved,
                "rejected": rejected,
                "skipped": skipped,
                "whatWorked": what_worked,
                "whatFailed": what_failed,
            }
        finally:
            conn.close()

    # ── JUDGMENT ────────────────────────────────────────────────

    def judge_action(self, action: str, context: dict = None) -> dict:
        """
        Checks if an action is permitted by the constitution.
        Every action must pass through this before execution.
        """
        result = check_constitution(action)
        self._memory.record_decision(
            skill_name="judgment",
            decision=f"{'ALLOWED' if result['allowed'] else 'BLOCKED'}: {action}",
            reasoning=(
                f"Constitution check: {len(result['blocking_rules'])} blocking rule(s). "
                + (f"Blocked by: {[r['code'] for r in result['blocking_rules']]}" if not result["allowed"] else "No restrictions.")
            ),
            context=context or {},
            confidence=1.0,
        )
        return result

    # ── EXECUTE ─────────────────────────────────────────────────

    # Approving a message = creating its DRAFT. Panda Bear never sends or
    # posts anything itself; Junior reviews each draft and sends it by hand.
    # See DECISIONS.md — do not add any code path that sends on approval.
    #
    # Message statuses after approval (one of these three, never 'sent'):
    #   draft_created         → a real draft exists (Gmail draft id stored in draftId)
    #   ready_for_slack_draft → approved, destination verified, but the Slack draft
    #                           does NOT exist yet: Slack has no public API for a bot
    #                           to create drafts, so Claude stages it on request.
    #   failed                → nothing was created; deliveryError says why.
    # 'sent' is only ever set by Junior himself ("Ya lo envié" in the dashboard).

    MESSAGE_LABELS = {
        "employee_email": "Correo de bienvenida",
        "manager_slack": "DM al líder",
        "bienvenidas_slack": "Bienvenida en #koronet",
    }
    NON_SPANISH_CC = "moira.gago@koronet.com"

    def approve_message(self, msg_id: int, notify: bool = True) -> dict:
        """Approves ONE message and immediately creates its draft. Never raises."""
        claim = self._skills["approval_workflow"].execute(action="approve_message", message_id=msg_id)
        if not claim.get("success"):
            return {"id": msg_id, "status": claim.get("currentStatus"), "created": False,
                    "error": claim.get("error", "No se pudo aprobar")}
        result = self.prepare_message_draft(msg_id)
        self._refresh_batch_status(claim.get("batchId"))
        if notify:
            self._notify_execution_result(claim.get("batchId"), [result])
            self.kick_slack_drafts([result])
        return result

    def approve_all(self, batch_id: int) -> dict:
        """'Aprobar todos': approves every still-pending message of a hire, one by one."""
        conn = get_db()
        try:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM messages WHERE batchId=? AND status='pending_approval' ORDER BY id",
                (batch_id,),
            ).fetchall()]
        finally:
            conn.close()
        results = [self.approve_message(i, notify=False) for i in ids]
        self._refresh_batch_status(batch_id)
        if results:
            self._notify_execution_result(batch_id, results)
            self.kick_slack_drafts(results)
        return {
            "batchId": batch_id,
            "approved": len(results),
            "created": sum(1 for r in results if r["status"] == "draft_created"),
            "results": results,
        }

    def prepare_message_draft(self, msg_id: int) -> dict:
        """
        Turns one approved (or previously failed) message into a draft and records
        the honest outcome. Any exception ends in 'failed' with the error stored —
        an approval can never be left silently 'approved' or marked 'sent'.
        """
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return {"id": msg_id, "status": None, "created": False, "error": "Mensaje no encontrado"}
        msg = dict(row)
        if msg["status"] not in ("approved", "failed"):
            return self._result_row(msg, {"status": msg["status"], "note": "Sin cambios: no estaba aprobado"})

        try:
            outcome = self._create_draft_for(msg)
        except Exception as e:
            outcome = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        if outcome.get("status") not in ("draft_created", "ready_for_slack_draft", "failed"):
            outcome = {"status": "failed", "error": f"Estado inesperado: {outcome.get('status')}"}

        conn = get_db()
        try:
            conn.execute(
                "UPDATE messages SET status=?, deliveryError=?, draftId=COALESCE(?, draftId), "
                "slackTarget=COALESCE(?, slackTarget), ccEmail=COALESCE(?, ccEmail), "
                "employeeEmail=COALESCE(?, employeeEmail), deliveredAt=?, "
                "slackDraftAttempts=CASE WHEN ?='ready_for_slack_draft' THEN 0 ELSE slackDraftAttempts END "
                "WHERE id=?",
                (
                    outcome["status"],
                    outcome.get("error") if outcome["status"] == "failed" else None,
                    outcome.get("draftId"), outcome.get("slackTarget"), outcome.get("cc"),
                    outcome.get("to"),
                    datetime.now().isoformat() if outcome["status"] == "draft_created" else None,
                    outcome["status"],
                    msg_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = self._result_row(msg, outcome)
        try:
            self._memory.record_decision(
                skill_name="prepare_message_draft",
                decision=f"Message {msg_id} ({msg['messageType']}) for {msg['employeeName']} → {outcome['status']}",
                reasoning="HR approved this message. Panda Bear only prepares drafts — Junior sends them himself.",
                context=result,
                outcome="error" if outcome["status"] == "failed" else "success",
            )
        except Exception as e:
            print(f"[Agent] Could not record draft decision: {e}")
        return result

    def _result_row(self, msg: dict, outcome: dict) -> dict:
        return {
            "id": msg["id"],
            "batchId": msg.get("batchId"),
            "type": msg["messageType"],
            "label": self.MESSAGE_LABELS.get(msg["messageType"], msg["messageType"]),
            "status": outcome["status"],
            "created": outcome["status"] == "draft_created",
            "error": outcome.get("error"),
            "note": outcome.get("note"),
            "draftId": outcome.get("draftId"),
            "employeeName": msg["employeeName"],
            "managerName": msg.get("managerName"),
            # kept for older callers that read r["detail"]["error"]
            "detail": {"success": outcome["status"] != "failed", "error": outcome.get("error")},
        }

    def _create_draft_for(self, msg: dict) -> dict:
        mtype = msg["messageType"]
        if mtype == "employee_email":
            return self._create_email_draft(msg)
        if mtype in ("manager_slack", "bienvenidas_slack"):
            return self._stage_slack_draft(msg)
        return {"status": "failed", "error": f"Tipo de mensaje desconocido: {mtype}"}

    def _create_email_draft(self, msg: dict) -> dict:
        if self._google and not self._google.gmail:
            try:
                self._google.start()  # e.g. token refresh failed at boot
            except Exception:
                pass
        if not self._google or not self._google.gmail:
            return {"status": "failed", "error": (
                "Gmail no está conectado (falta o expiró google_token.json — "
                "corre 'python setup_google_auth.py')."
            )}

        to_email = msg.get("employeeEmail") or ""
        if not to_email:
            # BambooHR's workEmail is empty for a candidate who hasn't started
            # yet — find their personal address in Junior's own Gmail.
            to_email = self._google.find_personal_email(msg["employeeName"]) or ""
        if not to_email:
            return {"status": "failed", "error": (
                "Sin email en BambooHR y no encontré su correo personal en Gmail — "
                "agrégalo y vuelve a intentar."
            )}

        cc = msg.get("ccEmail") or (self.NON_SPANISH_CC if (msg.get("language") or "es") != "es" else None)
        outcome = self._google.create_draft(
            to=to_email,
            subject="Tu aventura en Koronet está por comenzar 🚀",
            body_text=msg["messageContent"],
            image_path=msg.get("imagePath"),
            cc=cc,
        )
        if not outcome.get("success") or not outcome.get("draftId"):
            return {"status": "failed", "error": outcome.get("error") or "Gmail no confirmó el borrador"}
        return {
            "status": "draft_created", "draftId": outcome["draftId"], "to": to_email, "cc": cc,
            "note": f"Borrador creado en Gmail para {to_email}" + (f" (CC {cc})" if cc else ""),
        }

    def _stage_slack_draft(self, msg: dict) -> dict:
        """
        Verifies the Slack destination with the bot token and leaves the message
        as 'ready_for_slack_draft'. It does NOT create the draft — no public Slack
        API lets a bot do that — and the result says so explicitly.
        """
        slack_svc = self._kernel.get_service("slack_service")
        if not slack_svc or not slack_svc.client:
            return {"status": "failed", "error": "Slack no está conectado (revisa SLACK_BOT_TOKEN)."}

        if msg["messageType"] == "manager_slack":
            manager = (msg.get("managerName") or "").strip()
            if not manager:
                return {"status": "failed", "error": "El ingreso no tiene líder asignado en BambooHR."}
            target = slack_svc.resolve_user_id(manager)  # raises on token/scope errors
            if not target:
                return {"status": "failed", "error": f"No encontré a {manager} en Slack."}
            where = f"el DM de {manager}"
        else:
            target = os.getenv("BIENVENIDAS_CHANNEL_ID", "CDTF8BJP4")
            where = f"#{os.getenv('BIENVENIDAS_CHANNEL', 'koronet')}"

        return {
            "status": "ready_for_slack_draft", "slackTarget": target,
            "note": (
                f"Aprobado. Creando el borrador en {where} (tarda ~1 min); "
                f"te aviso por DM cuando esté, o si falla."
            ),
        }

    def notify_junior(self, text: str) -> bool:
        junior_id = os.getenv("JUNIOR_SLACK_ID", "")
        slack_svc = self._kernel.get_service("slack_service")
        if not junior_id or not slack_svc:
            return False
        return bool(slack_svc.send_notification(junior_id, text).get("sent"))

    def kick_slack_drafts(self, results: list) -> None:
        """Approved Slack messages → real drafts via headless Claude, in the background."""
        if any(r.get("status") == "ready_for_slack_draft" for r in results):
            from services.slack_draft_worker import kick
            kick(notify=self.notify_junior)

    def _refresh_batch_status(self, batch_id) -> None:
        """A hire's batch is just a summary of its 3 messages' own states."""
        if not batch_id:
            return
        conn = get_db()
        try:
            statuses = [r[0] for r in conn.execute(
                "SELECT status FROM messages WHERE batchId=?", (batch_id,)
            ).fetchall()]
            if not statuses:
                return
            if "pending_approval" in statuses:
                new = "pending_approval"
            elif all(s in ("rejected", "skipped") for s in statuses):
                new = "rejected" if "rejected" in statuses else "skipped"
            else:
                new = "approved"
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE batches SET status=?, "
                "approvedAt=CASE WHEN ?='approved' THEN COALESCE(approvedAt, ?) ELSE approvedAt END, "
                "rejectedAt=CASE WHEN ?='rejected' THEN COALESCE(rejectedAt, ?) ELSE rejectedAt END "
                "WHERE id=?",
                (new, new, now, new, now, batch_id),
            )
            conn.commit()
        finally:
            conn.close()

    def execute_batch(self, batch_id: int, notify: bool = True) -> dict:
        """
        (Re)prepares every approved-but-unprepared or failed message of a batch.
        Used by the hourly retry; normal approvals go through approve_message.
        """
        conn = get_db()
        try:
            ids = [r["id"] for r in conn.execute(
                # 'failed' too: retries a draft that failed before (e.g. Gmail
                # was disconnected) instead of leaving it dead.
                "SELECT id FROM messages WHERE batchId=? AND (status='approved' "
                "OR (status='failed' AND employeeHireDate >= ?))",
                (batch_id, datetime.now().strftime("%Y-%m-%d")),
            ).fetchall()]
        finally:
            conn.close()
        results = [self.prepare_message_draft(i) for i in ids]
        self._refresh_batch_status(batch_id)
        if notify and results:
            self._notify_execution_result(batch_id, results)
        prepared = sum(1 for r in results if r["status"] == "draft_created")
        return {"executed": True, "prepared": prepared, "total": len(results), "results": results}

    def _notify_execution_result(self, batch_id: int, results: list) -> None:
        """
        DMs Junior the outcome of every approved message right away — an
        approval must never end silently with nothing created. Failures carry
        the exact reason; Slack ones say plainly that the draft doesn't exist yet.
        """
        junior_id = os.getenv("JUNIOR_SLACK_ID", "")
        slack_svc = self._kernel.get_service("slack_service")
        if not results or not junior_id or not slack_svc:
            return
        emp_name = results[0].get("employeeName", "")
        lines = [f"🐼 *Aprobaste mensajes de {emp_name}.* Esto es lo que quedó:"]
        for r in results:
            label = r.get("label") or self.MESSAGE_LABELS.get(r["type"], r["type"])
            if r["type"] == "manager_slack" and r.get("managerName"):
                label += f" ({r['managerName']})"
            if r["status"] == "draft_created":
                lines.append(f"✅ {label}: borrador creado en Gmail — ve y envíalo.")
            elif r["status"] == "ready_for_slack_draft":
                lines.append(f"⏳ {label}: aprobado — creando el borrador en Slack, te aviso apenas esté.")
            elif r["status"] == "failed":
                lines.append(f"❌ {label}: NO se creó — {r.get('error') or 'error desconocido'}")
        if any(r["status"] == "failed" for r in results):
            lines.append("\nLos que fallaron se reintentan automáticamente cada hora, o con 🔁 en el dashboard.")
        try:
            res = slack_svc.send_notification(junior_id, "\n".join(lines))
            if res and not res.get("sent", True):
                print(f"[Agent] Could not notify execution result: {res.get('error')}")
        except Exception as e:
            print(f"[Agent] Could not notify execution result: {e}")

    # ── OPERATIONS CENTER ────────────────────────────────────────

    def ops_center(self) -> dict:
        """
        Returns complete system state in a single call.
        Powers the Operations Center dashboard — zero-log observability.
        External checks run in parallel with a shared 12-second wall-clock limit.
        """
        from core.incident import get_active_incidents
        from skills.bamboo import _bamboo_get
        from groq import Groq
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout

        timestamp = datetime.now().isoformat()
        kernel_health = self._kernel.health()
        slack_svc = self._kernel.get_service("slack_service")

        def check_bamboo():
            # Use the BambooService cached state — avoids a slow full-directory API call
            bamboo_svc = self._kernel.get_service("bamboo_service")
            if bamboo_svc:
                h = bamboo_svc.health()
                return {
                    "connected": h["status"] == "running",
                    "employeeCount": h.get("employeeCount", 0),
                    "error": None,
                }
            return {"connected": False, "employeeCount": 0, "error": "bamboo_service not registered"}

        def check_slack():
            result = {"connected": False, "scopes": {}, "workspace": None, "botName": None, "error": None}
            if slack_svc and slack_svc.client:
                try:
                    result["connected"] = True
                    result["workspace"] = slack_svc._workspace
                    result["botName"] = slack_svc._bot_name
                    result["messagesSent"] = slack_svc._messages_sent
                    result["scopes"] = slack_svc.audit_scopes()
                except Exception as e:
                    result["error"] = str(e)[:300]
            return result

        def check_groq():
            # Live Groq ping takes 10-15s — too slow for an ops dashboard.
            # Instead: verify key is set, then check if agent has successfully
            # generated content recently (decisions table).
            key = os.getenv("GROQ_API_KEY", "")
            if not key:
                return {"connected": False, "model": "llama-3.3-70b-versatile", "error": "GROQ_API_KEY not set"}
            try:
                c = get_db()
                row = c.execute(
                    "SELECT id FROM decision_history ORDER BY createdAt DESC LIMIT 1"
                ).fetchone()
                c.close()
                return {
                    "connected": True,
                    "model": "llama-3.3-70b-versatile",
                    "lastDecisionId": row["id"] if row else None,
                    "error": None,
                }
            except Exception as e:
                return {"connected": bool(key), "model": "llama-3.3-70b-versatile", "error": str(e)[:200]}

        bamboo = {"connected": False, "employeeCount": 0, "error": "timeout"}
        slack = {"connected": False, "scopes": {}, "workspace": None, "botName": None, "error": "timeout"}
        groq_status = {"connected": False, "model": "llama-3.3-70b-versatile", "error": "timeout"}

        pool = ThreadPoolExecutor(max_workers=3)
        try:
            futs = {
                pool.submit(check_bamboo): "bamboo",
                pool.submit(check_slack):  "slack",
                pool.submit(check_groq):   "groq",
            }
            try:
                for fut in as_completed(futs, timeout=10):
                    key = futs[fut]
                    try:
                        r = fut.result()
                        if key == "bamboo": bamboo = r
                        elif key == "slack": slack = r
                        elif key == "groq": groq_status = r
                    except Exception as e:
                        if key == "bamboo": bamboo["error"] = str(e)[:200]
                        elif key == "slack": slack["error"] = str(e)[:200]
                        elif key == "groq": groq_status["error"] = str(e)[:200]
            except FuturesTimeout:
                pass  # services that timed out keep their "timeout" error default
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        # DB snapshot
        conn = get_db()
        try:
            last_run = conn.execute("SELECT * FROM runs ORDER BY startedAt DESC LIMIT 1").fetchone()
            next_hire = conn.execute(
                "SELECT employeeName, employeeHireDate FROM messages "
                "WHERE status='pending_approval' ORDER BY employeeHireDate ASC LIMIT 1"
            ).fetchone()
            pending_batches = conn.execute("SELECT COUNT(*) FROM batches WHERE status='pending_approval'").fetchone()[0]
            pending_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE status='pending_approval'").fetchone()[0]
            total_sent = conn.execute("SELECT COUNT(*) FROM messages WHERE status IN ('sent','delivered')").fetchone()[0]
            total_failed = conn.execute("SELECT COUNT(*) FROM messages WHERE status='failed'").fetchone()[0]
        finally:
            conn.close()

        active_incidents = get_active_incidents(limit=10)

        # System health grade
        issues = []
        if not bamboo["connected"]: issues.append("BambooHR desconectado")
        if not slack["connected"]: issues.append("Slack desconectado")
        if not groq_status["connected"]: issues.append("Groq AI no disponible")
        critical_scopes = slack.get("scopes", {}).get("criticalMissing", [])
        if critical_scopes: issues.append(f"Scopes críticos faltantes: {critical_scopes}")
        if active_incidents: issues.append(f"{len(active_incidents)} incidente(s) activo(s)")

        health = "green" if not issues else ("yellow" if len(issues) == 1 else "red")

        return {
            "timestamp": timestamp,
            "systemHealth": health,
            "issues": issues,
            "kernel": kernel_health["kernel"],
            "services": kernel_health.get("services", {}),
            "bamboo": bamboo,
            "slack": slack,
            "groq": groq_status,
            "lastRun": dict(last_run) if last_run else None,
            "nextOnboarding": dict(next_hire) if next_hire else None,
            "pendingBatches": pending_batches,
            "pendingMessages": pending_msgs,
            "totalSent": total_sent,
            "totalFailed": total_failed,
            "activeIncidents": active_incidents,
            "lastHeartbeat": timestamp,
        }

    def production_audit(self) -> dict:
        """
        Scores Panda Bear across 8 production-readiness dimensions.
        Honest. No inflated scores. What's missing is what's missing.
        """
        ops = self.ops_center()

        bamboo_ok = ops["bamboo"]["connected"]
        slack_ok  = ops["slack"]["connected"]
        groq_ok   = ops["groq"]["connected"]
        missing_critical = ops["slack"].get("scopes", {}).get("criticalMissing", [])
        missing_all = list(ops["slack"].get("scopes", {}).get("missing", {}).keys())
        active_incidents = len(ops["activeIncidents"])
        pending_batches = ops["pendingBatches"]
        failed_msgs = ops["totalFailed"]

        dims = {}

        # 1. Availability — are external services reachable?
        avail_score = int((sum([bamboo_ok, slack_ok, groq_ok]) / 3) * 100)
        dims["availability"] = {
            "score": avail_score,
            "label": "Disponibilidad de servicios externos",
            "notes": f"{sum([bamboo_ok, slack_ok, groq_ok])}/3 servicios conectados",
            "blockers": ([f"BambooHR desconectado"] if not bamboo_ok else []) +
                        ([f"Slack desconectado"] if not slack_ok else []) +
                        ([f"Groq AI no disponible"] if not groq_ok else []),
        }

        # 2. Reliability — does it deliver what it promises?
        rel_score = 100
        rel_blockers = []
        if missing_critical:
            rel_score -= 30
            rel_blockers.append(f"Scopes críticos de Slack faltantes: {missing_critical}")
        if missing_all and not missing_critical:
            rel_score -= 10
            rel_blockers.append(f"Scopes opcionales faltantes: {[s for s in missing_all if s not in missing_critical]}")
        if failed_msgs > 0:
            rel_score -= 10
            rel_blockers.append(f"{failed_msgs} mensaje(s) fallido(s) en historial")
        dims["reliability"] = {
            "score": max(0, rel_score),
            "label": "Confiabilidad de entrega de mensajes",
            "notes": "Entrega de mensajes y estabilidad de integración",
            "blockers": rel_blockers,
        }

        # 3. Recovery — does it self-heal?
        dims["recovery"] = {
            "score": 70,
            "label": "Capacidad de auto-recuperación",
            "notes": "Kernel self-healing activo. Clasificación de incidentes implementada.",
            "blockers": [
                "Sin cola de mensajes (mensajes perdidos si Slack falla al enviar)",
                "Sin reintento automático por mensaje individual fallido",
                "Sin email como canal alternativo de entrega",
            ],
        }

        # 4. Observability — can you see what's happening?
        obs_blockers = []
        if active_incidents > 0: obs_blockers.append(f"{active_incidents} incidente(s) sin resolver")
        obs_blockers += [
            "Sin alertas externas (email/Slack a HR cuando hay incidentes críticos)",
            "Sin métricas de latencia por operación",
            "Sin dashboard de uptime externo",
        ]
        dims["observability"] = {
            "score": 70,
            "label": "Observabilidad del sistema",
            "notes": "Decision history, incidents, ops center y execution history implementados",
            "blockers": obs_blockers,
        }

        # 5. Security — is it safe?
        sec_score = 60
        sec_blockers = []
        if os.getenv("DASHBOARD_PASSWORD", "") in ("koronet2024", ""):
            sec_score -= 15
            sec_blockers.append("Contraseña del dashboard es el valor por defecto")
        sec_blockers += [
            "Sin HTTPS — requerido en producción real",
            "Sin rate limiting en endpoints de la API",
            "POST /agent/reset sin autenticación adicional",
            "Credenciales en .env en texto plano (usar secret manager en prod)",
        ]
        dims["security"] = {
            "score": max(0, sec_score),
            "label": "Seguridad",
            "notes": "HTTP Basic Auth presente. Falta HTTPS, rate limiting y gestión de secretos.",
            "blockers": sec_blockers,
        }

        # 6. Operations — can you run it without babysitting?
        ops_blockers = [
            "Sin CI/CD automatizado (deploy manual)",
            "Sin monitoreo externo de uptime (UptimeRobot, Better Uptime, etc.)",
            "Sin rotación automática de logs",
        ]
        if not os.getenv("PORT"): ops_blockers.append("Variable PORT no configurada explícitamente")
        dims["operations"] = {
            "score": 60,
            "label": "Operación y mantenimiento",
            "notes": "Kernel gestiona el ciclo de vida de servicios. Falta infraestructura de despliegue.",
            "blockers": ops_blockers,
        }

        # 7. Fault Tolerance — what happens when things go wrong?
        ft_score = 55
        ft_blockers = []
        if missing_critical:
            ft_score -= 20
            ft_blockers.append(f"Scopes críticos faltantes bloquean entrega: {missing_critical}")
        ft_blockers += [
            "SQLite no soporta concurrencia alta — usar PostgreSQL en producción real",
            "Sin cola de mensajes (RabbitMQ, Redis, SQS) para garantizar entrega",
            "Sin circuit breaker para llamadas a BambooHR y Slack",
        ]
        dims["fault_tolerance"] = {
            "score": max(0, ft_score),
            "label": "Tolerancia a fallos",
            "notes": "WAL mode en SQLite, clasificación de errores y recovery parcial implementados.",
            "blockers": ft_blockers,
        }

        # 8. HR Experience — does HR love using it?
        hr_score = 88
        hr_blockers = []
        if pending_batches > 0:
            hr_blockers.append(f"{pending_batches} batch(es) esperando aprobación ahora mismo")
        hr_blockers.append("Sin notificación proactiva a HR cuando hay nuevos batches pendientes")
        dims["hr_experience"] = {
            "score": hr_score,
            "label": "Experiencia del equipo de HR",
            "notes": "Dashboard completo, conversación con agente, flujo de aprobación funcional.",
            "blockers": hr_blockers,
        }

        overall = int(sum(d["score"] for d in dims.values()) / len(dims))
        all_blockers = [b for d in dims.values() for b in d["blockers"]]
        critical_blockers = (
            [f"[SEGURIDAD] Sin HTTPS"] +
            ([f"[SLACK] Scopes críticos faltantes: {missing_critical}"] if missing_critical else []) +
            (["[CONTRASEÑA] Cambiar DASHBOARD_PASSWORD en .env"] if os.getenv("DASHBOARD_PASSWORD", "") == "koronet2024" else [])
        )

        return {
            "timestamp": datetime.now().isoformat(),
            "overallScore": overall,
            "productionReady": overall >= 80 and not critical_blockers,
            "dimensions": dims,
            "criticalBlockers": critical_blockers,
            "totalBlockers": len(all_blockers),
            "recommendation": (
                "Listo para uso interno con supervisión activa." if overall >= 75
                else "Requiere trabajo adicional antes de exposición externa."
            ),
            "dependsOnFounder": [
                f"Agregar scopes de Slack en api.slack.com: {missing_all}" if missing_all else None,
                "Configurar HTTPS en el servidor de producción (Render, Railway, etc.)",
                "Cambiar DASHBOARD_PASSWORD en .env por una contraseña segura",
                "Opcional: contratar UptimeRobot o Better Uptime para monitoreo externo",
            ],
        }

    # ── SKILLS ──────────────────────────────────────────────────

    def invoke_skill(self, skill_name: str, **kwargs) -> dict:
        """Invokes a registered skill. Records the invocation."""
        skill = self._skills.get(skill_name)
        if not skill:
            return {"success": False, "error": f"Skill '{skill_name}' not found in registry"}
        return skill.execute(**kwargs)

    def list_skills(self) -> list:
        return [
            {"name": s.name, "description": s.description, "category": s.category}
            for s in self._skills.values()
        ]

    # ── STATS ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        conn = get_db()
        try:
            total_sent = conn.execute("SELECT COUNT(*) FROM messages WHERE status='sent'").fetchone()[0]
            total_failed = conn.execute("SELECT COUNT(*) FROM messages WHERE status='failed'").fetchone()[0]
            total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            total_emp = conn.execute(
                "SELECT COUNT(DISTINCT employeeId) FROM messages "
                "WHERE employeeId NOT LIKE '%_mgr' AND employeeId NOT LIKE '%_bvd'"
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT * FROM messages ORDER BY createdAt DESC LIMIT 10"
            ).fetchall()
            return {
                "totalMessagesSent": total_sent,
                "totalMessagesFailed": total_failed,
                "totalRuns": total_runs,
                "totalEmployeesTracked": total_emp,
                "recentMessages": [dict(r) for r in recent],
            }
        finally:
            conn.close()
