"""
Skill: Read Bamboo / Detect Employees
Fetches employee data from BambooHR and detects upcoming hires.
"""

import os
import requests
from datetime import datetime
from skills.base import Skill

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

ALL_FIELDS = [
    "firstName", "lastName", "jobTitle", "department", "hireDate",
    "workEmail", "mobilePhone", "supervisor", "location", "division",
    "employmentHistoryStatus", "employeeNumber", "country", "state",
    "city", "gender", "nationality",
]


def _bamboo_get(endpoint: str) -> dict | None:
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/{endpoint}"
    try:
        r = requests.get(
            url, auth=(BAMBOO_API_KEY, "x"),
            headers={"Accept": "application/json"}, timeout=15,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def _bamboo_report(fields: list = None) -> list:
    fields = fields or ALL_FIELDS
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/reports/custom"
    try:
        r = requests.post(
            url, auth=(BAMBOO_API_KEY, "x"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"title": "PandaBearReport", "fields": fields},
            timeout=15,
        )
        return r.json().get("employees", []) if r.ok else []
    except Exception:
        return []


def get_employee_details(employee_id: str) -> dict:
    fields = ",".join(ALL_FIELDS)
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}?fields={fields}"
    try:
        r = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}, timeout=15)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def get_employee_photo(employee_id: str) -> bytes | None:
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/files/view/"
        r = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}, timeout=15)
        if not r.ok:
            return None
        categories = r.json().get("categories", [])
        photo_file_id = None
        for cat in categories:
            if cat.get("name") == "Employee Uploads":
                for f in cat.get("files", []):
                    if f.get("name", "").lower().endswith((".png", ".jpg", ".jpeg")):
                        photo_file_id = f.get("id")
                        break
        if not photo_file_id:
            return None
        dl_url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/files/{photo_file_id}/"
        dl = requests.get(dl_url, auth=(BAMBOO_API_KEY, "x"), timeout=15)
        return dl.content if dl.ok else None
    except Exception:
        return None


class ReadBambooSkill(Skill):
    name = "read_bamboo"
    description = "Reads employee directory from BambooHR and returns formatted data."
    category = "data"

    def _run(self, query: str = "directory") -> dict:
        if query == "directory":
            data = _bamboo_get("employees/directory")
            if not data:
                return {"success": False, "error": "Could not reach BambooHR"}
            employees = data.get("employees", [])
            return {
                "result": employees,
                "count": len(employees),
                "decision": f"Retrieved {len(employees)} employees from BambooHR directory",
                "reasoning": "Fetched full directory for overview.",
            }
        return {"success": False, "error": f"Unknown query: {query}"}


class DetectEmployeesSkill(Skill):
    """
    Detects employees with upcoming hire dates from BambooHR.
    This is the core detection loop — G1.
    """
    name = "detect_employees"
    description = "Detects future employees from BambooHR within a lookback window."
    category = "detection"

    def _run(self, lookback_days: int = 60, already_processed: set = None) -> dict:
        already_processed = already_processed or set()
        employees = _bamboo_report()
        if not employees:
            return {
                "success": False,
                "error": "Could not connect to BambooHR or no employees returned.",
            }

        today = datetime.today()
        upcoming = []

        for emp in employees:
            hire_str = emp.get("hireDate", "")
            if not hire_str:
                continue
            try:
                hire_date = datetime.strptime(hire_str, "%Y-%m-%d")
                diff = (hire_date - today).days
                if 0 < diff <= lookback_days:
                    emp_number = str(emp.get("employeeNumber", ""))
                    if emp_number not in already_processed:
                        upcoming.append((emp, hire_date))
            except ValueError:
                continue

        upcoming.sort(key=lambda x: x[1])

        return {
            "result": upcoming,
            "count": len(upcoming),
            "scanned": len(employees),
            "decision": f"Detected {len(upcoming)} new employee(s) with upcoming hire dates",
            "reasoning": (
                f"Scanned {len(employees)} employees. Found {len(upcoming)} with hire dates "
                f"within the next {lookback_days} days that haven't been processed yet."
            ),
            "confidence": 1.0 if employees else 0.0,
        }
