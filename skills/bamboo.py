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
    "firstName", "lastName", "preferredName", "jobTitle", "department", "hireDate",
    "workEmail", "mobilePhone", "supervisor", "supervisorEId", "location", "division",
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


def get_job_info_table(employee_id: str) -> list:
    """
    "Job Information" (manager/reportsTo, title, department, division,
    location) is a BambooHR TABLE with effective-dated rows — the standard
    employee/report endpoints only return whichever row is effective TODAY.
    For a hire who hasn't started yet, that row is dated in the future, so
    those endpoints silently return null for all of it, even though the
    data is right there in BambooHR's UI. This reads the full table
    (all rows, any date) instead.
    """
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/tables/jobInfo"
    try:
        r = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}, timeout=15)
        return r.json() if r.ok else []
    except Exception:
        return []


def find_employee_id_by_name(full_name: str) -> str:
    """
    Matches a 'Firstname Lastname' display string — like the jobInfo
    table's reportsTo value — back to an employee ID via the directory.
    """
    if not full_name:
        return ""
    target = full_name.strip().lower()
    for e in _bamboo_report(["firstName", "lastName"]):
        candidate = f"{e.get('firstName', '')} {e.get('lastName', '')}".strip().lower()
        if candidate == target:
            return str(e.get("id", ""))
    return ""


def resolve_manager_first_name(supervisor_eid: str, fallback_display_name: str = "") -> str:
    """
    Resolves the manager's actual preferred (or first) name for greetings.
    Prefers a real lookup by employee ID over parsing the display string —
    BambooHR's legacy "supervisor" field comes as "Lastname, Firstname",
    while the "Reporting to" table field comes as "Firstname Lastname" —
    naively splitting either one the wrong way gives the LASTNAME, not a
    usable greeting name.
    """
    if not supervisor_eid and fallback_display_name:
        supervisor_eid = find_employee_id_by_name(fallback_display_name)

    if supervisor_eid:
        details = get_employee_details(supervisor_eid)
        name = details.get("preferredName") or details.get("firstName")
        if name:
            return name

    if fallback_display_name:
        if "," in fallback_display_name:
            _, _, first_part = fallback_display_name.partition(",")
            first_part = first_part.strip()
            if first_part:
                return first_part
        return fallback_display_name.split()[0]

    return ""


def get_employee_photo(employee_id: str) -> bytes | None:
    """
    Uses BambooHR's dedicated employee-photo endpoint — NOT the generic
    "Employee Uploads" file category, which is a grab-bag that can contain
    ID/passport scans, contracts, and other onboarding paperwork alongside
    an actual profile photo. Grabbing "the first image file" out of that
    folder previously picked up a passport scan for at least one employee
    (whatever file sorts first, by name, in that category) — this endpoint
    is BambooHR's own concept of "this person's profile photo" and nothing
    else can end up there.
    """
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/photo/original"
        r = requests.get(url, auth=(BAMBOO_API_KEY, "x"), timeout=15)
        return r.content if r.ok and r.content else None
    except Exception:
        return None


def update_employee_fields(employee_id: str, fields: dict) -> tuple[bool, str]:
    """
    Updates one or more fields on an employee record (e.g. {"preferredName": "Dan Giraldo"}).
    Returns (success, message) rather than raising, so bulk-update callers can
    keep going past individual failures and report a clean per-row result.

    "Connection: close" is required here: BambooHR's write endpoint (fronted by
    an APISIX gateway) silently 401s every request after the first one sent
    over a kept-alive connection — reads are unaffected, only POSTs. Forcing a
    fresh TCP connection per call avoids it.
    """
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}"
    try:
        r = requests.post(
            url, auth=(BAMBOO_API_KEY, "x"),
            headers={"Accept": "application/json", "Content-Type": "application/json", "Connection": "close"},
            json=fields, timeout=15,
        )
        if r.ok:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, str(e)


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
