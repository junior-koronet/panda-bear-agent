"""
Skill: Approval Workflow
Manages the Recommend → Review → Approve → Execute lifecycle.
This is the core of the constitution: nothing irreversible happens without human approval.

Every onboarding has 3 independent messages (DM al líder, bienvenida en #koronet,
correo al empleado). Each one is approved, skipped or rejected ON ITS OWN.
Approving only moves a message to 'approved'; the agent then creates its draft
(core/agent.py::prepare_message_draft). Approving NEVER sends — see DECISIONS.md.

Every transition is a conditional UPDATE (… WHERE status IN (allowed)), so a
double click or two tabs can't approve the same message twice and create two drafts.
"""

from datetime import datetime
from skills.base import Skill
from db.schema import get_db


class ApprovalWorkflowSkill(Skill):
    name = "approval_workflow"
    description = "Manages the approve/reject/edit lifecycle for all pending messages."
    category = "governance"

    def _run(self, action: str, **kwargs) -> dict:
        handlers = {
            "approve_batch": self._approve_batch,
            "reject_batch": self._reject_batch,
            "approve_message": self._approve_message,
            "skip_message": self._skip_message,
            "reject_message": self._reject_message,
            "get_pending": self._get_pending,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}
        return handler(**kwargs)

    def _get_pending(self) -> dict:
        conn = get_db()
        try:
            batches = conn.execute(
                "SELECT * FROM batches WHERE status='pending_approval' ORDER BY createdAt DESC"
            ).fetchall()
            total_messages = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE status='pending_approval'"
            ).fetchone()[0]
            return {
                "result": [dict(b) for b in batches],
                "totalMessages": total_messages,
                "decision": f"Found {len(batches)} batch(es) pending approval with {total_messages} messages",
                "reasoning": "Retrieved all pending approvals for HR review.",
            }
        finally:
            conn.close()

    def _transition(self, message_id: int, new_status: str, allowed_from: tuple, action: str) -> dict:
        """Moves one message to new_status only if it is currently in allowed_from."""
        conn = get_db()
        try:
            msg = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not msg:
                return {"success": False, "error": f"Mensaje {message_id} no encontrado"}
            msg = dict(msg)
            placeholders = ",".join("?" for _ in allowed_from)
            extra = ", approvedAt=?" if new_status == "approved" else ""
            params = [new_status] + ([datetime.now().isoformat()] if extra else []) + [message_id, *allowed_from]
            cur = conn.execute(
                f"UPDATE messages SET status=?{extra} WHERE id=? AND status IN ({placeholders})",
                params,
            )
            conn.commit()
            if cur.rowcount == 0:
                return {
                    "success": False,
                    "currentStatus": msg["status"],
                    "batchId": msg["batchId"],
                    "error": f"El mensaje ya no está pendiente (estado actual: {msg['status']})",
                }
        finally:
            conn.close()

        if self._memory:
            self._memory.record_approval(
                message_id=message_id,
                employee_name=msg.get("employeeName") or "Unknown",
                message_type=msg.get("messageType") or "unknown",
                action=action,
                original_content=msg.get("messageContent") or "",
                final_content=msg.get("messageContent") if action == "approved" else None,
            )
        return {
            "result": {action: True},
            "batchId": msg["batchId"],
            "messageType": msg["messageType"],
            "decision": f"{action.capitalize()} message {message_id} ({msg['messageType']}) for {msg['employeeName']}",
            "reasoning": f"HR {action} this individual message.",
        }

    def _approve_message(self, message_id: int) -> dict:
        # 'approved' only — nothing has been created or sent yet. The agent
        # turns it into a draft right after; Junior does the real send himself.
        return self._transition(message_id, "approved", ("pending_approval",), "approved")

    def _skip_message(self, message_id: int) -> dict:
        return self._transition(message_id, "skipped", ("pending_approval", "failed"), "skipped")

    def _reject_message(self, message_id: int) -> dict:
        return self._transition(message_id, "rejected", ("pending_approval", "failed"), "rejected")

    def _approve_batch(self, batch_id: int) -> dict:
        """Claims every pending message of the batch. Callers then prepare each draft."""
        conn = get_db()
        try:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM messages WHERE batchId=? AND status='pending_approval'", (batch_id,)
            ).fetchall()]
        finally:
            conn.close()
        if not ids:
            return {"success": False, "error": f"No hay mensajes pendientes en el batch {batch_id}"}
        approved = [i for i in ids if self._approve_message(i).get("success", True)]
        return {
            "result": {"approved": True, "messageIds": approved},
            "decision": f"Approved {len(approved)} message(s) of batch {batch_id} — drafts will be prepared, nothing sent",
            "reasoning": "HR approved all pending messages of this hire.",
            "confidence": 1.0,
        }

    def _reject_batch(self, batch_id: int) -> dict:
        """Rejects whatever is still pending/failed. Drafts already created are left alone."""
        conn = get_db()
        try:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM messages WHERE batchId=? AND status IN ('pending_approval','failed')",
                (batch_id,),
            ).fetchall()]
        finally:
            conn.close()
        rejected = [i for i in ids if self._reject_message(i).get("success", True)]
        return {
            "result": {"rejected": True, "messageIds": rejected},
            "batchId": batch_id,
            "decision": f"Rejected {len(rejected)} message(s) of batch {batch_id} — they will not be prepared",
            "reasoning": "HR decided not to send these messages.",
        }
