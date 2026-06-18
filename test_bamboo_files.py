import requests
import os
from dotenv import load_dotenv

load_dotenv()

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

# Buscar empleado
url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/reports/custom"
payload = {"title": "test", "fields": ["firstName", "lastName", "employeeNumber"]}
response = requests.post(url, auth=(BAMBOO_API_KEY, "x"), 
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    json=payload)

employees = response.json().get("employees", [])

# Buscar Koen Green (próximo ingreso)
for emp in employees:
    if "koen" in emp.get("firstName", "").lower() or "green" in emp.get("lastName", "").lower():
        emp_id = emp.get("id") or emp.get("employeeNumber")
        print(f"Encontrado: {emp.get('firstName')} {emp.get('lastName')} - ID: {emp_id}")
        print(f"Datos: {emp}")
        
        # Intentar obtener archivos
        files_url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{emp_id}/files/view/"
        r = requests.get(files_url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"})
        print(f"\nArchivos status: {r.status_code}")
        if r.ok:
            print(r.json())
        else:
            print(r.text[:500])
