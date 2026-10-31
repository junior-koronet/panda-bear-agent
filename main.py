"""
Panda Bear — Main Entry Point
Starts the agent, registers all services, and launches the API.

Usage:
    python main.py
    uvicorn main:app --host 0.0.0.0 --port 8000

The agent starts FIRST. Then the API. The dashboard connects to the running agent.
"""

import os
import sys
import threading
import uvicorn
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

# ── Verify environment ───────────────────────────────────────────
required = ["BAMBOO_API_KEY", "SLACK_BOT_TOKEN", "GROQ_API_KEY", "JUNIOR_SLACK_ID"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"⚠️  Missing environment variables: {', '.join(missing)}")
    print("   Some features may be unavailable.")

# ── Initialize database ──────────────────────────────────────────
from db.schema import init_db, migrate_db, get_db
init_db()
migrate_db()

# ── Create services ──────────────────────────────────────────────
from core.kernel import ServiceKernel
from services.bamboo_service import BambooService
from services.slack_service import SlackService
from services.google_service import GoogleService
from services.reminder_service import ReminderService
from services.meeting_join_service import MeetingJoinService
from services.sync_service import SyncService

kernel = ServiceKernel()
bamboo_svc = BambooService()
google_svc = GoogleService()

# ── Create the agent BEFORE starting services ────────────────────
# (so the Slack service can reference the agent's think() method)
from core.agent import PandaBear
agent = PandaBear(kernel=kernel, google_service=google_svc)

meeting_join_svc = MeetingJoinService(on_call_ended=agent.process_recorded_meeting)

JUNIOR_SLACK_ID = os.getenv("JUNIOR_SLACK_ID", "")


def route_message(user_id: str, text: str) -> str:
    # Personal-assistant skills (calendar/email) are only reachable by Junior.
    # Everyone else keeps using the normal HR onboarding brain.
    if JUNIOR_SLACK_ID and user_id == JUNIOR_SLACK_ID:
        return agent.think_personal(user_id=user_id, text=text)
    return agent.think(user_id=user_id, text=text)


slack_svc = SlackService(on_message=route_message)


def send_hire_reminder(text: str) -> bool:
    """Pending-approval reminder DM from the Panda Bear bot. True only if Slack accepted it."""
    if not JUNIOR_SLACK_ID:
        print("[reminders] JUNIOR_SLACK_ID no configurado — no puedo enviar recordatorios")
        return False
    result = slack_svc.send_notification(JUNIOR_SLACK_ID, text)
    if not result.get("sent"):
        print(f"[reminders] Slack rechazó el recordatorio: {result.get('error')}")
    return bool(result.get("sent"))


def handle_commitments_due(commitments: list) -> None:
    if not JUNIOR_SLACK_ID:
        return
    lines = ["🐼 *Recordatorio — compromisos pendientes de reuniones:*\n"]
    for c in commitments:
        owner = f" — {c['owner']}" if c.get("owner") else ""
        due = f" (para {c['dueHint']})" if c.get("dueHint") else ""
        lines.append(f"• {c['description']}{owner}{due}")
    slack_svc.send_notification(JUNIOR_SLACK_ID, "\n".join(lines))


def handle_slack_drafts_ready(count: int) -> None:
    if not JUNIOR_SLACK_ID:
        return
    text = (
        f"🐼 *{count} mensaje(s) de Slack listos para armar como borrador* "
        f"(manager / #koronet). Pedime en una conversación con Claude: "
        f"\"arma los borradores de Slack pendientes\" y los dejo listos para que vos los envíes."
    )
    slack_svc.send_notification(JUNIOR_SLACK_ID, text)


def handle_photo_found(batch_id: int, photo_bytes: bytes) -> None:
    result = agent.attach_employee_photo(batch_id, photo_bytes)
    if not JUNIOR_SLACK_ID or not result.get("success"):
        return
    conn = get_db()
    try:
        batch = conn.execute("SELECT employeeName FROM batches WHERE id=?", (batch_id,)).fetchone()
    finally:
        conn.close()
    emp_name = batch["employeeName"] if batch else ""
    text = (
        f"🐼 Junior, BambooHR ya tiene foto de *{emp_name}* — generé el flyer y lo dejé en "
        f"Descargas y en el correo/bienvenida del batch #{batch_id}."
    )
    slack_svc.send_notification(JUNIOR_SLACK_ID, text)


def handle_retry_failed_drafts(batch_id: int) -> None:
    """Retries a batch's failed drafts quietly; only DMs Junior when one now succeeds."""
    result = agent.execute_batch(batch_id, notify=False)
    still_failed = [r["id"] for r in result.get("results", []) if r["status"] == "failed"]
    if still_failed:
        conn = get_db()
        try:
            conn.executemany(
                "UPDATE messages SET retries = COALESCE(retries, 0) + 1 WHERE id=?",
                [(i,) for i in still_failed],
            )
            conn.commit()
        finally:
            conn.close()
    created = [r for r in result.get("results", []) if r["status"] == "draft_created"]
    if created and JUNIOR_SLACK_ID:
        emp_name = created[0].get("employeeName", "")
        slack_svc.send_notification(
            JUNIOR_SLACK_ID,
            f"🐼 Junior, reintenté y ya quedó creado el borrador del correo de *{emp_name}* en tu Gmail — revísalo y envíalo.",
        )


reminder_svc = ReminderService(
    on_hire_reminder=send_hire_reminder,
    on_commitments_due=handle_commitments_due,
    on_slack_drafts_ready=handle_slack_drafts_ready,
    on_photo_found=handle_photo_found,
    on_retry_failed_drafts=handle_retry_failed_drafts,
    notify_junior=agent.notify_junior,
)

from services import draft_health


def handle_sync_failed(slot_label: str, error: str) -> None:
    """DM from the Panda Bear bot when a scheduled sync fails."""
    if not JUNIOR_SLACK_ID:
        print("[sync] JUNIOR_SLACK_ID no configurado — no puedo avisar del sync fallido")
        return
    dashboard = os.getenv("DASHBOARD_URL", "http://localhost:8000/")
    text = (
        f"🐼 ⚠️ *Falló el sync automático {slot_label}.*\n"
        f"Error: `{error[:400]}`\n"
        f"No se detectaron ingresos nuevos en este ciclo. Revisa Runs en el dashboard "
        f"o dale ▶ Run Sync cuando esté resuelto: {dashboard}"
    )
    result = slack_svc.send_notification(JUNIOR_SLACK_ID, text)
    if not result.get("sent"):
        print(f"[sync] Slack rechazó el aviso de sync fallido: {result.get('error')}")


sync_svc = SyncService(
    agent=agent,
    times=os.getenv("SYNC_TIMES", "08:00,12:00,16:00"),
    timezone=os.getenv("SYNC_TIMEZONE", "America/Bogota"),
    lookback_days=int(os.getenv("SYNC_LOOKBACK_DAYS", 60)),
    on_failure=handle_sync_failed,
    on_before_run=lambda: draft_health.run_checks(google_svc, slack_svc, notify=agent.notify_junior),
)

# ── Register services with the Kernel ────────────────────────────
kernel.register(bamboo_svc)
kernel.register(google_svc)
kernel.register(slack_svc)
kernel.register(reminder_svc)
kernel.register(sync_svc)
kernel.register(meeting_join_svc)

# ── Wire the agent's routes ──────────────────────────────────────
from api.routes import router, set_agent
set_agent(agent)

from api.webhooks import router as webhooks_router, set_agent as set_webhooks_agent
set_webhooks_agent(agent)

from api.auth import router as auth_router, is_valid_session, SESSION_COOKIE_NAME

# ── Lifespan (startup + shutdown) ───────────────────────────────
@asynccontextmanager
async def lifespan(app):
    # ── Startup ──────────────────────────────────────────────────
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Panda Bear Agent — People Operations            ║")
    print("║  Koronet HR · Version 4.0                        ║")
    print("╚══════════════════════════════════════════════════╝\n")
    print(f"  Identity: {agent.identify()['name']} — {agent.identify()['role']}")
    print(f"  Skills: {len(agent._skills)}")
    print(f"  Services: {len(kernel._services)}\n")

    results = kernel.start_all()
    for svc_name, result in results.items():
        status = "OK" if result.get("started") else result.get("error", "Failed")
        print(f"  {'[OK]' if result.get('started') else '[!!]'} {svc_name}: {status}")

    from services.slack_draft_worker import claude_bin
    print(f"  Borradores de Slack vía Claude: {claude_bin() or 'NO ENCONTRADO — define CLAUDE_BIN'}")

    print("\n  API: http://0.0.0.0:8000")
    print("  Dashboard: http://0.0.0.0:8000/\n")

    yield

    # ── Shutdown ─────────────────────────────────────────────────
    print("\n[Agent] Shutting down — stopping all services...")
    kernel.stop_all()
    print("[Agent] Panda Bear offline.\n")


# ── Create FastAPI app ───────────────────────────────────────────
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Panda Bear Agent API",
    description="People Operations Agent — Koronet HR",
    version="4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Any unhandled error: full traceback to logs/errors.log (stdout is not a
# reliable log when started hidden) and a JSON body the dashboard can show,
# instead of a bare "HTTP 500".
import logging
import traceback
from fastapi import Request as _Request
from fastapi.responses import JSONResponse

LOG_DIR = os.getenv("PANDA_LOG_DIR", os.path.join(os.path.dirname(__file__), "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
_error_log = logging.getLogger("panda_bear.errors")
_error_handler = logging.FileHandler(os.path.join(LOG_DIR, "errors.log"), encoding="utf-8")
_error_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_error_log.addHandler(_error_handler)
_error_log.setLevel(logging.ERROR)


@app.exception_handler(Exception)
async def unhandled_error(request: _Request, exc: Exception):
    _error_log.error(
        "%s %s\n%s", request.method, request.url.path,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Error interno en {request.url.path}: {type(exc).__name__}: {exc}"},
    )


# Include agent routes
app.include_router(router)
app.include_router(webhooks_router)
app.include_router(auth_router)

# ── Serve dashboard ──────────────────────────────────────────────
from fastapi import Request
from fastapi.responses import RedirectResponse

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "dashboard", "index.html")
LOGIN_PATH = os.path.join(os.path.dirname(__file__), "dashboard", "login.html")

@app.get("/")
def serve_dashboard(request: Request):
    if not is_valid_session(request.cookies.get(SESSION_COOKIE_NAME)):
        return RedirectResponse(url="/login")
    if os.path.exists(DASHBOARD_PATH):
        # no-store: without it the browser may reuse a stale copy of the dashboard
        # after a change and keep showing the old UI.
        return FileResponse(DASHBOARD_PATH, headers={"Cache-Control": "no-store"})
    return {"message": "Panda Bear Agent API running", "version": "4.0"}

@app.get("/login")
def serve_login(request: Request):
    if is_valid_session(request.cookies.get(SESSION_COOKIE_NAME)):
        return RedirectResponse(url="/")
    return FileResponse(LOGIN_PATH)

@app.api_route("/cron/tick", methods=["GET", "POST"])
def cron_tick(request: Request):
    """
    External wake-up for hosts that sleep when idle (Render free tier): an
    external cron (e.g. cron-job.org every 10 min) hits this, which wakes the
    service AND runs the pending-approval reminder check right away. Protected
    by CRON_SECRET (?key=… or X-Cron-Key header). Sends at most one reminder
    per hire per day no matter how often it's called.
    """
    secret = os.getenv("CRON_SECRET", "")
    given = request.query_params.get("key") or request.headers.get("x-cron-key", "")
    if not secret or given != secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="CRON_SECRET inválido o no configurado")
    fired = reminder_svc.run_hire_reminders()
    return {"ok": True, "remindersSent": len(fired), "batches": [f["batchId"] for f in fired]}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "Panda Bear",
        "version": "4.0",
        "kernel": kernel.health()["kernel"]["status"],
    }

# Serve generated images
images_dir = os.path.join(os.path.dirname(__file__), "generated_images")
os.makedirs(images_dir, exist_ok=True)
app.mount("/images", StaticFiles(directory=images_dir), name="images")


# ── Run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level="warning",
        reload=False,
    )
