"""
╔══════════════════════════════════════════════════════════════╗
║           🐼 PANDA BEAR AGENT — Koronet HR                  ║
║   BambooHR + Groq AI + Slack + FastAPI + SQLite + Images    ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import io
import threading
import sqlite3
import requests
import uvicorn
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import numpy as np

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import secrets
from pydantic import BaseModel

from groq import Groq
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────
SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN  = os.getenv("SLACK_APP_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
BAMBOO_API_KEY   = os.getenv("BAMBOO_API_KEY")
BAMBOO_SUBDOMAIN = os.getenv("BAMBOO_SUBDOMAIN", "koronet")

ONBOARDING_GUIDE_URL = "https://docs.google.com/document/d/1xa56Rg1uNwtPuy-eYHvqdfOKPAEKZ_fji8y0cRrQyc8/edit?tab=t.0"
BIENVENIDAS_CHANNEL  = "#Koronet"
CC_EMAIL             = "moira.gago@koronet.com"

# Paths locales
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "template.png")
FONT_NAME     = os.path.join(BASE_DIR, "Poppins-Light.ttf")
FONT_CARGO    = os.path.join(BASE_DIR, "Poppins-Regular.ttf")
IMAGES_DIR    = os.path.join(BASE_DIR, "generated_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

slack_client = WebClient(token=SLACK_BOT_TOKEN)
groq_client  = Groq(api_key=GROQ_API_KEY)
conversations: dict[str, list] = {}

ALL_FIELDS = [
    "firstName", "lastName", "jobTitle", "department", "hireDate",
    "workEmail", "mobilePhone", "supervisor", "location", "division",
    "employmentHistoryStatus", "employeeNumber", "country", "state",
    "city", "gender", "nationality", "maritalStatus"
]

# ─── LANGUAGE & TIMEZONE RULES ────────────────────────────────

SPANISH_COUNTRIES = [
    "colombia", "mexico", "méxico", "argentina", "chile", "peru", "perú",
    "ecuador", "uruguay", "paraguay", "bolivia", "venezuela", "costa rica",
    "panama", "panamá", "guatemala", "honduras", "nicaragua", "el salvador",
    "república dominicana", "cuba", "puerto rico"
]

SPAIN_KEYWORDS = ["spain", "españa", "espana"]


def get_language_and_time(location: str, country: str) -> dict:
    loc = (location or "").lower()
    ctry = (country or "").lower()
    combined = loc + " " + ctry
    if any(s in combined for s in SPAIN_KEYWORDS):
        return {"lang": "es", "time": "7:00 AM Colombia (1:00 PM España)", "cc": None}
    elif any(s in combined for s in SPANISH_COUNTRIES):
        return {"lang": "es", "time": "8:00 AM Colombia", "cc": None}
    else:
        return {"lang": "en", "time": "8:30 AM Colombia", "cc": CC_EMAIL}


# ─── BUSINESS DAY RULES ───────────────────────────────────────

def get_manager_send_date(hire_date: datetime) -> datetime:
    weekday = hire_date.weekday()
    if weekday == 0 or weekday == 1:
        days_back = weekday + 4
    elif weekday == 2:
        days_back = 5
    elif weekday == 3:
        days_back = 3
    elif weekday == 4:
        days_back = 3
    else:
        days_back = 4
    return hire_date - timedelta(days=days_back)


# ─── IMAGE GENERATION ─────────────────────────────────────────

def generate_welcome_image(name: str, job_title: str = "", photo_bytes: bytes = None) -> str:
    name = name or ""
    job_title = job_title or ""
    """Genera la imagen de bienvenida con nombre, cargo y foto."""
    
    # Parámetros del círculo (calibrados)
    CX, CY, R = 707, 1500, 600

    # Cargar plantilla
    template = Image.open(TEMPLATE_PATH).convert('RGBA')
    W, H = template.size

    # Hacer el círculo transparente en la plantilla
    tm_arr = np.array(template)
    for y in range(max(0, CY-R-5), min(CY+R+5, H)):
        for x in range(max(0, CX-R-5), min(CX+R+5, W)):
            if ((x-CX)**2 + (y-CY)**2)**0.5 <= R:
                tm_arr[y, x, 3] = 0
    template_holed = Image.fromarray(tm_arr, 'RGBA')

    # Base negra
    base = Image.new('RGBA', (W, H), (0, 0, 0, 255))

    # Foto en círculo
    if photo_bytes:
        try:
            photo = Image.open(io.BytesIO(photo_bytes)).convert('RGBA')
            pw, ph = photo.size
            s = min(pw, ph)
            photo_sq = photo.crop(((pw-s)//2, (ph-s)//2, (pw+s)//2, (ph+s)//2))
            D = R * 2
            photo_r = photo_sq.resize((D, D), Image.LANCZOS)
            mask = Image.new('L', (D, D), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, D-1, D-1), fill=255)
            photo_r.putalpha(mask)
            base.paste(photo_r, (CX-R, CY-R), photo_r)
        except Exception as e:
            print(f"[Image] Error procesando foto: {e}")

    # Pegar plantilla encima
    base.paste(template_holed, (0, 0), template_holed)

    # Texto
    draw = ImageDraw.Draw(base)
    fn = ImageFont.truetype(FONT_NAME, 82)
    fc = ImageFont.truetype(FONT_CARGO, 58)

    # Nombre centrado
    bbox = draw.textbbox((0, 0), name, font=fn)
    draw.text(((W-(bbox[2]-bbox[0]))//2, 264), name, font=fn, fill="white")

    # Cargo centrado
    bbox2 = draw.textbbox((0, 0), job_title, font=fc)
    draw.text(((W-(bbox2[2]-bbox2[0]))//2, 468), job_title, font=fc, fill="white")

    # Guardar
    safe_name = name.replace(" ", "_").lower()
    output_path = os.path.join(IMAGES_DIR, f"welcome_{safe_name}.png")
    base.convert('RGB').save(output_path, quality=95)
    print(f"[Image] ✅ Imagen generada: {output_path}")
    return output_path


# ─── BAMBOOHR HELPERS ─────────────────────────────────────────

def bamboo_get(endpoint: str):
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/{endpoint}"
    response = requests.get(
        url, auth=(BAMBOO_API_KEY, "x"),
        headers={"Accept": "application/json"}, timeout=15,
    )
    if response.ok:
        return response.json()
    return None


def bamboo_report(fields: list = None) -> list:
    if fields is None:
        fields = ALL_FIELDS
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/reports/custom"
    payload = {"title": "PandaBearReport", "fields": fields}
    response = requests.post(
        url, auth=(BAMBOO_API_KEY, "x"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload, timeout=15,
    )
    if response.ok:
        return response.json().get("employees", [])
    return []


def get_employee_photo(employee_id: str) -> bytes:
    """Descarga la foto del empleado desde Employee Uploads en BambooHR."""
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/files/view/"
        r = requests.get(url, auth=(BAMBOO_API_KEY, "x"), headers={"Accept": "application/json"}, timeout=15)
        if not r.ok:
            return None

        data = r.json()
        categories = data.get("categories", [])

        # Buscar en Employee Uploads
        photo_file_id = None
        for cat in categories:
            if cat.get("name") == "Employee Uploads":
                for f in cat.get("files", []):
                    fname = f.get("name", "").lower()
                    if fname.endswith(('.png', '.jpg', '.jpeg')):
                        photo_file_id = f.get("id")
                        break

        if not photo_file_id:
            print(f"[BambooHR] No hay foto en Employee Uploads para empleado {employee_id}")
            return None

        # Descargar el archivo
        dl_url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUBDOMAIN}/v1/employees/{employee_id}/files/{photo_file_id}/"
        dl = requests.get(dl_url, auth=(BAMBOO_API_KEY, "x"), timeout=15)
        if dl.ok:
            print(f"[BambooHR] ✅ Foto descargada para empleado {employee_id}")
            return dl.content
        return None

    except Exception as e:
        print(f"[BambooHR] Error descargando foto: {e}")
        return None


def format_employee(emp: dict) -> str:
    name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
    lines = [f"👤 *{name}*"]
    if emp.get("jobTitle"):       lines.append(f"  💼 Cargo: {emp['jobTitle']}")
    if emp.get("department"):     lines.append(f"  🏢 Departamento: {emp['department']}")
    if emp.get("division"):       lines.append(f"  📂 División: {emp['division']}")
    if emp.get("supervisor"):     lines.append(f"  👔 Manager: {emp['supervisor']}")
    if emp.get("workEmail"):      lines.append(f"  📧 Email: {emp['workEmail']}")
    if emp.get("mobilePhone"):    lines.append(f"  📱 Teléfono: {emp['mobilePhone']}")
    if emp.get("location"):       lines.append(f"  📍 Ubicación: {emp['location']}")
    if emp.get("city") and emp.get("country"):
        lines.append(f"  🌍 Ciudad: {emp['city']}, {emp['country']}")
    if emp.get("hireDate"):
        try:
            hd = datetime.strptime(emp["hireDate"], "%Y-%m-%d")
            lines.append(f"  📅 Fecha de inicio: {hd.strftime('%d/%m/%Y')}")
        except:
            lines.append(f"  📅 Fecha de inicio: {emp['hireDate']}")
    if emp.get("employmentHistoryStatus"):
        lines.append(f"  📋 Tipo: {emp['employmentHistoryStatus']}")
    if emp.get("employeeNumber"):
        lines.append(f"  🔢 N° Empleado: {emp['employeeNumber']}")
    return "\n".join(lines)


def get_employee_directory() -> str:
    data = bamboo_get("employees/directory")
    if not data or "employees" not in data:
        return "No pude obtener el directorio."
    employees = data["employees"]
    lines = [f"📋 *Directorio de BambooHR* ({len(employees)} empleados):\n"]
    for emp in employees:
        name  = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
        dept  = emp.get("department", "Sin departamento")
        title = emp.get("jobTitle", "Sin cargo")
        lines.append(f"• {name} — {title} ({dept})")
    return "\n".join(lines)


def get_employee_by_name(name: str) -> str:
    employees = bamboo_report()
    if not employees:
        return "No pude conectarme a BambooHR."
    name_lower = name.lower()
    matches = [
        emp for emp in employees
        if name_lower in f"{emp.get('firstName','')} {emp.get('lastName','')}".lower()
    ]
    if not matches:
        return f"No encontré ningún empleado con el nombre '{name}'."
    return "\n\n".join([format_employee(emp) for emp in matches])


def get_new_hires(days: int = 60) -> str:
    employees = bamboo_report()
    if not employees:
        return "No pude conectarme a BambooHR."
    today = datetime.today()
    new_hires = []
    for emp in employees:
        hire_date_str = emp.get("hireDate", "")
        if hire_date_str:
            try:
                hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d")
                if 0 <= (today - hire_date).days <= days:
                    new_hires.append((emp, hire_date))
            except ValueError:
                pass
    if not new_hires:
        return f"No hay ingresos nuevos en los últimos {days} días."
    new_hires.sort(key=lambda x: x[1], reverse=True)
    lines = [f"🆕 *Ingresos recientes* (últimos {days} días):\n"]
    for emp, hd in new_hires:
        name = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
        lines.append(
            f"• *{name}* — {emp.get('jobTitle', 'N/A')} ({emp.get('department', 'N/A')}) "
            f"| 📍{emp.get('location', 'N/A')} | 👔 {emp.get('supervisor', 'N/A')} "
            f"| Ingresó el {hd.strftime('%d/%m/%Y')}"
        )
    return "\n".join(lines)


def get_upcoming_hires(days: int = 60) -> str:
    employees = bamboo_report()
    if not employees:
        return "No pude conectarme a BambooHR."
    today = datetime.today()
    upcoming = []
    for emp in employees:
        hire_date_str = emp.get("hireDate", "")
        if hire_date_str:
            try:
                hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d")
                diff = (hire_date - today).days
                if 0 < diff <= days:
                    upcoming.append((emp, hire_date))
            except ValueError:
                pass
    if not upcoming:
        return f"No hay próximos ingresos en los siguientes {days} días."
    upcoming.sort(key=lambda x: x[1])
    lines = [f"📅 *Próximos ingresos* (siguientes {days} días):\n"]
    for emp, hd in upcoming:
        lines.append(format_employee(emp))
        lines.append(f"  ⏳ Inicia en {(hd - today).days} días — {hd.strftime('%d/%m/%Y')}\n")
    return "\n".join(lines)


def get_time_off_today() -> str:
    today_str = datetime.today().strftime("%Y-%m-%d")
    data = bamboo_get(f"time_off/whos_out/?start={today_str}&end={today_str}")
    if data is None:
        return "No pude obtener información de ausencias."
    if not data:
        return "✅ Hoy no hay nadie ausente según BambooHR."
    lines = ["🏖️ *Ausencias de hoy*:\n"]
    for item in data:
        lines.append(f"• {item.get('name', 'Desconocido')} — {item.get('type', {}).get('name', 'Ausencia')}")
    return "\n".join(lines)


# ─── MESSAGE TEMPLATES ────────────────────────────────────────

def build_manager_message(manager_name: str, employee_name: str, lang: str) -> str:
    first_name = manager_name.split(",")[0].strip() if manager_name else "there"
    if lang == "es":
        return (
            f"Hola {first_name}! Dado que falta poquito para que {employee_name} ingrese, "
            f"te comparto la siguiente Guía de On Boarding!!!\n\n"
            f"{ONBOARDING_GUIDE_URL}\n\n"
            f"Cualquier cosa que necesites, ¡estamos a disposición! 🐼"
        )
    else:
        return (
            f"Hi {first_name}! Since {employee_name} is starting soon, "
            f"I would like you to have the On Boarding Guide with some tips!\n\n"
            f"{ONBOARDING_GUIDE_URL}\n\n"
            f"Let me know if there is anything I can help you with!! 🐼"
        )


def build_bienvenidas_message(first_name: str, last_name: str, job_title: str) -> str:
    return (
        f"Hi team! @here A huge welcome to @{first_name} {last_name}, "
        f"joining as {job_title}. We are so excited to have you with us! 🎉"
    )


def build_employee_email(first_name: str, hire_date: datetime, lang: str, onboarding_time: str) -> str:
    date_str = hire_date.strftime("%B %d, %Y") if lang == "en" else hire_date.strftime("%d/%m/%Y")
    if lang == "es":
        return (
            f"Hola {first_name}! Todo bien, ¿y tú?\n\n"
            f"Gracias por escribir. Confirmo que tu primer día es el {date_str}. ¡Ya falta poco!\n\n"
            f"En los próximos días estarás recibiendo un correo de Jeisson, nuestro encargado de IT, "
            f"con las instrucciones para configurar tu correo corporativo y una guía de configuración "
            f"de herramientas. Una vez tengas acceso a ese correo, desde allí te estaremos enviando "
            f"la citación formal para tu sesión de onboarding, que será aproximadamente a las "
            f"{onboarding_time}.\n\n"
            f"Haremos todo lo posible para que todo fluya sin inconvenientes. "
            f"Cualquier duda, aquí estamos.\n\n"
            f"Un abrazo,\nJuni"
        )
    else:
        return (
            f"Hi {first_name}! Hope you're doing well!\n\n"
            f"Just wanted to confirm that your first day is {date_str}. "
            f"We're so excited to have you on board!\n\n"
            f"In the coming days, you'll be receiving an email from Jeisson, our IT coordinator, "
            f"with instructions to set up your corporate email and a tools configuration guide. "
            f"Once you have access to that email, we'll send you the formal invitation for your "
            f"onboarding session, which will take place at approximately {onboarding_time}.\n\n"
            f"We'll do our best to make everything run smoothly. "
            f"If you have any questions, don't hesitate to reach out!\n\n"
            f"Best,\nJuni"
        )


# ─── DATABASE ─────────────────────────────────────────────────

DB_PATH = os.path.join(BASE_DIR, "panda_bear.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            startedAt TEXT NOT NULL,
            completedAt TEXT,
            status TEXT DEFAULT 'running',
            employeesScanned INTEGER DEFAULT 0,
            newEmployees INTEGER DEFAULT 0,
            messagesSent INTEGER DEFAULT 0,
            messagesFailed INTEGER DEFAULT 0,
            dryRun INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            runId INTEGER,
            employeeId TEXT,
            employeeName TEXT,
            employeeJobTitle TEXT,
            employeeHireDate TEXT,
            employeeEmail TEXT,
            employeeLocation TEXT,
            managerName TEXT,
            messageType TEXT DEFAULT 'employee_email',
            messageContent TEXT,
            language TEXT DEFAULT 'es',
            imagePath TEXT,
            status TEXT DEFAULT 'pending_approval',
            createdAt TEXT NOT NULL,
            sentAt TEXT,
            batchId INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            createdAt TEXT NOT NULL,
            status TEXT DEFAULT 'pending_approval',
            messageCount INTEGER DEFAULT 0,
            approvedAt TEXT,
            rejectedAt TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("✅ Base de datos SQLite inicializada")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── SYNC LOGIC ───────────────────────────────────────────────

def run_sync(dry_run: bool = False, lookback_days: int = 60) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO runs (startedAt, status, dryRun) VALUES (?, 'running', ?)",
              (datetime.now().isoformat(), 1 if dry_run else 0))
    run_id = c.lastrowid
    conn.commit()

    try:
        employees = bamboo_report()
        today = datetime.today()
        new_employees = []

        for emp in employees:
            hire_date_str = emp.get("hireDate", "")
            if hire_date_str:
                try:
                    hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d")
                    diff = (hire_date - today).days
                    if 0 < diff <= lookback_days:
                        emp_id = str(emp.get("employeeNumber", ""))
                        existing = c.execute(
                            "SELECT id FROM messages WHERE employeeId = ?", (emp_id,)
                        ).fetchone()
                        if not existing:
                            new_employees.append((emp, hire_date))
                except ValueError:
                    pass

        batch_id = None
        messages_created = 0

        if new_employees and not dry_run:
            total_msgs = len(new_employees) * 3
            c.execute("INSERT INTO batches (createdAt, messageCount) VALUES (?, ?)",
                      (datetime.now().isoformat(), total_msgs))
            batch_id = c.lastrowid
            conn.commit()

            for emp, hire_date in new_employees:
                emp_name  = f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip()
                emp_id    = str(emp.get("employeeNumber", ""))
                bamboo_id = str(emp.get("id", emp_id))
                location  = emp.get("location", "")
                country   = emp.get("country", "")
                manager   = emp.get("supervisor", "")

                lang_info = get_language_and_time(location, country)
                lang      = lang_info["lang"]
                time_str  = lang_info["time"]

                # Intentar descargar foto
                photo_bytes = get_employee_photo(bamboo_id)
                image_path  = None

                if photo_bytes:
                    image_path = generate_welcome_image(
                        emp_name, emp.get("jobTitle", ""), photo_bytes
                    )
                else:
                    print(f"[Sync] ⚠️ Sin foto para {emp_name} — imagen sin foto")
                    image_path = generate_welcome_image(emp_name, emp.get("jobTitle", ""), None)

                # 1. Correo al empleado
                email_content = build_employee_email(emp.get("firstName", ""), hire_date, lang, time_str)
                c.execute("""
                    INSERT INTO messages
                    (runId, employeeId, employeeName, employeeJobTitle, employeeHireDate,
                     employeeEmail, employeeLocation, managerName, messageType, messageContent,
                     language, imagePath, status, createdAt, batchId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'employee_email', ?, ?, ?, 'pending_approval', ?, ?)
                """, (run_id, emp_id, emp_name, emp.get("jobTitle", ""),
                      hire_date.strftime("%Y-%m-%d"), emp.get("workEmail", ""),
                      location, manager, email_content, lang, image_path,
                      datetime.now().isoformat(), batch_id))
                messages_created += 1

                # 2. Mensaje al manager
                manager_content = build_manager_message(manager, emp_name, lang)
                c.execute("""
                    INSERT INTO messages
                    (runId, employeeId, employeeName, employeeJobTitle, employeeHireDate,
                     employeeEmail, employeeLocation, managerName, messageType, messageContent,
                     language, status, createdAt, batchId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manager_slack', ?, ?, 'pending_approval', ?, ?)
                """, (run_id, emp_id+"_mgr", emp_name, emp.get("jobTitle", ""),
                      hire_date.strftime("%Y-%m-%d"), emp.get("workEmail", ""),
                      location, manager, manager_content, lang,
                      datetime.now().isoformat(), batch_id))
                messages_created += 1

                # 3. Mensaje en #bienvenidas
                bienvenidas_content = build_bienvenidas_message(
                    emp.get("firstName", ""), emp.get("lastName", ""),
                    emp.get("jobTitle", "New Team Member")
                )
                c.execute("""
                    INSERT INTO messages
                    (runId, employeeId, employeeName, employeeJobTitle, employeeHireDate,
                     employeeEmail, employeeLocation, managerName, messageType, messageContent,
                     language, imagePath, status, createdAt, batchId)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bienvenidas_slack', ?, 'en', ?, 'pending_approval', ?, ?)
                """, (run_id, emp_id+"_bvd", emp_name, emp.get("jobTitle", ""),
                      hire_date.strftime("%Y-%m-%d"), emp.get("workEmail", ""),
                      location, manager, bienvenidas_content, image_path,
                      datetime.now().isoformat(), batch_id))
                messages_created += 1

            conn.commit()
            notify_approver(batch_id, new_employees)

        c.execute("""
            UPDATE runs SET completedAt=?, status='completed',
            employeesScanned=?, newEmployees=?, messagesSent=? WHERE id=?
        """, (datetime.now().isoformat(), len(employees), len(new_employees), messages_created, run_id))
        conn.commit()

        return {
            "runId": run_id, "employeesScanned": len(employees),
            "newEmployees": len(new_employees), "messagesSent": messages_created,
            "dryRun": dry_run, "batchId": batch_id
        }

    except Exception as e:
        c.execute("UPDATE runs SET status='failed', completedAt=? WHERE id=?",
                  (datetime.now().isoformat(), run_id))
        conn.commit()
        raise e
    finally:
        conn.close()


def notify_approver(batch_id: int, new_employees: list):
    try:
        names = [f"{e[0].get('firstName','')} {e[0].get('lastName','')}" for e in new_employees]
        send_dates = [get_manager_send_date(e[1]).strftime('%d/%m/%Y') for e in new_employees]
        text = (
            f"🐼 *Panda Bear Agent* — ¡Nuevos ingresos detectados!\n\n"
            f"Encontré *{len(new_employees)}* nuevo(s) empleado(s):\n"
            + "\n".join([f"• {n} (msg manager: {d})" for n, d in zip(names, send_dates)])
            + f"\n\nBatch #{batch_id} — *{len(new_employees) * 3} mensajes* listos.\n"
            f"👉 Abre el dashboard en http://localhost:8000 para aprobar."
        )
        users = slack_client.users_list()
        juni_id = None
        for user in users["members"]:
            if "junior" in user.get("name", "").lower() or "juni" in user.get("real_name", "").lower():
                juni_id = user["id"]
                break
        if juni_id:
            dm = slack_client.conversations_open(users=[juni_id])
            channel = dm["channel"]["id"]
            slack_client.chat_postMessage(channel=channel, text=text)
    except Exception as e:
        print(f"[Notify] Error: {e}")


# ─── FASTAPI ──────────────────────────────────────────────────

app = FastAPI(title="Panda Bear Agent API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

security = HTTPBasic()

DASHBOARD_USER     = os.getenv("DASHBOARD_USER", "junior@koronet.com")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "koronet2024")

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    correct_pass = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.get("/")
def serve_dashboard(user: str = Depends(verify_credentials)):
    dashboard_path = os.path.join(BASE_DIR, "panda-bear-agent-v3.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return {"message": "Panda Bear Agent API running 🐼"}


class SyncRequest(BaseModel):
    dryRun: bool = False
    lookbackDays: int = 60


class EditRequest(BaseModel):
    content: Optional[str] = None
    feedback: Optional[str] = None


@app.get("/api/agent/stats")
def get_stats(user: str = Depends(verify_credentials)):
    conn = get_db()
    c = conn.cursor()
    total_sent   = c.execute("SELECT COUNT(*) FROM messages WHERE status='sent'").fetchone()[0]
    total_failed = c.execute("SELECT COUNT(*) FROM messages WHERE status='failed'").fetchone()[0]
    total_runs   = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    total_emp    = c.execute("SELECT COUNT(DISTINCT employeeId) FROM messages WHERE employeeId NOT LIKE '%_mgr' AND employeeId NOT LIKE '%_bvd'").fetchone()[0]
    recent       = c.execute("SELECT * FROM messages ORDER BY createdAt DESC LIMIT 10").fetchall()
    conn.close()
    return {
        "totalMessagesSent": total_sent, "totalMessagesFailed": total_failed,
        "totalRuns": total_runs, "totalEmployeesTracked": total_emp,
        "recentMessages": [dict(r) for r in recent]
    }


@app.get("/api/agent/integrations/status")
def get_integrations():
    bamboo_ok, slack_ok, groq_ok = False, False, False
    try: bamboo_ok = bamboo_get("employees/directory") is not None
    except: pass
    try: slack_client.auth_test(); slack_ok = True
    except: pass
    try:
        groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        groq_ok = True
    except: pass
    return {
        "bamboohr": {"connected": bamboo_ok, "detail": "145 empleados" if bamboo_ok else "Error"},
        "slack":    {"connected": slack_ok,   "detail": "Koronet" if slack_ok else "Error"},
        "anthropic":{"connected": groq_ok,    "detail": "Groq AI" if groq_ok else "Error"},
    }


@app.get("/api/agent/messages")
def get_messages(user: str = Depends(verify_credentials)):
    conn = get_db()
    msgs = conn.execute("SELECT * FROM messages ORDER BY createdAt DESC").fetchall()
    conn.close()
    return [dict(m) for m in msgs]


@app.get("/api/agent/runs")
def get_runs(user: str = Depends(verify_credentials)):
    conn = get_db()
    runs = conn.execute("SELECT * FROM runs ORDER BY startedAt DESC").fetchall()
    conn.close()
    return [dict(r) for r in runs]


@app.get("/api/agent/batches")
def get_batches(user: str = Depends(verify_credentials)):
    conn = get_db()
    batches = conn.execute(
        "SELECT * FROM batches WHERE status='pending_approval' ORDER BY createdAt DESC"
    ).fetchall()
    conn.close()
    return [dict(b) for b in batches]


@app.get("/api/agent/batches/{batch_id}")
def get_batch(batch_id: int):
    conn = get_db()
    batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    msgs = conn.execute("SELECT * FROM messages WHERE batchId=?", (batch_id,)).fetchall()
    conn.close()
    return {**dict(batch), "messages": [dict(m) for m in msgs]}


@app.post("/api/agent/batches/{batch_id}/approve")
def approve_batch(batch_id: int, user: str = Depends(verify_credentials)):
    conn = get_db()
    c = conn.cursor()
    msgs = c.execute(
        "SELECT * FROM messages WHERE batchId=? AND status='pending_approval'", (batch_id,)
    ).fetchall()
    sent = 0
    for msg in msgs:
        try:
            c.execute("UPDATE messages SET status='sent', sentAt=? WHERE id=?",
                      (datetime.now().isoformat(), msg["id"]))
            sent += 1
        except:
            c.execute("UPDATE messages SET status='failed' WHERE id=?", (msg["id"],))
    c.execute("UPDATE batches SET status='approved', approvedAt=? WHERE id=?",
              (datetime.now().isoformat(), batch_id))
    conn.commit()
    conn.close()
    return {"approved": True, "messagesSent": sent}


@app.post("/api/agent/batches/{batch_id}/reject")
def reject_batch(batch_id: int, user: str = Depends(verify_credentials)):
    conn = get_db()
    conn.execute("UPDATE batches SET status='rejected', rejectedAt=? WHERE id=?",
                 (datetime.now().isoformat(), batch_id))
    conn.execute("UPDATE messages SET status='rejected' WHERE batchId=?", (batch_id,))
    conn.commit()
    conn.close()
    return {"rejected": True}


@app.post("/api/agent/messages/{msg_id}/edit")
def edit_message(msg_id: int, body: EditRequest):
    conn = get_db()
    c = conn.cursor()
    msg = c.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if body.content:
        new_content = body.content
    elif body.feedback:
        prompt = f"Mensaje:\n\n{msg['messageContent']}\n\nCambio: {body.feedback}\n\nGenera el mensaje mejorado."
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400, temperature=0.7)
        new_content = response.choices[0].message.content
    else:
        raise HTTPException(status_code=400, detail="Provide content or feedback")
    c.execute("UPDATE messages SET messageContent=? WHERE id=?", (new_content, msg_id))
    conn.commit()
    conn.close()
    return {"updated": True, "newContent": new_content}


@app.post("/api/agent/messages/{msg_id}/skip")
def skip_message(msg_id: int, user: str = Depends(verify_credentials)):
    conn = get_db()
    conn.execute("UPDATE messages SET status='skipped' WHERE id=?", (msg_id,))
    conn.commit()
    conn.close()
    return {"skipped": True}


@app.post("/api/agent/messages/{msg_id}/resend")
def resend_message(msg_id: int, user: str = Depends(verify_credentials)):
    conn = get_db()
    conn.execute("UPDATE messages SET status='sent', sentAt=? WHERE id=?",
                 (datetime.now().isoformat(), msg_id))
    conn.commit()
    conn.close()
    return {"resent": True}


@app.post("/api/agent/messages/{msg_id}/test-dm")
def test_dm(msg_id: int, user: str = Depends(verify_credentials)):
    conn = get_db()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    conn.close()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        users = slack_client.users_list()
        juni_id = None
        for user in users["members"]:
            if "junior" in user.get("name", "").lower():
                juni_id = user["id"]
                break
        if juni_id:
            dm = slack_client.conversations_open(users=[juni_id])
            channel = dm["channel"]["id"]
            msg_type = msg["messageType"] or "message"
            
            # Si tiene imagen, enviarla también
            if msg["imagePath"] and os.path.exists(msg["imagePath"]):
                slack_client.files_upload_v2(
                    channel=channel,
                    file=msg["imagePath"],
                    initial_comment=f"🔔 *Preview [{msg_type}] para {msg['employeeName']}:*\n\n{msg['messageContent']}"
                )
            else:
                slack_client.chat_postMessage(
                    channel=channel,
                    text=f"🔔 *Preview [{msg_type}] para {msg['employeeName']}:*\n\n{msg['messageContent']}"
                )
            return {"delivered": True}
        return {"delivered": False, "error": "No se encontró a Juni"}
    except Exception as e:
        return {"delivered": False, "error": str(e)}


@app.post("/api/agent/sync")
def sync(body: SyncRequest, user: str = Depends(verify_credentials)):
    result = run_sync(dry_run=body.dryRun, lookback_days=body.lookbackDays)
    return result


@app.get("/api/agent/recent-hires")
def recent_hires(user: str = Depends(verify_credentials)):
    employees = bamboo_report()
    today = datetime.today()
    result = []
    conn = get_db()
    for emp in employees:
        hire_date_str = emp.get("hireDate", "")
        if hire_date_str:
            try:
                hire_date = datetime.strptime(hire_date_str, "%Y-%m-%d")
                diff = (hire_date - today).days
                if 0 < diff <= 60:
                    emp_id = str(emp.get("employeeNumber", ""))
                    existing = conn.execute(
                        "SELECT id FROM messages WHERE employeeId=?", (emp_id,)
                    ).fetchone()
                    lang_info = get_language_and_time(emp.get("location",""), emp.get("country",""))
                    send_date = get_manager_send_date(hire_date)
                    result.append({
                        "firstName": emp.get("firstName", ""),
                        "lastName":  emp.get("lastName", ""),
                        "jobTitle":  emp.get("jobTitle", ""),
                        "department": emp.get("department", ""),
                        "hireDate":  hire_date_str,
                        "supervisor": emp.get("supervisor", ""),
                        "workEmail":  emp.get("workEmail", ""),
                        "location":   emp.get("location", ""),
                        "language":   lang_info["lang"],
                        "managerMessageDate": send_date.strftime("%Y-%m-%d"),
                        "alreadyProcessed": existing is not None
                    })
            except ValueError:
                pass
    conn.close()
    return result


# ─── GROQ AI BRAIN ────────────────────────────────────────────

SYSTEM_PROMPT = """Eres Panda Bear 🐼, el agente de onboarding de Koronet HR.
Tu personalidad: amigable, profesional y con buen sentido del humor.

REGLA MÁS IMPORTANTE: NUNCA inventes nombres de empleados, fechas, departamentos 
ni ningún dato de personas. SOLO usa información que venga explícitamente en el 
contexto de BambooHR que se te proporciona.

Si no tienes datos reales de BambooHR, di honestamente que no tienes esa información.
Responde siempre en el mismo idioma que el usuario (español o inglés).
Sé concisa pero completa. Usa emojis con moderación."""


def ask_groq(user_id: str, user_message: str, bamboo_context: str = "") -> str:
    if user_id not in conversations:
        conversations[user_id] = []
    content = (
        f"[DATOS REALES DE BAMBOOHR]\n{bamboo_context}\n\n[PREGUNTA]\n{user_message}"
        if bamboo_context else user_message
    )
    conversations[user_id].append({"role": "user", "content": content})
    history = conversations[user_id][-20:]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages, max_tokens=1500, temperature=0.3,
    )
    reply = response.choices[0].message.content
    conversations[user_id].append({"role": "assistant", "content": reply})
    return reply


def route_message(user_id: str, text: str) -> str:
    text_lower = text.lower()
    bamboo_context = ""
    if any(w in text_lower for w in ["directorio", "directory", "lista de empleados", "todos los empleados", "cuántos empleados"]):
        bamboo_context = get_employee_directory()
    elif any(w in text_lower for w in ["próximo ingreso", "proximos ingresos", "próximos ingresos", "upcoming", "van a entrar", "quien entra", "quién entra"]):
        bamboo_context = get_upcoming_hires(60)
    elif any(w in text_lower for w in ["nuevo ingreso", "nuevos ingresos", "new hire", "recién ingresó", "últimos ingresos"]):
        bamboo_context = get_new_hires(60)
    elif any(w in text_lower for w in ["ausente", "ausencia", "time off", "quien falta", "quién falta", "fuera hoy"]):
        bamboo_context = get_time_off_today()
    elif any(w in text_lower for w in ["buscar", "información de", "datos de", "quién es", "quien es", "dime sobre", "busca a"]):
        for trigger in ["información de", "datos de", "buscar a", "busca a", "quién es", "quien es", "dime sobre"]:
            if trigger in text_lower:
                name_candidate = text_lower.split(trigger)[-1].strip().strip("?").strip()
                if len(name_candidate) > 2:
                    bamboo_context = get_employee_by_name(name_candidate)
                    break
    elif any(w in text_lower for w in ["ingreso", "ingresos", "hire", "empleado", "empleados", "onboarding"]):
        bamboo_context = get_upcoming_hires(60)
    return ask_groq(user_id, text, bamboo_context)


# ─── SLACK SOCKET MODE ────────────────────────────────────────

def handle_socket_event(client: SocketModeClient, req: SocketModeRequest):
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    payload = req.payload
    if req.type == "events_api":
        event = payload.get("event", {})
        if event.get("type") in ("message", "app_mention"):
            if event.get("bot_id") or event.get("subtype"):
                return
            user_id = event.get("user", "unknown")
            text    = event.get("text", "").strip()
            channel = event.get("channel")
            if "<@" in text:
                text = text.split(">", 1)[-1].strip()
            if not text:
                return
            print(f"[Slack] {user_id}: {text}")
            reply = route_message(user_id, text)
            slack_client.chat_postMessage(channel=channel, text=reply)
            print(f"[Panda] {reply[:100]}...")


def start_slack():
    socket_client = SocketModeClient(app_token=SLACK_APP_TOKEN, web_client=slack_client)
    socket_client.socket_mode_request_listeners.append(handle_socket_event)
    socket_client.connect()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


# ─── MAIN ─────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║  🐼 Panda Bear Agent — Koronet HR        ║")
    print("║  FastAPI + Slack + BambooHR + Groq       ║")
    print("╚══════════════════════════════════════════╝\n")

    init_db()
    print("🔍 Verificando credenciales...")

    try:
        auth = slack_client.auth_test()
        print(f"✅ Slack OK — Bot: {auth['user']} | Workspace: {auth['team']}")
    except Exception as e:
        print(f"❌ Slack ERROR: {e}"); return

    test = bamboo_get("employees/directory")
    if test:
        print(f"✅ BambooHR OK — {len(test.get('employees', []))} empleados")
    else:
        print("⚠️  BambooHR: verifica credenciales")

    try:
        groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        print("✅ Groq AI OK")
    except Exception as e:
        print(f"❌ Groq ERROR: {e}"); return

    # Verificar plantilla
    if os.path.exists(TEMPLATE_PATH):
        print("✅ Template OK")
    else:
        print(f"⚠️  Template no encontrado: {TEMPLATE_PATH}")

    print("\n🚀 Iniciando servicios...")
    print("   📡 API en http://localhost:8000")
    print("   💬 Slack Bot activo\n")

    slack_thread = threading.Thread(target=start_slack, daemon=True)
    slack_thread.start()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
