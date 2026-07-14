"""
Panda Bear — API Routes
The dashboard calls these. The routes call the agent. Never the other way around.
The agent is the source of truth. The dashboard is just a window into the agent.
"""

import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from pydantic import BaseModel
import secrets

from db.schema import get_db
from datetime import datetime

security = HTTPBasic()
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "junior@koronet.com")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "koronet2024")

router = APIRouter(prefix="/api")
_agent = None  # Injected at startup


def set_agent(agent):
    global _agent
    _agent = agent


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    correct_pass = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── AGENT IDENTITY ──────────────────────────────────────────────

@router.get("/agent/identity")
def get_identity():
    """Who is Panda Bear? Everything about the agent."""
    return _agent.identify()


@router.get("/agent/skills")
def get_skills():
    """List all agent skills."""
    return _agent.list_skills()


# ── DASHBOARD CORE ──────────────────────────────────────────────

@router.get("/agent/stats")
def get_stats():
    return _agent.get_stats()


@router.get("/agent/morning-brief")
def morning_brief():
    """The agent's morning summary. The Dashboard's home view."""
    return _agent.morning_brief()


@router.get("/agent/integrations/status")
def get_integrations():
    from skills.bamboo import _bamboo_get
    from groq import Groq
    import os

    bamboo_ok, slack_ok, groq_ok = False, False, False
    bamboo_count = 0
    try:
        data = _bamboo_get("employees/directory")
        if data:
            bamboo_ok = True
            bamboo_count = len(data.get("employees", []))
    except Exception:
        pass
    try:
        svc = _agent._kernel.get_service("slack_service")
        if svc and svc.client:
            svc.client.auth_test()
            slack_ok = True
    except Exception:
        pass
    try:
        groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
        groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        groq_ok = True
    except Exception:
        pass

    return {
        "bamboohr": {"connected": bamboo_ok, "detail": f"{bamboo_count} empleados" if bamboo_ok else "Error"},
        "slack": {"connected": slack_ok, "detail": "Koronet" if slack_ok else "Error"},
        "anthropic": {"connected": groq_ok, "detail": "Groq AI" if groq_ok else "Error"},
    }


# ── SYNC ────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    dryRun: bool = False
    lookbackDays: int = 60


@router.post("/agent/sync")
def sync(body: SyncRequest):
    """Run the detection and preparation cycle. Nothing is sent — only queued for approval."""
    return _agent.run_sync(dry_run=body.dryRun, lookback_days=body.lookbackDays)


# ── MESSAGES ────────────────────────────────────────────────────

@router.get("/agent/messages")
def get_messages():
    conn = get_db()
    try:
        msgs = conn.execute("SELECT * FROM messages ORDER BY createdAt DESC").fetchall()
        return [dict(m) for m in msgs]
    finally:
        conn.close()


@router.get("/agent/runs")
def get_runs():
    conn = get_db()
    try:
        runs = conn.execute("SELECT * FROM runs ORDER BY startedAt DESC").fetchall()
        return [dict(r) for r in runs]
    finally:
        conn.close()


@router.get("/agent/recent-hires")
def recent_hires():
    from skills.bamboo import _bamboo_report
    from skills.country_rules import CountryRulesSkill
    from skills.business_calendar import BusinessCalendarSkill

    employees = _bamboo_report()
    today = datetime.today()
    conn = get_db()
    result = []
    try:
        for emp in employees:
            hire_str = emp.get("hireDate", "")
            if not hire_str:
                continue
            try:
                hire_date = datetime.strptime(hire_str, "%Y-%m-%d")
                diff = (hire_date - today).days
                if 0 < diff <= 60:
                    emp_id = str(emp.get("employeeNumber", ""))
                    existing = conn.execute(
                        "SELECT id FROM messages WHERE employeeId=?", (emp_id,)
                    ).fetchone()
                    lang_info = CountryRulesSkill().execute(
                        location=emp.get("location", ""), country=emp.get("country", "")
                    )
                    cal_info = BusinessCalendarSkill().execute(hire_date=hire_str)
                    result.append({
                        "firstName": emp.get("firstName", ""),
                        "lastName": emp.get("lastName", ""),
                        "jobTitle": emp.get("jobTitle", ""),
                        "department": emp.get("department", ""),
                        "hireDate": hire_str,
                        "supervisor": emp.get("supervisor", ""),
                        "workEmail": emp.get("workEmail", ""),
                        "location": emp.get("location", ""),
                        "country": emp.get("country", ""),
                        "language": lang_info["result"]["language"],
                        "managerMessageDate": cal_info["result"]["managerNotifyDate"],
                        "daysUntilHire": diff,
                        "alreadyProcessed": existing is not None,
                    })
            except ValueError:
                continue
        return result
    finally:
        conn.close()


# ── BATCHES ─────────────────────────────────────────────────────

@router.get("/agent/batches")
def get_batches():
    conn = get_db()
    try:
        batches = conn.execute(
            "SELECT * FROM batches WHERE status='pending_approval' ORDER BY createdAt DESC"
        ).fetchall()
        return [dict(b) for b in batches]
    finally:
        conn.close()


@router.get("/agent/batches/{batch_id}")
def get_batch(batch_id: int):
    conn = get_db()
    try:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        msgs = conn.execute("SELECT * FROM messages WHERE batchId=?", (batch_id,)).fetchall()
        return {**dict(batch), "messages": [dict(m) for m in msgs]}
    finally:
        conn.close()


@router.post("/agent/batches/{batch_id}/approve")
def approve_batch(batch_id: int):
    result = _agent.invoke_skill("approval_workflow", action="approve_batch", batch_id=batch_id)
    if result.get("result", {}).get("approved"):
        exec_result = _agent.execute_batch(batch_id)
        result["execution"] = exec_result
    return result


@router.post("/agent/batches/{batch_id}/reject")
def reject_batch(batch_id: int):
    result = _agent.invoke_skill("approval_workflow", action="reject_batch", batch_id=batch_id)
    _agent.learn_from_batch(batch_id)
    return result


# ── INDIVIDUAL MESSAGE ACTIONS ───────────────────────────────────

class EditRequest(BaseModel):
    content: Optional[str] = None
    feedback: Optional[str] = None


@router.post("/agent/messages/{msg_id}/edit")
def edit_message(msg_id: int, body: EditRequest):
    conn = get_db()
    try:
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        if body.content:
            new_content = body.content
            reasoning = "HR manually edited the message"
        elif body.feedback:
            result = _agent.invoke_skill(
                "refine_content",
                original_content=msg["messageContent"],
                feedback=body.feedback,
                employee_name=msg["employeeName"],
            )
            new_content = result.get("result", msg["messageContent"])
            reasoning = f"AI refined based on HR feedback: '{body.feedback}'"
        else:
            raise HTTPException(status_code=400, detail="Provide content or feedback")

        conn.execute("UPDATE messages SET messageContent=? WHERE id=?", (new_content, msg_id))
        conn.commit()

        _agent._memory.record_approval(
            message_id=msg_id,
            employee_name=msg["employeeName"],
            message_type=msg["messageType"],
            action="edited",
            original_content=msg["messageContent"],
            final_content=new_content,
            hr_notes=body.feedback,
        )
        _agent._memory.record_decision(
            skill_name="edit_message",
            decision=f"Message {msg_id} for {msg['employeeName']} was edited",
            reasoning=reasoning,
            context={"messageId": msg_id, "feedback": body.feedback},
        )
        return {"updated": True, "newContent": new_content}
    finally:
        conn.close()


@router.post("/agent/messages/{msg_id}/skip")
def skip_message(msg_id: int):
    return _agent.invoke_skill("approval_workflow", action="skip_message", message_id=msg_id)


@router.post("/agent/messages/{msg_id}/resend")
def resend_message(msg_id: int):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE messages SET status='sent', sentAt=? WHERE id=?",
            (datetime.now().isoformat(), msg_id),
        )
        conn.commit()
        return {"resent": True}
    finally:
        conn.close()


@router.post("/agent/messages/{msg_id}/test-dm")
def test_dm(msg_id: int):
    conn = get_db()
    try:
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")

        slack_svc = _agent._kernel.get_service("slack_service")
        if not slack_svc or not slack_svc.client:
            return {"delivered": False, "error": "Slack not available"}

        juni_id = slack_svc.find_user_by_name("junior")
        if not juni_id:
            return {"delivered": False, "error": "No se encontró a Juni en Slack"}

        text = f"🔔 *Preview [{msg['messageType']}] para {msg['employeeName']}:*\n\n{msg['messageContent']}"
        img_path = msg["imagePath"]

        if img_path and os.path.exists(img_path):
            slack_svc.client.files_upload_v2(
                channel=slack_svc.client.conversations_open(users=[juni_id])["channel"]["id"],
                file=img_path,
                initial_comment=text,
            )
        else:
            slack_svc.send_notification(juni_id, text)

        return {"delivered": True}
    except Exception as e:
        return {"delivered": False, "error": str(e)}
    finally:
        conn.close()


# ── CONVERSATION ─────────────────────────────────────────────────

class ConversationRequest(BaseModel):
    message: str
    sessionId: str = "default"


@router.post("/agent/conversation")
def conversation(body: ConversationRequest):
    """Talk to Panda Bear. It thinks and responds using BambooHR context."""
    reply = _agent.think(user_id=body.sessionId, text=body.message)
    return {"reply": reply, "agent": "Panda Bear"}


@router.get("/agent/conversation/{session_id}")
def get_conversation(session_id: str):
    return _agent._memory.get_conversation(session_id)


# ── MEMORY & LEARNING ────────────────────────────────────────────

@router.get("/agent/memory")
def get_memory():
    return _agent._memory.get_memory_summary()


@router.get("/agent/decisions")
def get_decisions():
    return _agent._memory.get_recent_decisions(limit=50)


@router.get("/agent/lessons")
def get_lessons():
    return _agent._memory.get_lessons_for_context()


@router.post("/agent/learn/{batch_id}")
def learn(batch_id: int):
    return _agent.learn_from_batch(batch_id)


@router.get("/agent/employee-timeline/{employee_name}")
def employee_timeline(employee_name: str):
    return _agent._memory.get_employee_timeline(employee_name)


# ── KERNEL & SERVICES ────────────────────────────────────────────

@router.get("/agent/kernel/health")
def kernel_health():
    return _agent._kernel.health()


@router.get("/agent/kernel/metrics")
def kernel_metrics():
    return _agent._kernel.metrics()


@router.post("/agent/kernel/heartbeat")
def kernel_heartbeat():
    return _agent._kernel.heartbeat()


# ── OPERATIONS CENTER ────────────────────────────────────────────

@router.get("/agent/ops-center")
def ops_center():
    """Full system state in one call. Green/yellow/red at a glance."""
    return _agent.ops_center()


@router.get("/agent/production-audit")
def production_audit():
    """Scored production readiness across 8 dimensions. No indulgence."""
    return _agent.production_audit()


# ── INCIDENTS ────────────────────────────────────────────────────

@router.get("/agent/incidents")
def get_incidents():
    """All unresolved incidents. Classified, actionable, not generic."""
    from core.incident import get_active_incidents
    return get_active_incidents(limit=50)


@router.get("/agent/incidents/history")
def get_incident_history():
    """Full incident history — resolved and active."""
    from core.incident import get_incident_history
    return get_incident_history(limit=100)


@router.post("/agent/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int):
    """Mark an incident as resolved."""
    from core.incident import resolve_incident
    resolve_incident(incident_id)
    return {"resolved": True, "incidentId": incident_id}


# ── EXECUTION HISTORY ────────────────────────────────────────────

@router.get("/agent/execution-history")
def execution_history():
    """Per-message delivery audit trail. Every send, every failure."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                m.id, m.employeeName, m.employeeJobTitle, m.employeeEmail,
                m.messageType, m.status, m.createdAt, m.sentAt,
                m.deliveryError, m.retries,
                b.id AS batchId, b.status AS batchStatus,
                r.id AS runId, r.startedAt AS runStartedAt
            FROM messages m
            LEFT JOIN batches b ON m.batchId = b.id
            LEFT JOIN runs r ON m.runId = r.id
            ORDER BY m.createdAt DESC
            LIMIT 200
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── UTILITY ─────────────────────────────────────────────────────

@router.post("/agent/reset")
def reset_db():
    conn = get_db()
    try:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM batches")
        conn.execute("DELETE FROM runs")
        conn.commit()
        return {"reset": True, "note": "Operational data cleared. Memory preserved."}
    finally:
        conn.close()
