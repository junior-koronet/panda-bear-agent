import requests
import os
from dotenv import load_dotenv

load_dotenv()

BAMBOO_API_KEY = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

# Ver qué campos trae el directorio
url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/directory"
data = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}).json()

print("Campos disponibles en el directorio:")
if data.get("employees"):
    emp = data["employees"][0]
    for k, v in emp.items():
        print(f"  {k}: {v}")
    
    print(f"\nTotal empleados: {len(data['employees'])}")
    print("\nPrimeros 5 empleados con sus campos:")
    for e in data["employees"][:5]:
        print(f"  {e.get('firstName')} {e.get('lastName')} - hireDate: {e.get('hireDate')} - department: {e.get('department')}")
