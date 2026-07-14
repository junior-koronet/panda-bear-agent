"""
Panda Bear — Memory System
Persistent memory for the agent. Every important event is recorded here.
The agent consults memory before making decisions.
"""

import json
from datetime import datetime
from db.schema import get_db


class AgentMemory:
    """
    Persistent memory for Panda Bear.
    Organized by type: employee, manager, country, approval, lessons, decisions, conversations.
    """

    # ── EMPLOYEE MEMORY ─────────────────────────────────────────

    def remember_employee(self, data: dict) -> None:
        """Records or updates an employee in memory."""
        conn = get_db()
        now = datetime.now().isoformat()
        try:
            conn.execute("""
                INSERT INTO employee_memory
                (bambooId, employeeNumber, name, email, country, language,
                 hireDate, jobTitle, department, managerName, onboardingStatus,
                 batchId, imagePath, notes, createdAt, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bambooId) DO UPDATE SET
                    name=excluded.name, email=excluded.email,
                    country=excluded.country, language=excluded.language,
                    hireDate=excluded.hireDate, jobTitle=excluded.jobTitle,
                    department=excluded.department, managerName=excluded.managerName,
                    onboardingStatus=excluded.onboardingStatus,
                    batchId=coalesce(excluded.batchId, employee_memory.batchId),
                    imagePath=coalesce(excluded.imagePath, employee_memory.imagePath),
                    notes=coalesce(excluded.notes, employee_memory.notes),
                    updatedAt=excluded.updatedAt
            """, (
                data.get("bambooId"), data.get("employeeNumber"), data.get("name"),
                data.get("email"), data.get("country"), data.get("language"),
                data.get("hireDate"), data.get("jobTitle"), data.get("department"),
                data.get("managerName"), data.get("onboardingStatus", "detected"),
                data.get("batchId"), data.get("imagePath"), data.get("notes"),
                now, now,
            ))
            conn.commit()
        finally:
            conn.close()

    def recall_employee(self, bamboo_id: str) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM employee_memory WHERE bambooId=?", (bamboo_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_employee_status(self, bamboo_id: str, status: str) -> None:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE employee_memory SET onboardingStatus=?, updatedAt=? WHERE bambooId=?",
                (status, datetime.now().isoformat(), bamboo_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_employee_timeline(self, employee_name: str) -> list:
        """Returns full timeline of events for an employee."""
        conn = get_db()
        try:
            messages = conn.execute(
                "SELECT * FROM messages WHERE employeeName LIKE ? ORDER BY createdAt",
                (f"%{employee_name}%",),
            ).fetchall()
            decisions = conn.execute(
                "SELECT * FROM decision_history WHERE context LIKE ? ORDER BY createdAt",
                (f"%{employee_name}%",),
            ).fetchall()
            approvals = conn.execute(
                "SELECT * FROM approval_memory WHERE employeeName LIKE ? ORDER BY createdAt",
                (f"%{employee_name}%",),
            ).fetchall()

            timeline = []
            for m in messages:
                timeline.append({
                    "type": "message",
                    "status": m["status"],
                    "detail": m["messageType"],
                    "timestamp": m["createdAt"],
                })
            for d in decisions:
                timeline.append({
                    "type": "decision",
                    "detail": d["decision"],
                    "reasoning": d["reasoning"],
                    "timestamp": d["createdAt"],
                })
            for a in approvals:
                timeline.append({
                    "type": "approval",
                    "action": a["action"],
                    "messageType": a["messageType"],
                    "timestamp": a["createdAt"],
                })
            timeline.sort(key=lambda x: x["timestamp"])
            return timeline
        finally:
            conn.close()

    # ── MANAGER MEMORY ──────────────────────────────────────────

    def remember_manager(self, name: str, data: dict = None) -> None:
        """Creates or updates manager memory."""
        conn = get_db()
        now = datetime.now().isoformat()
        try:
            conn.execute("""
                INSERT INTO manager_memory (name, slackId, communicationStyle, preferredLanguage, messagesSent, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    slackId=coalesce(excluded.slackId, manager_memory.slackId),
                    communicationStyle=coalesce(excluded.communicationStyle, manager_memory.communicationStyle),
                    preferredLanguage=coalesce(excluded.preferredLanguage, manager_memory.preferredLanguage),
                    messagesSent=manager_memory.messagesSent + 1,
                    updatedAt=excluded.updatedAt
            """, (
                name,
                data.get("slackId") if data else None,
                data.get("communicationStyle", "professional") if data else "professional",
                data.get("preferredLanguage", "es") if data else "es",
                1,
                now,
            ))
            conn.commit()
        finally:
            conn.close()

    def recall_manager(self, name: str) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM manager_memory WHERE name=?", (name,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_manager_notes(self, name: str, notes: str) -> None:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE manager_memory SET notes=?, updatedAt=? WHERE name=?",
                (notes, datetime.now().isoformat(), name),
            )
            conn.commit()
        finally:
            conn.close()

    # ── APPROVAL MEMORY ─────────────────────────────────────────

    def record_approval(
        self,
        message_id: int,
        employee_name: str,
        message_type: str,
        action: str,
        original_content: str,
        final_content: str = None,
        hr_notes: str = None,
    ) -> None:
        """Records what HR did with a message (approved/edited/rejected/skipped)."""
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO approval_memory
                (messageId, employeeName, messageType, action, originalContent, finalContent, hrNotes, createdAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id, employee_name, message_type, action,
                original_content, final_content, hr_notes, datetime.now().isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    # ── DECISION HISTORY ────────────────────────────────────────

    def record_decision(
        self,
        skill_name: str,
        decision: str,
        reasoning: str,
        context: dict = None,
        outcome: str = None,
        confidence: float = 1.0,
    ) -> None:
        """Records every decision the agent makes with its reasoning."""
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO decision_history
                (skillName, decision, reasoning, context, outcome, confidence, createdAt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                skill_name, decision, reasoning,
                json.dumps(context) if context else None,
                outcome, confidence, datetime.now().isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    def get_recent_decisions(self, limit: int = 20) -> list:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM decision_history ORDER BY createdAt DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── LESSONS LEARNED ─────────────────────────────────────────

    def record_lesson(
        self,
        employee_name: str,
        employee_id: str,
        country: str,
        department: str,
        approved: int,
        edited: int,
        rejected: int,
        what_worked: str,
        what_failed: str,
        insights: dict = None,
    ) -> None:
        """Records what was learned from a completed onboarding."""
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO lessons_learned
                (employeeName, employeeId, country, department, messagesApproved,
                 messagesEdited, messagesRejected, whatWorked, whatFailed, insights, createdAt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                employee_name, employee_id, country, department,
                approved, edited, rejected, what_worked, what_failed,
                json.dumps(insights) if insights else None,
                datetime.now().isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    def get_lessons_for_context(self, country: str = None, department: str = None) -> list:
        """Returns lessons relevant to a given country/department."""
        conn = get_db()
        try:
            if country:
                rows = conn.execute(
                    "SELECT * FROM lessons_learned WHERE country=? ORDER BY createdAt DESC LIMIT 10",
                    (country,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lessons_learned ORDER BY createdAt DESC LIMIT 10"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── CONVERSATION MEMORY ─────────────────────────────────────

    def save_message(self, session_id: str, role: str, content: str, context: str = None) -> None:
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO conversations (sessionId, role, content, bambooContext, createdAt)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, role, content, context, datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()

    def get_conversation(self, session_id: str, limit: int = 20) -> list:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE sessionId=? ORDER BY createdAt DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    # ── SUMMARY ─────────────────────────────────────────────────

    def get_memory_summary(self) -> dict:
        """Returns a summary of what the agent remembers."""
        conn = get_db()
        try:
            return {
                "employees": conn.execute("SELECT COUNT(*) FROM employee_memory").fetchone()[0],
                "managers": conn.execute("SELECT COUNT(*) FROM manager_memory").fetchone()[0],
                "decisions": conn.execute("SELECT COUNT(*) FROM decision_history").fetchone()[0],
                "lessons": conn.execute("SELECT COUNT(*) FROM lessons_learned").fetchone()[0],
                "approvals": conn.execute("SELECT COUNT(*) FROM approval_memory").fetchone()[0],
                "conversations": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            }
        finally:
            conn.close()
