"""
Panda Bear — Sync Service
Runs the agent's detection cycle (run_sync) on a fixed daily schedule
(SYNC_TIMES, Colombia time by default) so new hires in BambooHR are picked up
and queued in "Mensajes programados" without anyone asking. On start it
catches up on a slot missed while the agent was down. Only queues messages —
never sends anything and never creates drafts (that needs Junior's approval).
If a scheduled sync fails, Junior gets a DM from the Panda Bear bot.
"""

import threading
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from services.base import ServiceBase
from db.schema import get_db


def parse_times(raw: str) -> list[tuple[int, int]]:
    """'08:00,12:00,16:00' → [(8, 0), (12, 0), (16, 0)], sorted; bad entries are skipped."""
    times = []
    for part in (raw or "").split(","):
        try:
            h, m = part.strip().split(":")
            h, m = int(h), int(m)
            if 0 <= h < 24 and 0 <= m < 60:
                times.append((h, m))
        except ValueError:
            continue
    return sorted(set(times))


class SyncService(ServiceBase):
    name = "sync_service"
    description = "Runs the agent's hire-detection cycle (run_sync) at fixed times each day."

    def __init__(self, agent, times: str = "08:00,12:00,16:00", timezone: str = "America/Bogota",
                 lookback_days: int = 60, on_failure=None):
        super().__init__()
        self._agent = agent
        self._times = parse_times(times) or [(8, 0), (12, 0), (16, 0)]
        self._tz = ZoneInfo(timezone)
        self._lookback_days = lookback_days
        self._on_failure = on_failure  # callable(slot_label: str, error: str)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._runs_completed: int = 0
        self._last_run: str | None = None
        self._last_result: dict | None = None
        self._next_run: datetime | None = None

    def _on_start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sync-loop")
        self._thread.start()

    def _on_stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _check_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _health_details(self) -> dict:
        return {
            "runsCompleted": self._runs_completed,
            "lastRun": self._last_run,
            "schedule": self.schedule_labels(),
            "nextRun": self._next_run.isoformat() if self._next_run else None,
        }

    def _get_metrics(self) -> dict:
        return {
            "runsCompleted": self._runs_completed,
            "lastRun": self._last_run,
        }

    # ── Schedule ───────────────────────────────────────────────────

    def schedule_labels(self) -> list[str]:
        return [f"{h:02d}:{m:02d}" for h, m in self._times]

    def _slots_around(self, now: datetime) -> list[datetime]:
        """Every scheduled slot yesterday, today and tomorrow (tz-aware), ascending."""
        day = now.date()
        return [
            datetime(d.year, d.month, d.day, h, m, tzinfo=self._tz)
            for d in (day - timedelta(days=1), day, day + timedelta(days=1))
            for h, m in self._times
        ]

    def next_slot(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(self._tz)
        return next(s for s in self._slots_around(now) if s > now)

    def last_slot(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(self._tz)
        return [s for s in self._slots_around(now) if s <= now][-1]

    @staticmethod
    def last_success() -> str | None:
        """startedAt of the last completed (non-dry) sync, manual or automatic."""
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT MAX(startedAt) FROM runs WHERE status='completed' AND COALESCE(dryRun, 0)=0"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def status(self) -> dict:
        now = datetime.now(self._tz)
        return {
            "times": self.schedule_labels(),
            "timezone": str(self._tz),
            "nextRun": self.next_slot(now).isoformat(),
            "lastSuccess": self.last_success(),
        }

    def _missed_slot(self) -> bool:
        """True when the latest slot passed with no successful sync after it (agent was down)."""
        last_ok = self.last_success()
        if not last_ok:
            return True
        slot = self.last_slot()
        # runs.startedAt is naive local time of this machine/server
        last_ok_dt = datetime.fromisoformat(last_ok).astimezone(self._tz)
        return last_ok_dt < slot

    # ── Loop ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        if self._missed_slot():
            self._run_once(f"recuperación del de las {self.last_slot().strftime('%H:%M')}")
        while not self._stop_event.is_set():
            self._next_run = self.next_slot()
            wait = (self._next_run - datetime.now(self._tz)).total_seconds()
            # Wake at most every 5 min: a PC that slept would otherwise
            # oversleep the slot; on wake the slot is caught up right away.
            if self._stop_event.wait(min(max(wait, 0), 300)):
                break
            if datetime.now(self._tz) >= self._next_run:
                self._run_once(f"de las {self._next_run.strftime('%H:%M')}")

    def _run_once(self, slot_label: str) -> None:
        self._last_run = datetime.now().isoformat()
        try:
            result = self._agent.run_sync(dry_run=False, lookback_days=self._lookback_days, trigger="auto")
            self._last_result = result
            self._runs_completed += 1
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            self._errors.append(error)
            print(f"[sync_service] Sync automático {slot_label} falló: {error}")
            if self._on_failure:
                try:
                    self._on_failure(slot_label, error)
                except Exception as notify_error:
                    print(f"[sync_service] No pude avisar por Slack: {notify_error}")
