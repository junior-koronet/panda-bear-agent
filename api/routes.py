"""
Panda Bear — API Routes
The dashboard calls these. The routes call the agent. Never the other way around.
The agent is the source of truth. The dashboard is just a window into the agent.
"""

import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.auth import verify_session_or_basic
from db.schema import get_db
from datetime import datetime
from skills import holidays_check

# Every route below requires a valid dashboard session (or Basic Auth for
# scripts) — this is the entire attack surface for HR data, message
# sending, and the personal assistant. See api/auth.py for the login flow.
router = APIRouter(prefix="/api", dependencies=[Depends(verify_session_or_basic)])
_agent = None  # Injected at startup


def set_agent(agent):
    global _agent
    _agent = agent


# ── AGENT ───────────────────────────────────────────────────────

@router.get("/agent/skills")
def get_skills():
    """List all agent skills."""
    return _agent.list_skills()


# ── DASHBOARD CORE ──────────────────────────────────────────────

@router.get("/agent/stats")
def get_stats():
    return _agent.get_stats()


# ── SYNC ────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    dryRun: bool = False
    lookbackDays: int = 60


@router.post("/agent/sync")
def sync(body: SyncRequest):
    """Run the detection and preparation cycle. Nothing is sent — only queued for approval."""
    return _agent.run_sync(dry_run=body.dryRun, lookback_days=body.lookbackDays, trigger="manual")


@router.get("/agent/sync/status")
def sync_status():
    """Schedule of the automatic sync, its next slot and the last successful sync (any trigger)."""
    svc = _agent._kernel.get_service("sync_service")
    if svc is None:
        from services.sync_service import SyncService
        return {"times": [], "timezone": None, "nextRun": None, "lastSuccess": SyncService.last_success()}
    return svc.status()


@router.get("/agent/drafts/health")
def drafts_health():
    """Last draft preflight (Gmail, Slack bot, Claude + Slack connector). Runs one if none yet."""
    from services import draft_health
    return draft_health.last_result() or _run_drafts_health()


@router.post("/agent/drafts/health")
def drafts_health_now():
    """'Comprobar ahora' — creates nothing, only checks each dependency."""
    return _run_drafts_health()


def _run_drafts_health():
    from services import draft_health
    return draft_health.run_checks(
        _agent._kernel.get_service("google_service"), _agent._kernel.get_service("slack_service"),
        notify=_agent.notify_junior,
    )


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
    """
    Every hire that still needs attention, each with its 3 messages and their
    own state: anything pending approval, failed, or approved but whose Slack
    draft hasn't been created yet — plus hires starting in the next days so
    freshly approved ones stay visible with their per-draft result.
    """
    conn = get_db()
    try:
        # Past hires drop out once they've started (old failures are moot by then);
        # anything still pending approval always shows.
        today = datetime.now().strftime("%Y-%m-%d")
        batches = conn.execute(
            """
            SELECT b.*, MIN(m.employeeHireDate) AS hireDate FROM batches b
            JOIN messages m ON m.batchId = b.id
            GROUP BY b.id
            HAVING SUM(m.status = 'pending_approval') > 0
                OR (MIN(m.employeeHireDate) >= ? AND b.status != 'rejected')
            ORDER BY MIN(m.employeeHireDate) ASC
            """,
            (today,),
        ).fetchall()
        result = []
        for b in batches:
            msgs = conn.execute(
                "SELECT * FROM messages WHERE batchId=? ORDER BY id", (b["id"],)
            ).fetchall()
            first = dict(msgs[0]) if msgs else {}
            alert = holidays_check.start_date_alert(b["hireDate"], first.get("employeeLocation"))
            result.append({**dict(b), "messages": [dict(m) for m in msgs], "startAlert": alert})
        return result
    finally:
        conn.close()


# Inbox (Mensajes programados) = what still needs Junior: pending review, plus an
# approved message whose draft is still being created or FAILED (it stays with
# its reason so he can retry). History = what is done: draft created / sent by
# Junior ("delivered" is the pre-v4 "sent"), rejected or skipped. A failure of
# a hire who already started can't be fixed anymore: it goes to history as such.
IN_FLIGHT_STATUSES = ("approved", "ready_for_slack_draft", "failed")
DONE_STATUSES = ("draft_created", "sent", "delivered", "rejected", "skipped")


@router.get("/agent/scheduled-messages")
def scheduled_messages():
    """
    "Mensajes programados": each hire with messages that still need Junior —
    pending review, or approved but whose draft is being created / failed (with
    the reason, to retry). Once the draft exists (or the message is rejected /
    skipped) it leaves this inbox for the Histórico de aprobaciones.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    inbox = "(m.status = 'pending_approval' OR (m.status IN ({}) AND m.employeeHireDate >= ?))".format(
        ",".join("?" * len(IN_FLIGHT_STATUSES)))
    conn = get_db()
    try:
        batches = conn.execute(
            f"""
            SELECT b.*, MIN(m.employeeHireDate) AS hireDate FROM batches b
            JOIN messages m ON m.batchId = b.id
            GROUP BY b.id
            HAVING SUM({inbox}) > 0
            ORDER BY MIN(m.employeeHireDate) ASC
            """,
            (*IN_FLIGHT_STATUSES, today),
        ).fetchall()
        result = []
        for b in batches:
            msgs = conn.execute(
                f"SELECT * FROM messages m WHERE m.batchId=? AND {inbox} ORDER BY m.id",
                (b["id"], *IN_FLIGHT_STATUSES, today),
            ).fetchall()
            first = dict(msgs[0]) if msgs else {}
            alert = holidays_check.start_date_alert(b["hireDate"], first.get("employeeLocation"))
            result.append({**dict(b), "messages": [dict(m) for m in msgs], "startAlert": alert})
        return result
    finally:
        conn.close()


@router.get("/agent/approvals/history")
def approvals_history():
    """
    "Histórico de aprobaciones": per hire, everything already done — messages
    whose draft was created (or Junior already sent), rejected or skipped, and
    sessions scheduled or skipped. A failed draft is NOT here (it stays in its
    inbox to retry) unless the hire already started. Nothing is approved here.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT m.*, COALESCE(b.employeeName, m.employeeName) AS hireName,
                   COALESCE(m.approvedAt,
                            (SELECT MAX(a.createdAt) FROM approval_memory a
                             WHERE a.messageId = m.id AND a.action IN ('rejected', 'skipped')),
                            m.sentAt, m.createdAt) AS decidedAt
            FROM messages m LEFT JOIN batches b ON b.id = m.batchId
            WHERE m.status IN ({",".join("?" * len(DONE_STATUSES))})
               OR (m.status IN ({",".join("?" * len(IN_FLIGHT_STATUSES))}) AND m.employeeHireDate < ?)
            """,
            (*DONE_STATUSES, *IN_FLIGHT_STATUSES, today),
        ).fetchall()
        hires: dict = {}

        def hire(key, name, hire_date):
            return hires.setdefault(key, {
                "batchId": key if isinstance(key, int) else None, "employeeName": name,
                "hireDate": hire_date, "lastDecidedAt": "", "messages": [], "meetings": [],
            })

        for r in rows:
            r = dict(r)
            r["decision"] = (r["status"] if r["status"] in ("rejected", "skipped")
                             else "expired" if r["status"] in IN_FLIGHT_STATUSES else "approved")
            h = hire(r["batchId"] if r["batchId"] is not None else f"m{r['id']}", r["hireName"], r["employeeHireDate"])
            h["messages"].append(r)
            h["lastDecidedAt"] = max(h["lastDecidedAt"], r["decidedAt"] or "")
    finally:
        conn.close()

    # A session shared by several hires of the same day shows under each of them.
    for m in meetings.decided_meetings():
        m["decidedAt"] = m.get("scheduledAt") or m.get("updatedAt") or m.get("createdAt") or ""
        for hr in m["hires"] or [{"batchId": m["batchId"], "name": m["employeeName"], "hireDate": m["startDate"]}]:
            h = hire(hr.get("batchId"), hr.get("name"), hr.get("hireDate"))
            h["meetings"].append(m)
            h["lastDecidedAt"] = max(h["lastDecidedAt"], m["decidedAt"])

    return sorted(hires.values(), key=lambda h: h["lastDecidedAt"], reverse=True)


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


@router.post("/agent/batches/{batch_id}/photo")
async def upload_batch_photo(batch_id: int, file: UploadFile = File(...)):
    """
    BambooHR had no photo for this hire when the batch was created — Junior
    uploads one here and Panda Bear generates the flyer and attaches it to
    every message in the batch that carries an image.
    """
    photo_bytes = await file.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    result = _agent.attach_employee_photo(batch_id, photo_bytes)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "No se pudo generar el flyer"))
    return result


@router.post("/agent/batches/{batch_id}/approve")
def approve_batch(batch_id: int):
    """
    "Aprobar todos": approves every still-pending message of this hire and
    creates each draft. Approving NEVER sends (DECISIONS.md). Each message's
    outcome (draft_created / ready_for_slack_draft / failed + reason) is in
    `results`; a draft failure is reported there, never as an HTTP 500.
    """
    return _agent.approve_all(batch_id)


@router.post("/agent/batches/{batch_id}/reject")
def reject_batch(batch_id: int):
    result = _agent.invoke_skill("approval_workflow", action="reject_batch", batch_id=batch_id)
    _agent._refresh_batch_status(batch_id)
    try:
        _agent.learn_from_batch(batch_id)
    except Exception as e:
        print(f"[routes] learn_from_batch failed for batch {batch_id}: {e}")
    return result


# ── ONBOARDING SESSION SCHEDULING ("Agendamiento") ──────────────
# Clicking "Agendar" is Junior's approval: only then is the event created on
# his calendar (with Meet) and the invitations sent. See services/onboarding_meetings.py.

from services import onboarding_meetings as meetings


class MeetingEdit(BaseModel):
    startDate: Optional[str] = None
    startTime: Optional[str] = None
    durationMinutes: Optional[int] = None
    attendeeEmail: Optional[str] = None
    extraAttendees: Optional[list[str]] = None
    title: Optional[str] = None
    eventId: Optional[str] = None  # Product Onboarding: which occurrence of Adrian's series
    hireEmails: Optional[dict[str, str]] = None  # batchId → corporate email, for each hire of the session


@router.get("/agent/meetings")
def list_meetings():
    return meetings.list_meetings(_agent._google)


@router.get("/agent/directory")
def search_directory(q: str = ""):
    """Name → work email from the BambooHR directory, for the 'Otros' invitee picker."""
    try:
        return meetings.search_directory(q)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/agent/meetings/{meeting_id}")
def edit_meeting(meeting_id: int, body: MeetingEdit):
    result = meetings.update_meeting(meeting_id, body.model_dump(exclude_none=True), _agent._google)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result["meeting"]


@router.get("/agent/meetings/{meeting_id}/slots")
def meeting_slots(meeting_id: int, from_date: str | None = None, hosts: str | None = None,
                  minutes: int | None = None):
    """First month / IT / Payroll: free slots for the hosts and Junior, from the date being looked at."""
    host_list = [h.strip() for h in hosts.split(",")] if hosts is not None else None
    return meetings.meeting_slots(meeting_id, _agent._google, from_date, host_list, minutes)


@router.post("/agent/meetings/{meeting_id}/add-hires")
def add_missing_hires(meeting_id: int):
    """Adds hires who start the same day to a session that was already scheduled (same event, no duplicate)."""
    return meetings.add_missing_hires(meeting_id, _agent._google)


@router.post("/agent/meetings/{meeting_id}/reschedule")
def reschedule_meeting(meeting_id: int, body: MeetingEdit):
    """Moves a scheduled session; the guests get Google's update."""
    return meetings.reschedule_meeting(meeting_id, body.model_dump(exclude_none=True), _agent._google)


@router.post("/agent/meetings/{meeting_id}/schedule")
def schedule_meeting(meeting_id: int, body: Optional[MeetingEdit] = None):
    """Saves any last edits, then creates the event. A failure comes back in the body, never as a bare 500."""
    if body:
        edits = body.model_dump(exclude_none=True)
        if edits:
            saved = meetings.update_meeting(meeting_id, edits, _agent._google)
            if not saved["ok"]:
                raise HTTPException(status_code=400, detail=saved["error"])
    return meetings.schedule_meeting(meeting_id, _agent._google)


@router.post("/agent/meetings/{meeting_id}/skip")
def skip_meeting(meeting_id: int):
    result = meetings.skip_meeting(meeting_id)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["error"])
    return result["meeting"]


# ── INDIVIDUAL MESSAGE ACTIONS ───────────────────────────────────

@router.post("/agent/messages/{msg_id}/approve")
def approve_message(msg_id: int):
    """Approves ONE message and creates only its draft. The other two stay pending."""
    result = _agent.approve_message(msg_id)
    if result.get("status") is None and result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/agent/messages/{msg_id}/reject")
def reject_message(msg_id: int):
    result = _agent.invoke_skill("approval_workflow", action="reject_message", message_id=msg_id)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    _agent._refresh_batch_status(result.get("batchId"))
    return result


@router.post("/agent/messages/{msg_id}/retry")
def retry_message(msg_id: int):
    """Retries a failed draft right now (the hourly retry does the same automatically)."""
    return _agent.prepare_message_draft(msg_id)


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
        if msg["status"] not in ("pending_approval", "failed"):
            raise HTTPException(
                status_code=409,
                detail=f"Solo se edita un mensaje pendiente o fallido (estado actual: {msg['status']})",
            )
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
    result = _agent.invoke_skill("approval_workflow", action="skip_message", message_id=msg_id)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    _agent._refresh_batch_status(result.get("batchId"))
    return result


@router.post("/agent/messages/{msg_id}/resend")
def resend_message(msg_id: int):
    """
    "Ya lo envié": Junior confirms HE sent it himself. The only way a message
    ever becomes 'sent' — and only once it actually got past approval, so a
    pending/failed/rejected message can never be marked sent by mistake.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE messages SET status='sent', sentAt=? WHERE id=? "
            "AND status IN ('draft_created','ready_for_slack_draft')",
            (datetime.now().isoformat(), msg_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            row = conn.execute("SELECT status FROM messages WHERE id=?", (msg_id,)).fetchone()
            raise HTTPException(
                status_code=409,
                detail=f"No se puede marcar como enviado (estado actual: {row['status'] if row else 'no existe'})",
            )
        return {"resent": True}
    finally:
        conn.close()


@router.get("/agent/slack-drafts/pending")
def pending_slack_drafts():
    """
    Messages approved and ready but not yet staged as real Slack drafts.
    Panda Bear's bot can't create native Slack drafts (no public API for that) —
    this list is what Junior asks Claude to turn into real Slack drafts, one
    destination resolved per message so nothing needs a manual lookup.
    """
    conn = get_db()
    try:
        msgs = conn.execute(
            "SELECT * FROM messages WHERE status='ready_for_slack_draft' ORDER BY createdAt"
        ).fetchall()
        slack_svc = _agent._kernel.get_service("slack_service")
        bienvenidas_channel = os.getenv("BIENVENIDAS_CHANNEL", "koronet")

        result = []
        for m in msgs:
            m = dict(m)
            if m["messageType"] == "manager_slack":
                manager_slack_id = m.get("slackTarget") or (
                    slack_svc.find_user_by_name(m["managerName"])
                    if slack_svc and m.get("managerName") else None
                )
                m["destination"] = {"kind": "dm", "managerName": m.get("managerName"), "slackUserId": manager_slack_id}
            elif m["messageType"] == "bienvenidas_slack":
                m["destination"] = {"kind": "channel", "channelName": bienvenidas_channel,
                                    "channelId": m.get("slackTarget")}
            result.append(m)

        return {"result": result, "count": len(result)}
    finally:
        conn.close()


@router.post("/agent/messages/{msg_id}/mark-drafted")
def mark_slack_draft_created(msg_id: int):
    """Called after a real Slack draft has been staged for this message, so it stops showing up as pending."""
    conn = get_db()
    try:
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        if msg["status"] not in ("ready_for_slack_draft", "draft_created"):
            raise HTTPException(
                status_code=409,
                detail=f"Solo un mensaje aprobado puede marcarse como borrador creado (estado: {msg['status']})",
            )
        conn.execute(
            "UPDATE messages SET status='draft_created', deliveryError=NULL, deliveredAt=? WHERE id=?",
            (datetime.now().isoformat(), msg_id),
        )
        conn.commit()
        return {"updated": True}
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


@router.get("/agent/conversation/personal")
def get_personal_conversation():
    """
    History for the personal-assistant thread. Declared BEFORE the generic
    /{session_id} route below so FastAPI matches this specific path first —
    otherwise "personal" would be swallowed as a literal session_id.
    think_personal() always keys memory by JUNIOR_SLACK_ID (shared between
    Slack and the dashboard — one continuous personal-assistant thread,
    not two parallel ones), so history lookup must use that same key.
    """
    junior_id = os.getenv("JUNIOR_SLACK_ID", "")
    return _agent._memory.get_conversation(junior_id)


@router.get("/agent/conversation/{session_id}")
def get_conversation(session_id: str):
    return _agent._memory.get_conversation(session_id)


@router.post("/agent/conversation/personal")
def conversation_personal(body: ConversationRequest):
    """
    Talk to Panda Bear's personal assistant (calendar/email).
    The dashboard is single-tenant (only Junior can log in), so a valid
    session here already proves the caller is Junior — we pass his
    JUNIOR_SLACK_ID through so think_personal's own identity check still passes.
    """
    junior_id = os.getenv("JUNIOR_SLACK_ID", "")
    reply = _agent.think_personal(user_id=junior_id, text=body.message)
    return {"reply": reply, "agent": "Panda Bear (Personal)"}


# ── MEETING JOIN CONTROL ─────────────────────────────────────────

class JoinMeetingRequest(BaseModel):
    meetUrl: str
    calendarEventId: Optional[str] = None
    title: Optional[str] = None


@router.post("/agent/meetings/join")
def join_meeting(body: JoinMeetingRequest):
    return _agent.join_meeting(meet_url=body.meetUrl, calendar_event_id=body.calendarEventId, title=body.title)


@router.post("/agent/meetings/leave")
def leave_meeting():
    return _agent.leave_meeting()


@router.get("/agent/meetings/status")
def meeting_status():
    return _agent.meeting_status()


# ── LEARNING ─────────────────────────────────────────────────────

@router.post("/agent/learn/{batch_id}")
def learn(batch_id: int):
    return _agent.learn_from_batch(batch_id)


@router.get("/agent/employee-timeline/{employee_name}")
def employee_timeline(employee_name: str):
    return _agent._memory.get_employee_timeline(employee_name)


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
