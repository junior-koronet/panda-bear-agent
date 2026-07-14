"""
Skill: Approval Workflow
Manages the Recommend → Review → Approve → Execute lifecycle.
This is the core of the constitution: nothing irreversible happens without human approval.
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

    def _approve_batch(self, batch_id: int) -> dict:
        conn = get_db()
        try:
            msgs = conn.execute(
                "SELECT * FROM messages WHERE batchId=? AND status='pending_approval'", (batch_id,)
            ).fetchall()

            if not msgs:
                return {"success": False, "error": f"No pending messages in batch {batch_id}"}

            now = datetime.now().isoformat()
            msg_dicts = [dict(m) for m in msgs]

            for msg in msg_dicts:
                conn.execute(
                    "UPDATE messages SET status='sent', sentAt=? WHERE id=?",
                    (now, msg["id"]),
                )
            conn.execute(
                "UPDATE batches SET status='approved', approvedAt=? WHERE id=?",
                (now, batch_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Record approvals AFTER the main transaction is committed and closed
        if self._memory:
            for msg in msg_dicts:
                self._memory.record_approval(
                    message_id=msg["id"],
                    employee_name=msg["employeeName"],
                    message_type=msg["messageType"],
                    action="approved",
                    original_content=msg["messageContent"],
                    final_content=msg["messageContent"],
                )

        return {
            "result": {"approved": True, "messagesSent": len(msg_dicts)},
            "decision": f"Approved batch {batch_id} — {len(msg_dicts)} message(s) released for sending",
            "reasoning": "HR reviewed and approved all messages in this batch.",
            "confidence": 1.0,
        }

    def _reject_batch(self, batch_id: int) -> dict:
        conn = get_db()
        try:
            msgs = conn.execute("SELECT * FROM messages WHERE batchId=?", (batch_id,)).fetchall()
            msg_dicts = [dict(m) for m in msgs]
            conn.execute(
                "UPDATE batches SET status='rejected', rejectedAt=? WHERE id=?",
                (datetime.now().isoformat(), batch_id),
            )
            conn.execute("UPDATE messages SET status='rejected' WHERE batchId=?", (batch_id,))
            conn.commit()
        finally:
            conn.close()

        if self._memory:
            for msg in msg_dicts:
                self._memory.record_approval(
                    message_id=msg["id"],
                    employee_name=msg["employeeName"],
                    message_type=msg["messageType"],
                    action="rejected",
                    original_content=msg["messageContent"],
                )

        return {
            "result": {"rejected": True},
            "decision": f"Rejected batch {batch_id} — messages will not be sent",
            "reasoning": "HR decided not to send these messages.",
        }

    def _approve_message(self, message_id: int) -> dict:
        conn = get_db()
        try:
            msg = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not msg:
                return {"success": False, "error": f"Message {message_id} not found"}
            conn.execute(
                "UPDATE messages SET status='sent', sentAt=? WHERE id=?",
                (datetime.now().isoformat(), message_id),
            )
            conn.commit()

            if self._memory:
                self._memory.record_approval(
                    message_id=message_id,
                    employee_name=msg["employeeName"],
                    message_type=msg["messageType"],
                    action="approved",
                    original_content=msg["messageContent"],
                )

            return {
                "result": {"approved": True},
                "decision": f"Approved message {message_id} for {msg['employeeName']}",
                "reasoning": "HR approved this individual message.",
            }
        finally:
            conn.close()

    def _skip_message(self, message_id: int) -> dict:
        conn = get_db()
        try:
            msg = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            msg_dict = dict(msg) if msg else None
            conn.execute("UPDATE messages SET status='skipped' WHERE id=?", (message_id,))
            conn.commit()
        finally:
            conn.close()

        if self._memory and msg_dict:
            self._memory.record_approval(
                message_id=message_id,
                employee_name=msg_dict.get("employeeName", "Unknown"),
                message_type=msg_dict.get("messageType", "unknown"),
                action="skipped",
                original_content=msg_dict.get("messageContent", ""),
            )

        return {
            "result": {"skipped": True},
            "decision": f"Skipped message {message_id}",
            "reasoning": "HR chose to skip this message.",
        }
