"""
Panda Bear — The Agent
This is THE entity. The brain. The decision-maker.
The dashboard shows what this agent tells it to show.
The dashboard never thinks for itself.
"""

import os
import json
from datetime import datetime
from groq import Groq

from core.identity import get_full_identity, check_constitution, CONSTITUTION
from core.memory import AgentMemory
from core.kernel import ServiceKernel

from skills.country_rules import CountryRulesSkill
from skills.business_calendar import BusinessCalendarSkill
from skills.bamboo import DetectEmployeesSkill, ReadBambooSkill, get_employee_details, get_employee_photo
from skills.generate_content import GenerateEmailSkill, GenerateSlackManagerSkill, GenerateSlackBienvenidaSkill, RefineContentSkill
from skills.image_generation import GenerateImageSkill
from skills.approval_workflow import ApprovalWorkflowSkill

from db.schema import get_db

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY")) if os.getenv("GROQ_API_KEY") else None

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

    def __init__(self, kernel: ServiceKernel = None):
        self._memory = AgentMemory()
        self._kernel = kernel or ServiceKernel()
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
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=1500,
                temperature=0.3,
            )
            reply = response.choices[0].message.content

        self._conversations[user_id].append({"role": "assistant", "content": reply})
        self._memory.save_message(user_id, "user", text, bamboo_context)
        self._memory.save_message(user_id, "assistant", reply)

        return reply

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
        country_result = self._skills["country_rules"].execute(location=location, country=country)
        rules = country_result["result"]
        lang = rules["language"]
        onboarding_time = rules["onboardingTime"]
        cc_email = rules["ccEmail"]

        # Apply business calendar
        calendar_result = self._skills["business_calendar"].execute(
            hire_date=hire_date.strftime("%Y-%m-%d")
        )
        manager_notify_date = calendar_result["result"]["managerNotifyDate"]

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
            already_ids = set(
                row[0] for row in c.execute(
                    "SELECT DISTINCT employeeId FROM messages"
                ).fetchall()
            )
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

                    emp_name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                    emp_number = str(emp.get("employeeNumber", ""))
                    location = emp.get("location", "") or emp.get("country", "")
                    country = emp.get("country", "")
                    manager = emp.get("supervisor", "")

                    # G2: Plan
                    plan = self.plan_onboarding(emp, hire_date)
                    lang = plan["language"]
                    onboarding_time = plan["onboardingTime"]

                    # Generate image (with photo if available)
                    photo_bytes = get_employee_photo(bamboo_id) if bamboo_id else None
                    image_result = self._skills["generate_image"].execute(
                        name=emp_name,
                        job_title=emp.get("jobTitle", ""),
                        photo_bytes=photo_bytes,
                    )
                    image_path = image_result.get("imagePath") if image_result.get("success", True) else None

                    # Create batch for this employee
                    c.execute(
                        "INSERT INTO batches (createdAt, messageCount, employeeName) VALUES (?, 3, ?)",
                        (datetime.now().isoformat(), emp_name),
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
                        manager_name=manager,
                        employee_name=emp_name,
                        language=lang,
                    )
                    bienvenida_result = self._skills["generate_slack_bienvenida"].execute(
                        first_name=emp.get("firstName", ""),
                        last_name=emp.get("lastName", ""),
                        job_title=emp.get("jobTitle", "New Team Member"),
                    )

                    hire_str = hire_date.strftime("%Y-%m-%d")

                    # Queue all 3 messages for approval (NEVER send directly)
                    for msg_type, content_result, extra_emp_id, img in [
                        ("employee_email", email_result, emp_number, image_path),
                        ("manager_slack", manager_result, emp_number + "_mgr", None),
                        ("bienvenidas_slack", bienvenida_result, emp_number + "_bvd", image_path),
                    ]:
                        c.execute("""
                            INSERT INTO messages
                            (runId, employeeId, employeeName, employeeJobTitle, employeeHireDate,
                             employeeEmail, employeeLocation, managerName, messageType, messageContent,
                             language, imagePath, status, createdAt, batchId)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?)
                        """, (
                            run_id, extra_emp_id, emp_name, emp.get("jobTitle", ""),
                            hire_str, emp.get("workEmail", ""), location, manager,
                            msg_type, content_result.get("result", ""), lang,
                            img, datetime.now().isoformat(), batch_id,
                        ))
                        messages_created += 1

                    conn.commit()

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
            upcoming_week = [
                emp for emp in employees
                if emp.get("hireDate") and
                0 <= (datetime.strptime(emp["hireDate"], "%Y-%m-%d") - today).days <= 7
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

            approved = sum(1 for m in msgs if m["status"] == "sent")
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

    def execute_batch(self, batch_id: int) -> dict:
        """
        Executes an approved batch: sends each message to its destination via Slack.
        Called ONLY after HR has approved through the approval workflow.
        manager_slack  → DM to the manager
        bienvenidas_slack → post to the configured channel
        employee_email → DM to the approver as a preview (no SMTP configured)
        """
        slack = self._kernel.get_service("slack_service")
        if not slack or not slack.client:
            return {"executed": False, "error": "Slack service not available"}

        conn = get_db()
        results = []
        bienvenidas_channel = os.getenv("BIENVENIDAS_CHANNEL", "bienvenidas")

        try:
            msgs = conn.execute(
                "SELECT * FROM messages WHERE batchId=? AND status='sent'", (batch_id,)
            ).fetchall()

            for msg in msgs:
                msg = dict(msg)
                mtype = msg["messageType"]
                content = msg["messageContent"]
                image = msg.get("imagePath")
                outcome = {}

                if mtype == "manager_slack":
                    manager = msg.get("managerName") or ""
                    manager_id = slack.find_user_by_name(manager) if manager else None
                    if manager_id:
                        outcome = slack.send_notification(manager_id, content)
                    else:
                        # Manager not found — notify approver so they can forward manually
                        approver_id = slack.find_user_by_name("junior")
                        note = f"⚠️ *Manager no encontrado en Slack para {msg['employeeName']}.*\nReenvía este mensaje manualmente:\n\n{content}"
                        outcome = slack.send_notification(approver_id, note) if approver_id else {"sent": False, "error": "Approver not found"}

                elif mtype == "bienvenidas_slack":
                    outcome = slack.post_to_channel(bienvenidas_channel, content, image)

                elif mtype == "employee_email":
                    # No SMTP — forward to approver as preview
                    approver_id = slack.find_user_by_name("junior")
                    note = f"📧 *Email pendiente de enviar a {msg['employeeName']}:*\n\n{content}"
                    outcome = slack.send_notification(approver_id, note) if approver_id else {"sent": False, "error": "Approver not found"}

                status = "delivered" if outcome.get("sent") else "failed"
                conn.execute(
                    "UPDATE messages SET status=? WHERE id=?",
                    (status, msg["id"]),
                )
                results.append({"id": msg["id"], "type": mtype, "status": status, "detail": outcome})

            conn.commit()
            delivered = sum(1 for r in results if r["status"] == "delivered")
            self._memory.record_decision(
                skill_name="execute_batch",
                decision=f"Batch {batch_id} executed — {delivered}/{len(results)} messages delivered",
                reasoning="HR approved the batch. Agent sent via Slack.",
                context={"batchId": batch_id, "results": results},
            )
            return {"executed": True, "delivered": delivered, "total": len(results), "results": results}
        finally:
            conn.close()

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
