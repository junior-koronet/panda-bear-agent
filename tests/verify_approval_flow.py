"""
Verificación del flujo de aprobación — "Aprobar = crear borradores, nunca enviar".

Simula aprobaciones contra una base de datos temporal (nunca toca panda_bear.db,
Gmail ni Slack reales) y comprueba que:
  1. aprobar UN mensaje crea solo ese borrador y deja los otros pendientes
  2. "Aprobar todos" crea todos los borradores (con CC a Moira si no es hispanohablante)
  3. un fallo forzado queda en 'failed' con el error — nunca 'sent' ni 'approved'
  4. doble clic no crea dos borradores; 'Ya lo envié' no marca algo pendiente como enviado
  5. un error inesperado devuelve JSON legible (no un 500 vacío) y queda en logs/errors.log
  6. los recordatorios: primero a 5 días hábiles, uno por día, urgente el día anterior,
     y se detienen al resolver todo
  7. aprobar un mensaje de Slack dispara el worker que crea el borrador real en Slack
     (Claude solo puede crear borradores), y si falla queda 'failed' con aviso a Junior

Uso:  python tests/verify_approval_flow.py      (o: pytest tests/verify_approval_flow.py)
"""

import os
import sys
import tempfile
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmpdir = tempfile.mkdtemp(prefix="pb_verify_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "verify.db")
os.environ["JUNIOR_SLACK_ID"] = "UJUNIOR"
os.environ["PANDA_LOG_DIR"] = _tmpdir
os.environ.setdefault("DASHBOARD_URL", "http://localhost:8000/")

from db.schema import init_db, migrate_db, get_db  # noqa: E402

init_db()
migrate_db()

from core.agent import PandaBear  # noqa: E402
from core.kernel import ServiceKernel  # noqa: E402
from services.base import ServiceBase  # noqa: E402


# ── Fakes ────────────────────────────────────────────────────────

class FakeGoogle:
    def __init__(self):
        self.gmail = object()
        self.drafts = []
        self.fail_with = None

    def start(self):
        pass

    def find_personal_email(self, name):
        return None

    def create_draft(self, to, subject, body_text, image_path=None, cc=None):
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.drafts.append({"to": to, "subject": subject, "body": body_text, "cc": cc})
        return {"success": True, "draftId": f"r-test-{len(self.drafts)}"}


class FakeSlack(ServiceBase):
    name = "slack_service"

    def __init__(self):
        super().__init__()
        self.client = object()
        self.dms = []
        self.posted = []  # anything here would be a real send — must stay empty
        self.fail_dm = False

    def _on_start(self): pass
    def _on_stop(self): pass
    def _check_alive(self): return True

    def resolve_user_id(self, name):
        return {"Facundo Lavino": "UFACUNDO"}.get(name)

    def send_notification(self, user_id, text):
        if self.fail_dm:
            return {"sent": False, "error": "forced"}
        self.dms.append((user_id, text))
        return {"sent": True}

    def post_to_channel(self, *a, **k):
        self.posted.append(a)
        return {"sent": True}

    def send_dm(self, *a, **k):
        self.posted.append(a)
        return {"sent": True}


google = FakeGoogle()
slack = FakeSlack()
kernel = ServiceKernel()
kernel.register(slack)
agent = PandaBear(kernel=kernel, google_service=google)
# Never launch real headless Claude from tests — record the kicks instead.
kicks = []
agent.kick_slack_drafts = lambda results: kicks.append([r.get("status") for r in results])


def make_hire(name="Milena Briones", hire_date="2026-11-02", language="es", cc=None,
              manager="Facundo Lavino", email="mile@example.com") -> int:
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO batches (createdAt, messageCount, employeeName) VALUES (?, 3, ?)",
                  (datetime.now().isoformat(), name))
        bid = c.lastrowid
        for mtype in ("employee_email", "manager_slack", "bienvenidas_slack"):
            c.execute(
                "INSERT INTO messages (employeeId, employeeName, employeeHireDate, employeeEmail, managerName, "
                "messageType, messageContent, language, status, createdAt, batchId, ccEmail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?)",
                (f"{bid}-{mtype}", name, hire_date, email if mtype == "employee_email" else None, manager,
                 mtype, f"Contenido {mtype} de {name}", language, datetime.now().isoformat(), bid,
                 cc if mtype == "employee_email" else None),
            )
        conn.commit()
        return bid
    finally:
        conn.close()


def msgs(bid) -> dict:
    conn = get_db()
    try:
        return {r["messageType"]: dict(r) for r in conn.execute("SELECT * FROM messages WHERE batchId=?", (bid,))}
    finally:
        conn.close()


def batch_status(bid):
    conn = get_db()
    try:
        return conn.execute("SELECT status FROM batches WHERE id=?", (bid,)).fetchone()[0]
    finally:
        conn.close()


def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import router, set_agent
    from api.auth import verify_session_or_basic
    set_agent(agent)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_session_or_basic] = lambda: True
    return TestClient(app)


# ── Tests ────────────────────────────────────────────────────────

def test_approve_single_message_only_creates_that_draft():
    google.fail_with = None
    bid = make_hire()
    m = msgs(bid)
    r = client().post(f"/api/agent/messages/{m['employee_email']['id']}/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "draft_created" and body["created"] is True, body
    after = msgs(bid)
    assert after["employee_email"]["status"] == "draft_created"
    assert after["employee_email"]["draftId"], "draftId must be stored as proof"
    assert after["manager_slack"]["status"] == "pending_approval"
    assert after["bienvenidas_slack"]["status"] == "pending_approval"
    assert batch_status(bid) == "pending_approval"
    assert google.drafts[-1]["subject"] == "Tu aventura en Koronet está por comenzar 🚀"
    assert google.drafts[-1]["cc"] is None  # Spanish speaker → no CC

    # Slack one: destination verified, honestly reported as NOT created yet
    r = client().post(f"/api/agent/messages/{m['manager_slack']['id']}/approve").json()
    assert r["status"] == "ready_for_slack_draft" and r["created"] is False, r
    assert "Creando el borrador" in r["note"]
    assert kicks and kicks[-1] == ["ready_for_slack_draft"], "approval must kick the Slack draft worker"
    assert msgs(bid)["manager_slack"]["slackTarget"] == "UFACUNDO"
    assert msgs(bid)["bienvenidas_slack"]["status"] == "pending_approval"
    assert slack.posted == [], "nothing may ever be posted on approval"


def test_approve_all_creates_every_draft_with_moira_cc():
    google.fail_with = None
    bid = make_hire(name="Andrei Test", language="en", cc=None)
    r = client().post(f"/api/agent/batches/{bid}/approve")
    assert r.status_code == 200, r.text
    results = {x["type"]: x for x in r.json()["results"]}
    assert set(results) == {"employee_email", "manager_slack", "bienvenidas_slack"}
    assert results["employee_email"]["status"] == "draft_created"
    assert results["manager_slack"]["status"] == "ready_for_slack_draft"
    assert results["bienvenidas_slack"]["status"] == "ready_for_slack_draft"
    assert google.drafts[-1]["cc"] == "moira.gago@koronet.com"
    assert msgs(bid)["employee_email"]["ccEmail"] == "moira.gago@koronet.com"
    assert batch_status(bid) == "approved"
    assert all(m["status"] != "sent" for m in msgs(bid).values())
    assert any("Andrei Test" in t for _, t in slack.dms), "Junior must get the per-draft result DM"
    assert slack.posted == []


def test_forced_failure_is_failed_never_sent():
    bid = make_hire(name="Falla Forzada")
    google.fail_with = "invalid_grant: Token has been expired or revoked."
    m = msgs(bid)
    r = client().post(f"/api/agent/messages/{m['employee_email']['id']}/approve")
    assert r.status_code == 200, r.text  # never an HTTP 500
    body = r.json()
    assert body["status"] == "failed" and body["created"] is False
    assert "invalid_grant" in body["error"]
    row = msgs(bid)["employee_email"]
    assert row["status"] == "failed", row["status"]
    assert "invalid_grant" in row["deliveryError"]
    assert row["sentAt"] is None

    # Slack token / unknown manager also fail loudly
    bid2 = make_hire(name="Sin Lider", manager="Persona Inexistente")
    r2 = agent.approve_message(msgs(bid2)["manager_slack"]["id"])
    assert r2["status"] == "failed" and "No encontré" in r2["error"]

    # Gmail back → retry creates it
    google.fail_with = None
    r3 = client().post(f"/api/agent/messages/{m['employee_email']['id']}/retry").json()
    assert r3["status"] == "draft_created", r3


def test_double_click_and_mark_sent_guards():
    google.fail_with = None
    bid = make_hire(name="Doble Clic")
    mid = msgs(bid)["employee_email"]["id"]
    before = len(google.drafts)
    c = client()
    c.post(f"/api/agent/messages/{mid}/approve")
    second = c.post(f"/api/agent/messages/{mid}/approve").json()
    assert len(google.drafts) == before + 1, "second click must not create a second draft"
    assert second["created"] is False and "ya no está pendiente" in second["error"]

    pending_id = msgs(bid)["manager_slack"]["id"]
    r = c.post(f"/api/agent/messages/{pending_id}/resend")
    assert r.status_code == 409, "a pending message can never be marked sent"
    assert msgs(bid)["manager_slack"]["status"] == "pending_approval"

    # skip / reject per message
    assert c.post(f"/api/agent/messages/{pending_id}/skip").status_code == 200
    bvd = msgs(bid)["bienvenidas_slack"]["id"]
    assert c.post(f"/api/agent/messages/{bvd}/reject").status_code == 200
    assert batch_status(bid) == "approved"  # nothing pending anymore


def test_unexpected_error_returns_readable_json_and_is_logged():
    from fastapi.testclient import TestClient
    import main
    from api.auth import verify_session_or_basic
    main.app.dependency_overrides[verify_session_or_basic] = lambda: True
    original = main.agent.get_stats
    main.agent.get_stats = lambda: (_ for _ in ()).throw(ValueError("boom de prueba"))
    try:
        r = TestClient(main.app, raise_server_exceptions=False).get("/api/agent/stats")
    finally:
        main.agent.get_stats = original
    assert r.status_code == 500
    assert "boom de prueba" in r.json()["detail"], r.text
    log = open(os.path.join(_tmpdir, "errors.log"), encoding="utf-8").read()
    assert "boom de prueba" in log


def test_reminders_fire_once_per_day_and_stop():
    from services import hire_reminders as hr
    tz = ZoneInfo("America/Bogota")
    cfg = {**hr.config(), "firstBusinessDays": 5, "dailyHour": 8, "urgentBusinessDays": 1,
           "weekends": False, "timezone": "America/Bogota"}
    hire = date(2027, 3, 15)  # Monday
    bid = make_hire(name="Recordatorio Test", hire_date=hire.isoformat())
    sent = []

    def send(text):
        sent.append(text)
        return True

    def at(d, h=8, mi=5):
        return datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)

    first_day = hr.business_days_before(hire, 5)          # Mon 2027-03-08
    assert first_day == date(2027, 3, 8)
    mine = lambda fired: [f for f in fired if f["batchId"] == bid]

    assert mine(hr.run_due_reminders(send, at(first_day - timedelta(days=1)), cfg)) == []  # too early
    assert mine(hr.run_due_reminders(send, at(first_day, 7, 59), cfg)) == []  # before 8:00
    f = mine(hr.run_due_reminders(send, at(first_day), cfg))
    assert len(f) == 1 and f[0]["kind"] == "first"
    text = f[0]["text"]
    assert "Recordatorio Test" in text and "15/03/2027" in text and "http://localhost:8000/" in text
    assert "DM al líder" in text and "Correo de bienvenida" in text
    # same day again → nothing (max one per hire per day)
    assert mine(hr.run_due_reminders(send, at(first_day, 15), cfg)) == []
    # next day → daily
    f = mine(hr.run_due_reminders(send, at(first_day + timedelta(days=1)), cfg))
    assert len(f) == 1 and f[0]["kind"] == "daily"
    # weekend → nothing
    assert mine(hr.run_due_reminders(send, at(date(2027, 3, 13)), cfg)) == []
    # business day before hire (Fri 12) → urgent
    f = mine(hr.run_due_reminders(send, at(date(2027, 3, 12)), cfg))
    assert len(f) == 1 and f[0]["kind"] == "urgent" and "URGENTE" in f[0]["text"]

    # Slack send failure releases the slot so a later check retries the same day
    assert mine(hr.run_due_reminders(lambda t: False, at(date(2027, 3, 11)), cfg)) == []
    assert len(mine(hr.run_due_reminders(send, at(date(2027, 3, 11), 9), cfg))) == 1

    # approving one message: the reminder lists only what's still pending
    google.fail_with = None
    agent.approve_message(msgs(bid)["employee_email"]["id"], notify=False)
    conn = get_db(); conn.execute("DELETE FROM hire_reminders WHERE batchId=?", (bid,)); conn.commit(); conn.close()
    f = mine(hr.run_due_reminders(send, at(date(2027, 3, 10)), cfg))
    assert len(f) == 1 and "Correo de bienvenida" not in f[0]["text"]

    # resolve everything (skip + reject) → reminders stop
    agent.invoke_skill("approval_workflow", action="skip_message", message_id=msgs(bid)["manager_slack"]["id"])
    agent.invoke_skill("approval_workflow", action="reject_message", message_id=msgs(bid)["bienvenidas_slack"]["id"])
    conn = get_db(); conn.execute("DELETE FROM hire_reminders WHERE batchId=?", (bid,)); conn.commit(); conn.close()
    assert mine(hr.run_due_reminders(send, at(date(2027, 3, 12), 10), cfg)) == []


def test_slack_draft_worker_creates_real_drafts_and_fails_loudly():
    from services import slack_draft_worker as w
    # Claude may ONLY create drafts — every sending tool is blocked.
    assert w.ALLOWED_TOOLS == "ToolSearch,mcp__claude_ai_Slack__slack_send_message_draft"
    assert "slack_send_message" in w.DISALLOWED_TOOLS and "slack_schedule_message" in w.DISALLOWED_TOOLS

    bid = make_hire(name="Worker Test", hire_date="2027-05-10")
    m = msgs(bid)
    agent.approve_message(m["manager_slack"]["id"])
    agent.approve_message(m["bienvenidas_slack"]["id"])
    calls, dms = [], []

    def ok_runner(channel, message):
        calls.append((channel, message))
        return {"ok": True, "draft_id": f"Dr{len(calls)}"}

    done = w.create_pending_slack_drafts(notify=dms.append, runner=ok_runner)
    mine = [d for d in done if d["id"] in (m["manager_slack"]["id"], m["bienvenidas_slack"]["id"])]
    assert len(mine) == 2 and all(d["status"] == "draft_created" for d in mine), done
    after = msgs(bid)
    assert after["manager_slack"]["status"] == "draft_created" and after["manager_slack"]["draftId"]
    assert ("UFACUNDO", "Contenido manager_slack de Worker Test") in calls  # verbatim content, right DM
    assert ("CDTF8BJP4", "Contenido bienvenidas_slack de Worker Test") in calls  # #koronet
    assert any("ya creé el mensaje al manager de *Worker Test*" in t for t in dms)
    assert any("ya tengo creada la bienvenida de *Worker Test* el lunes" in t for t in dms)
    assert w.create_pending_slack_drafts(notify=dms.append, runner=ok_runner) == []  # nothing twice

    # Failure path: retried on later sweeps, then 'failed' with reason + exactly one DM
    bid2 = make_hire(name="Worker Falla", hire_date="2027-05-10")
    mid = msgs(bid2)["manager_slack"]["id"]
    agent.approve_message(mid)
    dms.clear()
    bad = lambda ch, msg: {"ok": False, "error": "attached_draft_exists"}
    for _ in range(w.MAX_ATTEMPTS):
        w.create_pending_slack_drafts(notify=dms.append, runner=bad)
    row = msgs(bid2)["manager_slack"]
    assert row["status"] == "failed" and "attached_draft_exists" in row["deliveryError"], row
    assert len([t for t in dms if "Worker Falla" in t]) == 1
    assert w.create_pending_slack_drafts(notify=dms.append, runner=bad) == []

    # 🔁 Reintentar re-stages it and the next sweep creates it
    assert agent.prepare_message_draft(mid)["status"] == "ready_for_slack_draft"
    w.create_pending_slack_drafts(notify=dms.append, runner=ok_runner)
    assert msgs(bid2)["manager_slack"]["status"] == "draft_created"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} pruebas OK")
    sys.exit(1 if failed else 0)
