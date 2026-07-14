"""
Panda Bear — Service Base
All services implement this contract.
The Kernel orchestrates them: start, stop, health, heartbeat, recover, metrics.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional


class ServiceStatus:
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    RECOVERING = "recovering"
    STARTING = "starting"


class ServiceBase(ABC):
    """
    Base class for all Panda Bear services.
    Services are long-running processes managed by the Kernel.
    """

    name: str = "unnamed_service"
    description: str = "No description."

    def __init__(self):
        self._status = ServiceStatus.STOPPED
        self._started_at: Optional[str] = None
        self._errors: list = []
        self._heartbeat_count: int = 0
        self._last_heartbeat: Optional[str] = None

    def start(self) -> dict:
        """Start the service."""
        self._status = ServiceStatus.STARTING
        try:
            self._on_start()
            self._status = ServiceStatus.RUNNING
            self._started_at = datetime.now().isoformat()
            print(f"[{self.name}] ✅ Started")
            return {"started": True, "service": self.name}
        except Exception as e:
            self._status = ServiceStatus.ERROR
            self._errors.append(str(e))
            print(f"[{self.name}] ❌ Start failed: {e}")
            return {"started": False, "service": self.name, "error": str(e)}

    def stop(self) -> dict:
        """Stop the service gracefully."""
        try:
            self._on_stop()
            self._status = ServiceStatus.STOPPED
            print(f"[{self.name}] ⏹ Stopped")
            return {"stopped": True, "service": self.name}
        except Exception as e:
            self._errors.append(str(e))
            return {"stopped": False, "service": self.name, "error": str(e)}

    def restart(self) -> dict:
        """Restart the service."""
        self.stop()
        return self.start()

    def health(self) -> dict:
        """Returns the current health state."""
        return {
            "service": self.name,
            "status": self._status,
            "startedAt": self._started_at,
            "errors": self._errors[-5:],
            "lastHeartbeat": self._last_heartbeat,
            "heartbeatCount": self._heartbeat_count,
            **self._health_details(),
        }

    def heartbeat(self) -> dict:
        """Records a heartbeat and checks if still alive."""
        self._heartbeat_count += 1
        self._last_heartbeat = datetime.now().isoformat()
        is_alive = self._check_alive()
        if not is_alive and self._status == ServiceStatus.RUNNING:
            self._status = ServiceStatus.ERROR
        return {
            "service": self.name,
            "alive": is_alive,
            "count": self._heartbeat_count,
            "timestamp": self._last_heartbeat,
        }

    def recover(self) -> dict:
        """Attempt to recover from an error state."""
        if self._status != ServiceStatus.ERROR:
            return {"recovered": False, "reason": "Service is not in error state"}
        self._status = ServiceStatus.RECOVERING
        return self.restart()

    def metrics(self) -> dict:
        """Returns service performance metrics."""
        return {
            "service": self.name,
            "status": self._status,
            "uptimeSince": self._started_at,
            "errors": len(self._errors),
            "heartbeats": self._heartbeat_count,
            **self._get_metrics(),
        }

    # ── Override these in subclasses ────────────────────────────

    def _on_start(self) -> None:
        pass

    def _on_stop(self) -> None:
        pass

    def _check_alive(self) -> bool:
        return self._status == ServiceStatus.RUNNING

    def _health_details(self) -> dict:
        return {}

    def _get_metrics(self) -> dict:
        return {}

    def __repr__(self):
        return f"<Service:{self.name}[{self._status}]>"
