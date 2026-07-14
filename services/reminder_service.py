"""
Panda Bear — Reminder Service
Periodically checks for upcoming hire dates and surfaces reminders to the agent.
Runs in a background thread. The agent acts on reminders — this service only detects.
"""

import threading
import os
from datetime import datetime, timedelta
from services.base import ServiceBase, ServiceStatus


class ReminderService(ServiceBase):
    name = "reminder_service"
    description = "Monitors upcoming hires and surfaces reminders to Panda Bear."

    def __init__(self, on_reminder=None, check_interval_seconds: int = 3600):
        super().__init__()
        self._on_reminder = on_reminder  # callback(employee_data: dict)
        self._interval = check_interval_seconds
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
            "intervalSeconds": self._interval,
        }

    def _get_metrics(self) -> dict:
        return {
            "remindersFired": self._reminders_fired,
            "lastCheck": self._last_check,
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_check()
            except Exception as e:
                self._errors.append(str(e))
            self._stop_event.wait(self._interval)

    def _run_check(self) -> None:
        """
        Reads upcoming hires from the DB (populated by run_sync) and
        fires reminders for employees whose manager notification date is today.
        """
        self._last_check = datetime.now().isoformat()
        try:
            from db.schema import get_db
            conn = get_db()
            today = datetime.today().strftime("%Y-%m-%d")
            msgs = conn.execute(
                """
                SELECT * FROM messages
                WHERE status = 'pending_approval'
                  AND managerMessageDate = ?
                """,
                (today,),
            ).fetchall()
            conn.close()

            for msg in msgs:
                self._reminders_fired += 1
                if self._on_reminder:
                    self._on_reminder(dict(msg))
        except Exception as e:
            self._errors.append(f"check error: {e}")
