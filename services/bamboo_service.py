"""
Service: BambooHR
Maintains the connection to BambooHR and reports its health.
"""

import os
import requests
from services.base import ServiceBase

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")


class BambooService(ServiceBase):
    name = "bamboo_service"
    description = "Maintains and monitors the BambooHR API connection."

    def __init__(self):
        super().__init__()
        self._employee_count: int = 0
        self._last_check: str = None

    def _on_start(self) -> None:
        result = self._ping()
        if not result["connected"]:
            raise RuntimeError(f"Cannot connect to BambooHR: {result.get('error')}")
        self._employee_count = result.get("employeeCount", 0)

    def _check_alive(self) -> bool:
        return self._ping()["connected"]

    def _ping(self) -> dict:
        try:
            url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/directory"
            r = requests.get(
                url, auth=(BAMBOO_API_KEY, "x"),
                headers={"Accept": "application/json"}, timeout=10,
            )
            if r.ok:
                count = len(r.json().get("employees", []))
                self._employee_count = count
                return {"connected": True, "employeeCount": count}
            return {"connected": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    def _health_details(self) -> dict:
        return {"employeeCount": self._employee_count}

    def _get_metrics(self) -> dict:
        return {"employeeCount": self._employee_count}
