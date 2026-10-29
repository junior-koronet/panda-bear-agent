"""
Hire reminders — Panda Bear DMs Junior while a hire still has onboarding
messages waiting for his approval and the hire date is getting close.

Rules (all configurable via env vars, defaults in brackets):
  REMINDER_FIRST_BUSINESS_DAYS   [5]  first reminder when this many business days are left
  REMINDER_DAILY_HOUR            [8]  daily reminder hour, in REMINDER_TIMEZONE
  REMINDER_URGENT_BUSINESS_DAYS  [1]  from this many business days before: urgent wording
  REMINDER_TIMEZONE   [America/Bogota]
  REMINDER_WEEKENDS   [false]       also remind on Saturday/Sunday
  DASHBOARD_URL       [http://localhost:8000/]

Guarantees:
  - Never more than one reminder per hire per (Colombia) day: enforced by the
    UNIQUE(batchId, reminderDate) constraint on hire_reminders, claimed BEFORE
    sending. If the Slack send fails the claim is released so a later check retries.
  - Stops as soon as nothing of that hire is 'pending_approval' (approved,
    skipped or rejected all count as resolved).
"""

import os
import sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from db.schema import get_db

LABELS = {
    "manager_slack": "DM al líder",
    "bienvenidas_slack": "Bienvenida en #koronet",
    "employee_email": "Correo de bienvenida al empleado",
}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def config() -> dict:
    return {
        "firstBusinessDays": int(os.getenv("REMINDER_FIRST_BUSINESS_DAYS", 5)),
        "dailyHour": int(os.getenv("REMINDER_DAILY_HOUR", 8)),
        "urgentBusinessDays": int(os.getenv("REMINDER_URGENT_BUSINESS_DAYS", 1)),
        "timezone": os.getenv("REMINDER_TIMEZONE", "America/Bogota"),
        "weekends": os.getenv("REMINDER_WEEKENDS", "false").lower() in ("1", "true", "yes", "si", "sí"),
        "dashboardUrl": os.getenv("DASHBOARD_URL", "http://localhost:8000/"),
    }


def business_days_before(d: date, n: int) -> date:
    cur, counted = d, 0
    while counted < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            counted += 1
    return cur


def now_local(cfg: dict | None = None) -> datetime:
    cfg = cfg or config()
    return datetime.now(ZoneInfo(cfg["timezone"]))


def pending_hires(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.batchId, MAX(m.employeeName) AS employeeName, MIN(m.employeeHireDate) AS hireDate,
               GROUP_CONCAT(m.messageType) AS pendingTypes
        FROM messages m
        WHERE m.status = 'pending_approval' AND m.batchId IS NOT NULL
        GROUP BY m.batchId
        """
    ).fetchall()
    return [dict(r) for r in rows]


def due_reminders(now: datetime, cfg: dict | None = None, conn=None) -> list[dict]:
    """Pure decision: which hires should get a reminder at `now` (tz-aware, local)."""
    cfg = cfg or config()
    own = conn is None
    conn = conn or get_db()
    try:
        today = now.date()
        if now.hour < cfg["dailyHour"]:
            return []
        if today.weekday() >= 5 and not cfg["weekends"]:
            return []
        due = []
        for h in pending_hires(conn):
            try:
                hire = datetime.strptime(h["hireDate"] or "", "%Y-%m-%d").date()
            except ValueError:
                continue
            if today >= hire:
                continue  # already started — nothing useful left to remind
            first = business_days_before(hire, cfg["firstBusinessDays"])
            urgent = business_days_before(hire, cfg["urgentBusinessDays"])
            if today < first:
                continue
            already_today = conn.execute(
                "SELECT 1 FROM hire_reminders WHERE batchId=? AND reminderDate=?",
                (h["batchId"], today.isoformat()),
            ).fetchone()
            if already_today:
                continue
            any_before = conn.execute(
                "SELECT 1 FROM hire_reminders WHERE batchId=?", (h["batchId"],)
            ).fetchone()
            kind = "urgent" if today >= urgent else ("daily" if any_before else "first")
            types = sorted(set((h["pendingTypes"] or "").split(",")) - {""})
            due.append({
                "batchId": h["batchId"],
                "employeeName": h["employeeName"],
                "hireDate": hire.isoformat(),
                "pendingTypes": types,
                "kind": kind,
                "reminderDate": today.isoformat(),
            })
        return due
    finally:
        if own:
            conn.close()


def build_text(r: dict, cfg: dict | None = None) -> str:
    cfg = cfg or config()
    hire = datetime.strptime(r["hireDate"], "%Y-%m-%d").date()
    fecha = f"{DIAS[hire.weekday()]} {hire.strftime('%d/%m/%Y')}"
    pend = "\n".join(f"• {LABELS.get(t, t)}" for t in r["pendingTypes"])
    if r["kind"] == "urgent":
        head = f"🚨 *URGENTE — {r['employeeName']} ingresa el {fecha}* y todavía hay mensajes sin aprobar:"
    elif r["kind"] == "first":
        head = f"🐼 *{r['employeeName']} ingresa el {fecha}.* Estos mensajes siguen pendientes de tu aprobación:"
    else:
        head = f"🐼 Recordatorio diario: *{r['employeeName']}* ingresa el {fecha}. Sigue pendiente:"
    return (
        f"{head}\n{pend}\n\n"
        f"👉 Apruébalos (o omítelos) en el dashboard: {cfg['dashboardUrl']}\n"
        f"_Aprobar solo crea los borradores — nada se envía sin ti._"
    )


def run_due_reminders(send, now: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """
    Sends every due reminder through `send(text) -> bool`. Claims the
    (batch, day) slot first so concurrent checks can't double-send; releases
    it if the send failed so the next check retries.
    """
    cfg = cfg or config()
    now = now or now_local(cfg)
    fired = []
    conn = get_db()
    try:
        for r in due_reminders(now, cfg, conn):
            try:
                conn.execute(
                    "INSERT INTO hire_reminders (batchId, reminderDate, kind, pendingTypes, sentAt) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r["batchId"], r["reminderDate"], r["kind"], ",".join(r["pendingTypes"]),
                     datetime.now().isoformat()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                continue  # another check already took today's slot
            text = build_text(r, cfg)
            ok = False
            try:
                ok = bool(send(text))
            except Exception as e:
                print(f"[hire_reminders] send failed for batch {r['batchId']}: {e}")
            if not ok:
                conn.execute(
                    "DELETE FROM hire_reminders WHERE batchId=? AND reminderDate=?",
                    (r["batchId"], r["reminderDate"]),
                )
                conn.commit()
                continue
            fired.append({**r, "text": text})
        return fired
    finally:
        conn.close()
