"""
Panda Bear — Service Kernel
The Kernel manages all services. It starts them, monitors them, and recovers them.
If Panda Bear goes down, the Kernel is what brings it back.
"""

from datetime import datetime
from typing import Dict
from services.base import ServiceBase, ServiceStatus


class ServiceKernel:
    """
    The runtime that manages all Panda Bear services.
    Every service must be registered here. None can be started manually.
    """

    def __init__(self):
        self._services: Dict[str, ServiceBase] = {}
        self._started_at: str = None
        self._is_running: bool = False

    def register(self, service: ServiceBase) -> None:
        """Register a service with the Kernel."""
        self._services[service.name] = service
        print(f"[Kernel] Registered service: {service.name}")

    def start_all(self) -> dict:
        """Start all registered services."""
        print("\n[Kernel] Starting all services...")
        results = {}
        for name, svc in self._services.items():
            result = svc.start()
            results[name] = result
        self._started_at = datetime.now().isoformat()
        self._is_running = True
        running = sum(1 for r in results.values() if r.get("started", False))
        print(f"[Kernel] {running}/{len(self._services)} services running\n")
        return results

    def stop_all(self) -> dict:
        """Stop all services gracefully."""
        results = {}
        for name, svc in reversed(list(self._services.items())):
            results[name] = svc.stop()
        self._is_running = False
        return results

    def restart(self, service_name: str = None) -> dict:
        """Restart a specific service or all services."""
        if service_name:
            svc = self._services.get(service_name)
            if not svc:
                return {"error": f"Service '{service_name}' not found"}
            return svc.restart()
        return {name: svc.restart() for name, svc in self._services.items()}

    def health(self) -> dict:
        """Returns health status of all services."""
        service_health = {name: svc.health() for name, svc in self._services.items()}
        all_healthy = all(
            h["status"] == ServiceStatus.RUNNING for h in service_health.values()
        )
        return {
            "kernel": {
                "running": self._is_running,
                "startedAt": self._started_at,
                "totalServices": len(self._services),
                "healthyServices": sum(
                    1 for h in service_health.values()
                    if h["status"] == ServiceStatus.RUNNING
                ),
                "status": "healthy" if all_healthy else "degraded",
            },
            "services": service_health,
        }

    def heartbeat(self) -> dict:
        """Sends heartbeat to all services. Records incidents and auto-recovers failures."""
        from core.incident import classify_error, record_incident
        results = {}
        for name, svc in self._services.items():
            hb = svc.heartbeat()
            if not hb["alive"] and svc._status == ServiceStatus.RUNNING:
                print(f"[Kernel] {name} failed heartbeat — attempting recovery")
                error_msg = f"Service {name} failed heartbeat check"
                classification = classify_error(error_msg, service=name)
                classification["type"] = "service_unavailable"
                classification["cause"] = f"{name} dejó de responder al heartbeat"
                recovery = svc.recover()
                recovered = recovery.get("started", False)
                record_incident(
                    service=name,
                    error=error_msg,
                    classification=classification,
                    recovered=recovered,
                    recovery_attempts=1,
                )
                hb["recovered"] = recovered
                hb["incidentRecorded"] = True
            results[name] = hb
        return results

    def metrics(self) -> dict:
        """Aggregates metrics from all services."""
        return {
            "kernel": {
                "startedAt": self._started_at,
                "servicesCount": len(self._services),
            },
            "services": {name: svc.metrics() for name, svc in self._services.items()},
        }

    def get_service(self, name: str) -> ServiceBase | None:
        return self._services.get(name)

    def list_services(self) -> list:
        return [
            {
                "name": name,
                "description": svc.description,
                "status": svc._status,
            }
            for name, svc in self._services.items()
        ]
