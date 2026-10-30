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
  8. Agendamiento: la sesión se propone con la hora del correo y el correo corporativo
     de NEW HIRES; se puede editar; "Agendar" crea UN evento (doble clic no duplica),
     y un fallo queda 'failed' con el motivo y se reintenta con el mismo eventId
  9. Sesiones IT / Payroll / Product / First month: días hábiles según feriados, alertas
     (feriado, ausencia en BambooHR, ocupado), Product solo a UNA ocurrencia, First month
     en día 28–30, sesiones ya existentes se vinculan (no se duplican), reagendar mueve
     el evento y los cambios hechos en Calendar se sincronizan; alerta de feriado en Approvals
 11. Horarios libres de First month / IT / Payroll se recalculan desde la fecha que se mira:
     ese día si hay espacio, si no el aviso de por qué y los siguientes días hábiles
 10. Varios ingresos el mismo día: una sola sesión de HR (español, contigo) + otra en inglés
     con Moira si hay no hispanohablantes; IT solo hispanohablantes; Payroll solo Colombia;
     todos a la misma Product; First month por persona; quien se suma después de agendar
     se agrega al MISMO evento

Uso:  python tests/verify_approval_flow.py      (o: pytest tests/verify_approval_flow.py)
"""

import os
import re
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
        self.corporate = {"Milena Briones": "milena.briones@koronet.com"}
        self.events = {}          # eventId → {title, start, end, tz, attendees, recurring?, organizer?}
        self.event_fail_with = None
        self.busy = {}            # email → [(start_iso, end_iso)]

    def start(self):
        pass

    def find_corporate_email(self, name, hire_date=""):
        email = self.corporate.get(name)
        return {"email": email, "source": "NEW HIRES- KORONET (pestaña 2026)"} if email else             {"email": None, "error": f"{name} no aparece todavía en NEW HIRES"}

    def create_onboarding_event(self, event_id, title, start_iso, end_iso, time_zone, attendees, description=""):
        if self.event_fail_with:
            return {"success": False, "error": self.event_fail_with}
        if not re.fullmatch(r"[a-v0-9]{5,1024}", event_id):  # Google: "Invalid resource id value."
            return {"success": False, "error": "Invalid resource id value."}
        self.events.setdefault(event_id, {"title": title, "start": start_iso, "end": end_iso,
                                          "tz": time_zone, "attendees": attendees})
        return {"success": True, "eventId": event_id, "link": f"https://cal/{event_id}", "meetLink": "https://meet/x"}

    def _as_event(self, eid, e):
        off = lambda t: t if t[-6] in "+-" and t[-3] == ":" else t + "-05:00"
        return {"id": eid, "summary": e["title"], "status": e.get("status", "confirmed"),
                "start": {"dateTime": off(e["start"])}, "end": {"dateTime": off(e["end"])},
                "attendees": [{"email": a} for a in e["attendees"]], "htmlLink": f"https://cal/{eid}",
                "recurringEventId": e.get("recurring"), "organizer": {"email": e.get("organizer", "junior@koronet.com")}}

    def list_events(self, time_min, time_max, query=None):
        out = [self._as_event(k, v) for k, v in self.events.items()
               if time_min[:10] <= v["start"][:10] <= time_max[:10] and (not query or query.lower() in v["title"].lower())]
        return sorted(out, key=lambda e: e["start"]["dateTime"])

    def get_event(self, eid):
        v = self.events.get(eid)
        return self._as_event(eid, v) if v else None

    def busy_slots(self, emails, time_min, time_max, time_zone):
        return {e: list(self.busy.get(e, [])) for e in emails}

    def set_attendee(self, eid, email, present):
        v = self.events.get(eid)
        if not v:
            return {"success": False, "error": "No encontré ese evento"}
        v["attendees"] = [a for a in v["attendees"] if a != email] + ([email] if present else [])
        return {"success": True, "eventId": eid, "link": f"https://cal/{eid}"}

    def move_event(self, eid, start_iso, end_iso, time_zone):
        self.events[eid].update(start=start_iso, end=end_iso)
        return {"success": True, "eventId": eid, "link": f"https://cal/{eid}"}

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


def _fake_bamboo(time_off=()):
    """Directory + who's-out for the session hosts (BambooHR is never called from tests)."""
    from services import onboarding_meetings as om
    people = [("1", "Jeisson", "Garcia", "jeisson@kometsales.com", "Colombia"),
              ("2", "Daniel", "Patiño", "danielp@koronet.com", "Colombia"),
              ("3", "Guadalupe", "Galle", "guadalupe@kometsales.com", "Argentina"),
              ("4", "Moira", "Gago", "moira.gago@koronet.com", "Argentina"),
              ("5", "Andrés", "Soporte", "andres.soporte@koronet.com", "Colombia")]

    def fake(endpoint):
        if endpoint.startswith("employees/directory"):
            return {"employees": [{"id": i, "firstName": f, "lastName": l, "displayName": f"{f} {l}",
                                   "workEmail": e, "location": loc, "jobTitle": ""} for i, f, l, e, loc in people]}
        if endpoint.startswith("time_off/whos_out"):
            return [{"type": "timeOff", "employeeId": i, "start": st, "end": en} for i, st, en in time_off]
        return None
    om._bamboo = fake
    om._cache.clear()


def _set_hire(bid, location, content="... sesión conmigo a las 08:00 am (hora Colombia)."):
    conn = get_db()
    conn.execute("UPDATE messages SET employeeLocation=?, messageContent=? WHERE batchId=? AND messageType='employee_email'",
                 (location, content, bid))
    conn.commit(); conn.close()


def _created_events():
    """Events Panda Bear created (the fixtures for Adrian's series / hand-made events carry an organizer)."""
    return {k: v for k, v in google.events.items() if not v.get("organizer")}


def _sessions(c, bid):
    return {x["sessionType"]: x for x in c.get("/api/agent/meetings").json()
            if bid in [h["batchId"] for h in x["hires"]]}


def _session(c, bid, kind):
    return _sessions(c, bid)[kind]


def test_onboarding_meeting_proposal_edit_and_schedule():
    _fake_bamboo()
    google.events.clear()
    google.event_fail_with = None
    bid = make_hire(name="Milena Briones", hire_date="2099-11-03")
    _set_hire(bid, "Argentina", "... una sesión conmigo a las 08:00 am (hora Colombia) / 10:00 am (hora Argentina).")

    c = client()
    m = _session(c, bid, "hr")
    assert (m["startDate"], m["startTime"], m["status"]) == ("2099-11-03", "08:00", "pending_approval"), m
    assert m["hires"][0]["email"] == "milena.briones@koronet.com" and "NEW HIRES" in m["hires"][0]["emailSource"], m
    assert m["extraAttendees"] == ["guadalupe@kometsales.com", "moira.gago@koronet.com"], m
    assert m["localTime"] == "10:00 (hora Argentina)", m
    kinds = set(_sessions(c, bid))
    assert kinds == {"hr", "it", "product", "first_month"}, kinds  # Argentina: no Payroll
    assert not _created_events(), "nada se agenda antes de que Junior apruebe"

    # Edit before approving; bad input is rejected with a readable error
    assert c.post(f"/api/agent/meetings/{m['id']}", json={"startTime": "25:00"}).status_code == 400
    r = c.post(f"/api/agent/meetings/{m['id']}", json={"startDate": "2099-11-04", "startTime": "09:30",
                                                      "extraAttendees": ["guadalupe@kometsales.com"]})
    assert r.status_code == 200 and r.json()["startTime"] == "09:30", r.text

    # Forced failure → failed with reason, no event
    google.event_fail_with = "Calendar caído"
    r = c.post(f"/api/agent/meetings/{m['id']}/schedule").json()
    assert not r["ok"] and r["meeting"]["status"] == "failed" and "Calendar caído" in r["meeting"]["error"], r
    first_event_id = r["meeting"]["eventId"]

    # Retry succeeds with the SAME event id; double click doesn't create a second event
    google.event_fail_with = None
    r = c.post(f"/api/agent/meetings/{m['id']}/schedule").json()
    assert r["ok"] and r["meeting"]["status"] == "scheduled" and r["meeting"]["eventId"] == first_event_id, r
    r2 = c.post(f"/api/agent/meetings/{m['id']}/schedule").json()
    assert r2["ok"] and r2.get("note") == "Ya estaba agendado"
    assert len(_created_events()) == 1, google.events
    ev = google.events[first_event_id]
    assert ev["start"] == "2099-11-04T09:30:00" and ev["end"] == "2099-11-04T10:30:00" and ev["tz"] == "America/Bogota", ev
    assert ev["attendees"] == ["milena.briones@koronet.com", "guadalupe@kometsales.com"], ev
    # Scheduled → no more edits
    assert c.post(f"/api/agent/meetings/{m['id']}", json={"startTime": "10:00"}).status_code == 400

    # A hire not yet in NEW HIRES: no email, can't be scheduled until Junior types it
    bid2 = make_hire(name="Persona Sin Correo", hire_date="2099-11-10")
    m2 = _session(c, bid2, "hr")
    assert not m2["hires"][0]["email"] and "NEW HIRES" in (m2["error"] or ""), m2
    r = c.post(f"/api/agent/meetings/{m2['id']}/schedule").json()
    assert not r["ok"] and "correo corporativo" in r["error"], r
    assert len(_created_events()) == 1

    # A manual test card (negative batchId) with an id Google rejected gets a valid new id on retry
    conn = get_db()
    cur = conn.execute("INSERT INTO onboarding_meetings (batchId, sessionType, groupKey, employeeName, attendeeEmail, "
                       "extraAttendees, title, startDate, startTime, durationMinutes, status, eventId, createdAt) VALUES "
                       "(-7, 'hr', 'prueba', 'PRUEBA', 'prueba@example.com', '', 'PRUEBA', '2099-11-02', '08:00', 60, "
                       "'failed', 'pbonb-7abc', '2099-01-01')")
    conn.commit(); mid = cur.lastrowid; conn.close()
    r = c.post(f"/api/agent/meetings/{mid}/schedule").json()
    assert r["ok"] and re.fullmatch(r"[a-v0-9]{5,1024}", r["meeting"]["eventId"]), r
    assert len(_created_events()) == 2


def test_onboarding_sessions_holidays_alerts_product_first_month_reschedule():
    import holidays as pyholidays
    from skills import holidays_check as hol
    google.events.clear()
    google.busy.clear()
    google.event_fail_with = None
    # A Monday that is a holiday in Colombia (and not in Argentina) in 2099, followed by a business day.
    co = pyholidays.country_holidays("CO", years=2099)
    d = next(x for x in sorted(co) if x.weekday() == 0 and not hol.holiday_name(x, "Argentina")
             and hol.is_business_day(x + timedelta(days=1), ["Colombia"]))
    nxt = d + timedelta(days=1)
    _fake_bamboo(time_off=[("1", (d - timedelta(days=3)).isoformat(), nxt.isoformat())])  # Jeisson out
    google.busy["danielp@koronet.com"] = [(f"{nxt}T11:00:00-05:00", f"{nxt}T11:30:00-05:00")]
    # Adrian's recurring Product Onboarding: two upcoming occurrences
    p1, p2 = d + timedelta(days=8), d + timedelta(days=36)
    for i, pd in enumerate((p1, p2)):
        google.events[f"series1_{i}"] = {"title": "Monthly Product Onboarding", "start": f"{pd}T09:00:00",
                                         "end": f"{pd}T10:00:00", "attendees": ["adrian.aubone@kometsales.com"],
                                         "recurring": "series1", "organizer": "adrian.aubone@kometsales.com"}

    google.corporate["Carlos Feriado"] = "carlos.feriado@koronet.com"
    bid = make_hire(name="Carlos Feriado", hire_date=d.isoformat())
    _set_hire(bid, "Colombia")
    c = client()
    rows = _sessions(c, bid)
    assert set(rows) == {"hr", "it", "payroll", "product", "first_month"}, rows.keys()  # Colombia: Payroll too
    alerts = lambda k: " | ".join(a["text"] for a in rows[k]["alerts"])

    # HR on a Colombian holiday: warn, Moira gives it
    assert "Feriado en Colombia" in alerts("hr") and "Moira" in alerts("hr"), alerts("hr")
    # IT / Payroll: next Colombian business day at 10:00 / 11:00; Jeisson's leave and Dani's meeting flagged
    assert (rows["it"]["startDate"], rows["it"]["startTime"], rows["it"]["durationMinutes"]) == (nxt.isoformat(), "10:00", 30)
    assert (rows["payroll"]["startDate"], rows["payroll"]["startTime"]) == (nxt.isoformat(), "11:00")
    assert rows["it"]["extraAttendees"] == ["jeisson@kometsales.com"] and "ausente" in alerts("it"), alerts("it")
    assert rows["payroll"]["extraAttendees"] == ["danielp@koronet.com"] and "Ocupado a esa hora: Daniel" in alerts("payroll"), alerts("payroll")
    # Product: nearest occurrence, options listed
    assert rows["product"]["eventId"] == "series1_0" and rows["product"]["startDate"] == p1.isoformat(), rows["product"]
    assert [o["eventId"] for o in rows["product"]["productOptions"]] == ["series1_0", "series1_1"]
    # First month: Mon–Fri, no holiday, day 28–30
    fm = rows["first_month"]
    day = (date.fromisoformat(fm["startDate"]) - d).days
    assert 28 <= day <= 30 and hol.is_business_day(fm["startDate"], ["Colombia", "Argentina"]), fm
    assert fm["title"] == "Carlos / Guada - First month ✨" and fm["durationMinutes"] == 30, fm
    slots = c.get(f"/api/agent/meetings/{fm['id']}/slots").json()["slots"]
    assert slots and all(28 <= s["day"] <= 41 for s in slots), slots
    assert not _created_events(), "nada se agenda solo"

    # Approvals warns that the start date is a holiday in Colombia
    b = next(x for x in c.get("/api/agent/batches").json() if x["id"] == bid)
    assert b["startAlert"] and "feriado en Colombia" in b["startAlert"], b

    # Jeisson is out: swap the IT host, schedule, then reschedule (event moved, not duplicated)
    it = rows["it"]
    r = c.post(f"/api/agent/meetings/{it['id']}/schedule", json={"extraAttendees": ["andres.soporte@koronet.com"]}).json()
    assert r["ok"], r
    ev_id = r["meeting"]["eventId"]
    assert google.events[ev_id]["attendees"] == ["carlos.feriado@koronet.com", "andres.soporte@koronet.com"]
    later = (nxt + timedelta(days=1)).isoformat()
    r = c.post(f"/api/agent/meetings/{it['id']}/reschedule", json={"startDate": later, "startTime": "15:00"}).json()
    assert r["ok"] and r["meeting"]["startDate"] == later and r["meeting"]["startTime"] == "15:00", r
    assert google.events[ev_id]["start"] == f"{later}T15:00:00" and len(_created_events()) == 1
    # Moved directly in Google Calendar → synced back
    google.events[ev_id].update(start=f"{later}T16:00:00", end=f"{later}T16:30:00")
    assert _session(c, bid, "it")["startTime"] == "16:00"

    # Product: added to ONE occurrence only; reschedule moves the hire to the other one
    pr = rows["product"]
    r = c.post(f"/api/agent/meetings/{pr['id']}/schedule", json={"eventId": "series1_0"}).json()
    assert r["ok"] and "carlos.feriado@koronet.com" in google.events["series1_0"]["attendees"], r
    assert "carlos.feriado@koronet.com" not in google.events["series1_1"]["attendees"]
    r = c.post(f"/api/agent/meetings/{pr['id']}/reschedule", json={"eventId": "series1_1"}).json()
    assert r["ok"] and r["meeting"]["startDate"] == p2.isoformat(), r
    assert "carlos.feriado@koronet.com" not in google.events["series1_0"]["attendees"]
    assert "carlos.feriado@koronet.com" in google.events["series1_1"]["attendees"]
    assert len(_created_events()) == 1, "Product no crea eventos nuevos"

    # A session someone already booked by hand is linked, not proposed again
    google.corporate["Ana Existente"] = "ana.existente@koronet.com"
    google.events["moira_hr"] = {"title": "On - Boarding HR Koronet 🚀", "start": "2099-12-01T08:15:00",
                                 "end": "2099-12-01T09:15:00", "organizer": "moira.gago@koronet.com",
                                 "attendees": ["junior@koronet.com", "ana.existente@koronet.com", "moira.gago@koronet.com"]}
    bid3 = make_hire(name="Ana Existente", hire_date="2099-12-01")
    _set_hire(bid3, "Argentina")
    hr = _session(c, bid3, "hr")
    assert (hr["status"], hr["origin"], hr["eventId"], hr["startTime"]) == ("scheduled", "existing", "moira_hr", "08:15"), hr
    assert hr["organizerEmail"] == "moira.gago@koronet.com" and hr["extraAttendees"] == ["moira.gago@koronet.com"], hr
    assert c.post(f"/api/agent/meetings/{hr['id']}/schedule").json().get("note") == "Ya estaba agendado"
    assert len(_created_events()) == 1


def test_same_day_hires_share_sessions_by_language_and_country():
    from skills import holidays_check as hol
    _fake_bamboo()
    google.events.clear()
    google.busy.clear()
    google.event_fail_with = None
    day = date(2099, 3, 10)
    while not hol.is_business_day(day, ["Colombia", "Argentina", "Romania", "Netherlands"]):
        day += timedelta(days=1)
    google.events["series2_0"] = {"title": "Monthly Product Onboarding", "start": f"{day + timedelta(days=7)}T09:00:00",
                                  "end": f"{day + timedelta(days=7)}T10:00:00", "attendees": ["adrian.aubone@kometsales.com"],
                                  "recurring": "series2", "organizer": "adrian.aubone@kometsales.com"}
    people = [("Ana Colombia", "Colombia", "es"), ("Beto Argentina", "Argentina", "es"),
              ("Cora Romania", "Romania", "en"), ("Dirk Holland", "Netherlands", "en")]
    ids = {}
    for name, loc, lang in people:
        google.corporate[name] = name.split(" ")[0].lower() + "@koronet.com"
        ids[name] = make_hire(name=name, hire_date=day.isoformat(), language=lang)
        _set_hire(ids[name], loc, "... session with me at 08:30 am (hora Colombia).")
    c = client()
    rows = [x for x in c.get("/api/agent/meetings").json() if x["hireDate"] == day.isoformat()]
    by = {}
    for x in rows:
        by.setdefault(x["sessionType"], []).append(sorted(h["name"] for h in x["hires"]))
    assert by["hr"] == [["Ana Colombia", "Beto Argentina"]], by           # one HR in Spanish, with Junior
    assert by["hr_en"] == [["Cora Romania", "Dirk Holland"]], by          # one HR in English, with Moira
    assert by["it"] == [["Ana Colombia", "Beto Argentina"]], by           # IT: Spanish speakers only
    assert by["payroll"] == [["Ana Colombia"]], by                        # Payroll: Colombia only
    assert by["product"] == [sorted(n for n, _, _ in people)], by         # everyone, same Product
    assert len(by["first_month"]) == 4 and all(len(x) == 1 for x in by["first_month"]), by  # one each
    hr_en = next(x for x in rows if x["sessionType"] == "hr_en")
    assert hr_en["extraAttendees"] == ["moira.gago@koronet.com"] and hr_en["startTime"] == "08:30", hr_en

    # Schedule the Spanish HR once for both
    hr = next(x for x in rows if x["sessionType"] == "hr")
    r = c.post(f"/api/agent/meetings/{hr['id']}/schedule").json()
    assert r["ok"], r
    ev = google.events[r["meeting"]["eventId"]]
    assert ev["attendees"][:2] == ["ana@koronet.com", "beto@koronet.com"] and len(_created_events()) == 1, ev

    # Another Colombian starts the same day after HR was booked: joins pending IT/Payroll,
    # and is offered to be added to the SAME HR event (no second event)
    google.corporate["Eva Colombia"] = "eva@koronet.com"
    eva = make_hire(name="Eva Colombia", hire_date=day.isoformat(), language="es")
    _set_hire(eva, "Colombia")
    s2 = _sessions(c, eva)
    assert s2["hr"]["id"] == hr["id"] and s2["hr"]["missingHires"] == ["Eva Colombia"], s2["hr"]
    assert sorted(h["name"] for h in s2["payroll"]["hires"]) == ["Ana Colombia", "Eva Colombia"], s2["payroll"]
    assert len(s2["it"]["hires"]) == 3 and len(s2["product"]["hires"]) == 5
    r = c.post(f"/api/agent/meetings/{hr['id']}/add-hires").json()
    assert r["ok"] and not r["meeting"]["missingHires"], r
    assert "eva@koronet.com" in ev["attendees"] and len(_created_events()) == 1, ev

    # Product: the whole day goes to the same occurrence
    pr = s2["product"]
    r = c.post(f"/api/agent/meetings/{pr['id']}/schedule", json={"eventId": "series2_0"}).json()
    assert r["ok"] and len(r["meeting"]["invitedEmails"]) == 5, r
    assert set(google.events["series2_0"]["attendees"]) >= {"ana@koronet.com", "cora@koronet.com", "eva@koronet.com"}


def test_slots_follow_the_date_being_looked_at():
    from skills import holidays_check as hol
    google.events.clear()
    google.busy.clear()
    day = date(2099, 5, 4)
    while not hol.is_business_day(day, ["Colombia"]):
        day += timedelta(days=1)
    nxt = hol.next_business_day(day, ["Colombia"], include_today=False)
    _fake_bamboo(time_off=[("1", nxt.isoformat(), nxt.isoformat())])  # Jeisson out the day after
    google.corporate["Sara Slots"] = "sara.slots@koronet.com"
    bid = make_hire(name="Sara Slots", hire_date=day.isoformat(), language="es")
    _set_hire(bid, "Colombia", "... session with me at 08:00 am (hora Colombia).")
    c = client()
    rows = _sessions(c, bid)
    url = lambda k: f"/api/agent/meetings/{rows[k]['id']}/slots"

    # Payroll: Dani busy 11:00 on the HR day → that time isn't offered, others are; never before HR ends
    google.busy["danielp@koronet.com"] = [(f"{day}T11:00:00-05:00", f"{day}T11:30:00-05:00")]
    r = c.get(url("payroll"), params={"from_date": day.isoformat()}).json()
    same = [x["startTime"] for x in r["slots"] if x["startDate"] == day.isoformat()]
    assert same and "11:00" not in same and min(same) >= "09:00" and not r["notice"], r

    # IT: the user looks at a Saturday → notice + next business days (skipping Jeisson's day off)
    sat = day + timedelta(days=(5 - day.weekday()) % 7)
    r = c.get(url("it"), params={"from_date": sat.isoformat()}).json()
    assert "fin de semana" in r["notice"] and r["slots"] and all(x["startDate"] > sat.isoformat() for x in r["slots"]), r
    assert all(hol.is_business_day(x["startDate"], ["Colombia"]) for x in r["slots"])
    # Jeisson's day off → nothing that day; swapping the responsible (not saved) brings it back
    r = c.get(url("it"), params={"from_date": nxt.isoformat()}).json()
    assert "ausente" in r["notice"] and all(x["startDate"] != nxt.isoformat() for x in r["slots"]), r
    r = c.get(url("it"), params={"from_date": nxt.isoformat(), "hosts": "andres.soporte@koronet.com"}).json()
    assert "10:00" in [x["startTime"] for x in r["slots"] if x["startDate"] == nxt.isoformat()] and not r["notice"], r

    # First month: a later date → slots start from there, "día N" relative to the start date
    later = day + timedelta(days=45)
    r = c.get(url("first_month"), params={"from_date": later.isoformat()}).json()
    assert r["slots"] and all(x["startDate"] >= later.isoformat() for x in r["slots"]), r
    assert all(x["day"] == (date.fromisoformat(x["startDate"]) - day).days for x in r["slots"])
    # Everything busy that day → says so and moves on
    google.busy["guadalupe@kometsales.com"] = [(f"{later}T00:00:00-05:00", f"{later}T23:59:00-05:00")]
    r = c.get(url("first_month"), params={"from_date": later.isoformat()}).json()
    if hol.is_business_day(later, ["Colombia", "Argentina"]):
        assert "no hay un espacio libre" in r["notice"], r
    assert r["slots"] and all(x["startDate"] > later.isoformat() for x in r["slots"]), r
    assert not _created_events(), "buscar horarios nunca agenda"


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
