import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

print(f"Subdomain: {BAMBOO_SUBDOMAIN}")
print(f"API Key: {BAMBOO_API_KEY[:10]}..." if BAMBOO_API_KEY else "NO API KEY")

url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/directory"
data = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}).json()

today = datetime.today()
print(f"\nHoy: {today.strftime('%Y-%m-%d')}")
print(f"\nFechas de todos los empleados:\n")

for emp in data["employees"]:
    hire = emp.get("hireDate", "")
    name = f"{emp.get('firstName','')} {emp.get('lastName','')}".strip()
    if hire:
        try:
            hire_date = datetime.strptime(hire, "%Y-%m-%d")
            diff = (hire_date - today).days
            print(f"{name} — {hire} — {diff} días desde hoy")
        except:
            print(f"{name} — {hire} — formato desconocido")
