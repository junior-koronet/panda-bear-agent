"""
Panda Bear — Reminder Service
Periodically checks for upcoming hire dates and surfaces reminders to the agent.
Runs in a background thread. The agent acts on reminders — this service only detects.
"""

import threading
import os
import time
from datetime import datetime, timedelta
from services.base import ServiceBase, ServiceStatus


class ReminderService(ServiceBase):
    name = "reminder_service"
    description = "Monitors upcoming hires and surfaces reminders to Panda Bear."

    def __init__(self, on_hire_reminder=None, on_commitments_due=None, on_slack_drafts_ready=None,
                 on_photo_found=None, on_retry_failed_drafts=None, notify_junior=None,
                 check_interval_seconds: int = 3600, commitment_reminder_days: int = 1):
        super().__init__()
        self._on_hire_reminder = on_hire_reminder  # callback(text: str) -> bool (sent?)
        self._last_reminder_check: str | None = None
        self._on_commitments_due = on_commitments_due  # callback(commitments: list[dict])
        self._on_slack_drafts_ready = on_slack_drafts_ready  # callback(count: int)
        self._on_photo_found = on_photo_found  # callback(batch_id: int, photo_bytes: bytes) -> dict
        self._on_retry_failed_drafts = on_retry_failed_drafts  # callback(batch_id: int)
        self._notify_junior = notify_junior  # callback(text: str) -> bool
        self._interval = check_interval_seconds
        self._commitment_reminder_days = commitment_reminder_days
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._reminders_fired: int = 0
        self._last_check: str | None = None

    def _on_start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="reminder-loop")
        self._thread.start()

    def _on_stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _check_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _health_details(self) -> dict:
        return {
            "remindersFired": self._reminders_fired,
            "lastCheck": self._last_check,
            "lastHireReminderCheck": self._last_reminder_check,
            "intervalSeconds": self._interval,
        }

    def _get_metrics(self) -> dict:
        return {
            "remindersFired": self._reminders_fired,
            "lastCheck": self._last_check,
        }

    # Hire reminders must go out close to 8:00 sharp, so the loop ticks often;
    # the heavier checks (BambooHR photos, Gmail retries...) keep their hourly pace.
    HIRE_REMINDER_TICK_SECONDS = 300

    def _loop(self) -> None:
        last_full = None
        while not self._stop_event.is_set():
            try:
                self.run_hire_reminders()
            except Exception as e:
                self._errors.append(f"hire reminders error: {e}")
            try:
                # Sweep for approved Slack messages still without a draft (e.g.
                # approved right before a restart). Approvals also kick it instantly.
                from services.slack_draft_worker import create_pending_slack_drafts
                create_pending_slack_drafts(notify=self._notify_junior)
            except Exception as e:
                self._errors.append(f"slack draft worker error: {e}")
            if last_full is None or time.monotonic() - last_full >= self._interval:
                last_full = time.monotonic()
                try:
                    self._run_check()
                except Exception as e:
                    self._errors.append(str(e))
            self._stop_event.wait(min(self._interval, self.HIRE_REMINDER_TICK_SECONDS))

    def run_hire_reminders(self) -> list:
        """
        DMs Junior (via the Panda Bear bot) about hires that still have messages
        pending approval as the hire date approaches. Rules and the
        one-per-hire-per-day guarantee live in services/hire_reminders.py.
        Also callable from the /cron/tick endpoint (external wake-up).
        """
        from services.hire_reminders import run_due_reminders
        self._last_reminder_check = datetime.now().isoformat()
        if not self._on_hire_reminder:
            return []
        fired = run_due_reminders(self._on_hire_reminder)
        self._reminders_fired += len(fired)
        return fired

    def _run_check(self) -> None:
        """
        Hourly housekeeping: meeting commitments, Slack-draft nudges, retries of
        failed drafts, missing photos. (Pending-approval reminders are handled
        by run_hire_reminders above — one DM per hire per day.)
        """
        self._last_check = datetime.now().isoformat()
        try:
            self._check_commitments()
        except Exception as e:
            self._errors.append(f"commitments check error: {e}")

        try:
            self._check_slack_drafts_ready()
        except Exception as e:
            self._errors.append(f"slack drafts check error: {e}")

        try:
            self._retry_failed_drafts()
        except Exception as e:
            self._errors.append(f"failed drafts retry error: {e}")

        try:
            self._check_missing_photos()
        except Exception as e:
            self._errors.append(f"missing photo check error: {e}")

    def _check_missing_photos(self) -> None:
        """
        A hire's flyer is skipped at detection time if BambooHR has no photo
        for them yet (batches.photoStatus='missing') — Junior gets asked to
        either upload one or wait for BambooHR to have it. This re-checks
        BambooHR on every cycle so the flyer gets generated automatically
        the moment a photo shows up there, without Junior having to ask.
        """
        from db.schema import get_db
        from skills.bamboo import get_employee_photo

        conn = get_db()
        try:
            batches = conn.execute(
                "SELECT id, bambooEmployeeId FROM batches "
                "WHERE photoStatus='missing' AND bambooEmployeeId IS NOT NULL AND bambooEmployeeId != ''"
            ).fetchall()
        finally:
            conn.close()

        for batch in batches:
            photo_bytes = get_employee_photo(batch["bambooEmployeeId"])
            if photo_bytes and self._on_photo_found:
                self._reminders_fired += 1
                self._on_photo_found(batch["id"], photo_bytes)

    MAX_DRAFT_RETRIES = 24  # one per check cycle — about a day at the default interval

    def _retry_failed_drafts(self) -> None:
        """
        An approved message whose draft failed (e.g. Gmail was disconnected)
        must not stay dead — retries it every cycle, up to MAX_DRAFT_RETRIES.
        """
        from db.schema import get_db
        conn = get_db()
        try:
            batch_ids = [
                r["batchId"] for r in conn.execute(
                    # Per-message approval: the batch can still be 'pending_approval'
                    # while one of its messages already failed. 'approved' rows older
                    # than 10 min are approvals interrupted mid-way (crash/restart).
                    "SELECT DISTINCT m.batchId FROM messages m "
                    "WHERE ((m.status='failed' AND COALESCE(m.retries, 0) < ?) "
                    "   OR (m.status='approved' AND COALESCE(m.approvedAt, '') < ?)) "
                    "AND m.employeeHireDate >= ?",  # past hires: nothing left to prepare
                    (self.MAX_DRAFT_RETRIES,
                     (datetime.now() - timedelta(minutes=10)).isoformat(),
                     datetime.now().strftime("%Y-%m-%d")),
                ).fetchall()
            ]
        finally:
            conn.close()
        for batch_id in batch_ids:
            if self._on_retry_failed_drafts:
                self._on_retry_failed_drafts(batch_id)

    # Re-nudge while Slack drafts are still waiting — one ping is easy to miss.
    SLACK_DRAFT_RENUDGE_HOURS = 4

    def _check_slack_drafts_ready(self) -> None:
        """
        Panda Bear's bot can't create native Slack drafts itself (no public API
        for that) — nudges Junior to ask his Claude session to stage them.
        Nudges when there's a new message waiting (draftNotifiedAt IS NULL), and
        again every SLACK_DRAFT_RENUDGE_HOURS while any stays pending — so an
        approved Slack message can't sit forgotten. Reports the full pending total.
        """
        from db.schema import get_db
        conn = get_db()
        try:
            new_ids = [
                r["id"] for r in conn.execute(
                    # Only legacy rows with no verified destination: everything else
                    # is turned into a draft automatically by slack_draft_worker.
                    "SELECT id FROM messages WHERE status='ready_for_slack_draft' "
                    "AND (slackTarget IS NULL OR slackTarget = '') "
                    "AND (draftNotifiedAt IS NULL OR draftNotifiedAt < ?)",
                    ((datetime.now() - timedelta(hours=self.SLACK_DRAFT_RENUDGE_HOURS)).isoformat(),),
                ).fetchall()
            ]
            if new_ids:
                total = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE status='ready_for_slack_draft' "
                    "AND (slackTarget IS NULL OR slackTarget = '')"
                ).fetchone()[0]
                if self._on_slack_drafts_ready:
                    self._reminders_fired += 1
                    self._on_slack_drafts_ready(total)
                now = datetime.now().isoformat()
                conn.executemany(
                    "UPDATE messages SET draftNotifiedAt=? WHERE id=?",
                    [(now, i) for i in new_ids],
                )
                conn.commit()
        finally:
            conn.close()

    def _check_commitments(self) -> None:
        """
        Reminds Junior about open commitments (from meeting notes) that
        haven't been nudged in the last `commitment_reminder_days` days.
        One digest per check, not one message per commitment.
        """
        from db.schema import get_db
        conn = get_db()
        try:
            cutoff = (datetime.now() - timedelta(days=self._commitment_reminder_days)).isoformat()
            rows = conn.execute(
                """
                SELECT * FROM commitments
                WHERE status = 'open'
                  AND (remindedAt IS NULL OR remindedAt <= ?)
                """,
                (cutoff,),
            ).fetchall()
            commitments = [dict(r) for r in rows]
            if not commitments:
                return

            if self._on_commitments_due:
                self._reminders_fired += 1
                self._on_commitments_due(commitments)

            now = datetime.now().isoformat()
            for c in commitments:
                conn.execute("UPDATE commitments SET remindedAt=? WHERE id=?", (now, c["id"]))
            conn.commit()
        finally:
            conn.close()
