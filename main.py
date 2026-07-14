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
required = ["BAMBOO_API_KEY", "SLACK_BOT_TOKEN", "GROQ_API_KEY"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"⚠️  Missing environment variables: {', '.join(missing)}")
    print("   Some features may be unavailable.")

# ── Initialize database ──────────────────────────────────────────
from db.schema import init_db, migrate_db
init_db()
migrate_db()

# ── Create services ──────────────────────────────────────────────
from core.kernel import ServiceKernel
from services.bamboo_service import BambooService
from services.slack_service import SlackService

kernel = ServiceKernel()
bamboo_svc = BambooService()

# ── Create the agent BEFORE starting services ────────────────────
# (so the Slack service can reference the agent's think() method)
from core.agent import PandaBear
agent = PandaBear(kernel=kernel)

slack_svc = SlackService(on_message=lambda user_id, text: agent.think(user_id=user_id, text=text))

# ── Register services with the Kernel ────────────────────────────
kernel.register(bamboo_svc)
kernel.register(slack_svc)

# ── Wire the agent's routes ──────────────────────────────────────
from api.routes import router, set_agent
set_agent(agent)

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

# Include agent routes
app.include_router(router)

# ── Serve dashboard ──────────────────────────────────────────────
DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "dashboard", "index.html")

@app.get("/")
def serve_dashboard():
    if os.path.exists(DASHBOARD_PATH):
        return FileResponse(DASHBOARD_PATH)
    return {"message": "Panda Bear Agent API running", "version": "4.0"}

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
