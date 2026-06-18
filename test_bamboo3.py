import requests
import os
from dotenv import load_dotenv

load_dotenv()

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

# Endpoint de reporte con campos específicos
url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/reports/custom"

payload = {
    "title": "Hire Dates",
    "fields": ["firstName", "lastName", "hireDate", "jobTitle", "department", "workEmail", "supervisorEId"]
}

response = requests.post(
    url,
    auth=(BAMBOO_API_KEY, "x"),
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    json=payload
)

print(f"Status: {response.status_code}")
data = response.json()

employees = data.get("employees", [])
print(f"Total: {len(employees)}")
print("\nPrimeros 10 con hireDate:")
for emp in employees[:10]:
    print(f"  {emp.get('firstName')} {emp.get('lastName')} — hireDate: {emp.get('hireDate')}")
