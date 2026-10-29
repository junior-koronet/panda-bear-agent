"""
Panda Bear — Sync Service
Periodically runs the agent's detection cycle (run_sync) so new hires in
BambooHR are picked up and queued for approval without anyone asking.
Runs in a background thread; only queues messages — never sends anything.
"""

import threading
import os
from datetime import datetime
from services.base import ServiceBase


class SyncService(ServiceBase):
    name = "sync_service"
    description = "Periodically runs the agent's hire-detection cycle (run_sync)."

    def __init__(self, agent, check_interval_seconds: int = 21600, lookback_days: int = 60):
        super().__init__()
        self._agent = agent
        self._interval = check_interval_seconds
        self._lookback_days = lookback_days
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._runs_completed: int = 0
        self._last_run: str | None = None
        self._last_result: dict | None = None

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
            "intervalSeconds": self._interval,
        }

    def _get_metrics(self) -> dict:
        return {
            "runsCompleted": self._runs_completed,
            "lastRun": self._last_run,
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception as e:
                self._errors.append(str(e))
            self._stop_event.wait(self._interval)

    def _run_once(self) -> None:
        self._last_run = datetime.now().isoformat()
        result = self._agent.run_sync(dry_run=False, lookback_days=self._lookback_days)
        self._last_result = result
        self._runs_completed += 1
