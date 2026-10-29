"""
Service: Meeting Join
Joins a Google Meet call on Junior's explicit command (Slack or dashboard),
records it, and hands the transcript to the agent when the call ends.

v1 note: `_run_join` below is a STUB — it fakes joining, waits a few seconds,
and calls back with a canned transcript. This lets the whole pipeline
(Slack/dashboard trigger → this service → agent.process_recorded_meeting →
ingest_meeting_content → "which format?" → generation) get built and tested
before the real Playwright/audio-capture automation is written. Replace
`_run_join`'s body once that part is ready — the state machine, threading
model, and public methods around it are already the real shape.

One meeting at a time by design — a second start_join() while one is
already active is rejected rather than queued.
"""

import os
import threading
import time
from datetime import datetime
from services.base import ServiceBase

MAX_DURATION_MINUTES = int(os.getenv("MEETING_BOT_MAX_DURATION_MINUTES", "180"))

IDLE = "idle"
LAUNCHING = "launching"
KNOCKING = "knocking"
IN_CALL = "in_call"
ENDED = "ended"
TRANSCRIBING = "transcribing"
DONE = "done"
ERROR = "error"


class MeetingJoinService(ServiceBase):
    name = "meeting_join_service"
    description = "Se une a una reunión de Meet por orden de Junior, grava y transcribe."

    def __init__(self, on_call_ended=None):
        super().__init__()
        self._on_call_ended = on_call_ended  # callback(title, transcript, meet_url)
        self._state = IDLE
        self._meet_url = None
        self._title = None
        self._started_call_at = None
        self._error_detail = None
        self._leave_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def _on_start(self) -> None:
        # Validate config only — never opens a browser at server startup.
        self._state = IDLE

    def _on_stop(self) -> None:
        self._leave_requested.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _check_alive(self) -> bool:
        return True  # idle is a valid, healthy state

    def _health_details(self) -> dict:
        # Namespaced to avoid colliding with ServiceBase.health()'s own
        # "status"/"startedAt" keys (that's the service's run state, not
        # the meeting's — mixing them up previously made the kernel think
        # this service was down whenever no meeting was active).
        status = self.get_status()
        return {
            "meetingState": status["status"],
            "meetUrl": status["meetUrl"],
            "meetingTitle": status["title"],
            "meetingStartedAt": status["startedAt"],
            "meetingError": status["error"],
        }

    # ── PUBLIC API ──────────────────────────────────────────────────

    def start_join(self, meet_url: str, calendar_event_id: str = None, title: str = None) -> dict:
        if self._state not in (IDLE, DONE, ERROR):
            return {"success": False, "error": f"Ya hay una reunión activa (estado: {self._state})"}

        self._state = LAUNCHING
        self._meet_url = meet_url
        self._title = title or "Reunión sin título"
        self._started_call_at = datetime.now().isoformat()
        self._error_detail = None
        self._leave_requested.clear()

        self._thread = threading.Thread(
            target=self._run_join, args=(meet_url, calendar_event_id), daemon=True, name="meeting-join"
        )
        self._thread.start()
        return {"success": True, "status": self._state}

    def request_leave(self) -> dict:
        if self._state not in (LAUNCHING, KNOCKING, IN_CALL):
            return {"success": False, "error": f"No hay una reunión activa para salir (estado: {self._state})"}
        self._leave_requested.set()
        return {"success": True, "status": "leaving"}

    def get_status(self) -> dict:
        return {
            "status": self._state,
            "meetUrl": self._meet_url,
            "title": self._title,
            "startedAt": self._started_call_at,
            "error": self._error_detail,
        }

    # ── STUB JOIN LOOP (replace with real Playwright automation) ────

    def _run_join(self, meet_url: str, calendar_event_id: str) -> None:
        try:
            self._state = IN_CALL
            for _ in range(5):
                if self._leave_requested.is_set():
                    break
                time.sleep(1)

            self._state = ENDED
            self._state = TRANSCRIBING
            fake_transcript = (
                f"[TRANSCRIPCIÓN DE PRUEBA — modo stub, sin Playwright todavía]\n"
                f"Reunión: {self._title}\nLink: {meet_url}\n"
                f"Junior: Esto es una prueba del flujo de unión a reuniones.\n"
                f"Junior: El compromiso de prueba es revisar que todo el flujo funcione de punta a punta."
            )
            self._state = DONE
            if self._on_call_ended:
                self._on_call_ended(self._title, fake_transcript, meet_url)
        except Exception as e:
            self._state = ERROR
            self._error_detail = str(e)
