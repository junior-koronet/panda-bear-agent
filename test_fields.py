import requests
import os
from dotenv import load_dotenv

load_dotenv()

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

# Obtener todos los campos disponibles en BambooHR
url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/reports/custom"

payload = {
    "title": "AllFields",
    "fields": [
        "firstName", "lastName", "jobTitle", "department", "hireDate",
        "workEmail", "mobilePhone", "homePhone", "workPhone",
        "supervisor", "supervisorEId", "location", "division",
        "employmentHistoryStatus", "employeeNumber", "country",
        "state", "city", "address1", "linkedin", "gender",
        "nationality", "maritalStatus", "exempt"
    ]
}

response = requests.post(
    url,
    auth=(BAMBOO_API_KEY, "x"),
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    json=payload,
    timeout=15,
)

print(f"Status: {response.status_code}")
data = response.json()
employees = data.get("employees", [])

# Mostrar campos del primer empleado con datos
print("\nCampos disponibles con datos reales:\n")
for emp in employees[:5]:
    name = f"{emp.get('firstName','')} {emp.get('lastName','')}".strip()
    print(f"👤 {name}")
    for k, v in emp.items():
        if v and v != "None" and v != "":
            print(f"   {k}: {v}")
    print()
