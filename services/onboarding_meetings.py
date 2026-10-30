"""
Onboarding sessions ("Agendamiento" tab).

Hires starting the same day share their sessions (one meeting, several hires):

  hr          On - Boarding HR Koronet 🚀 — Spanish speakers, with Junior, exactly as the
              welcome email states it (start date + "hh:mm am (hora Colombia)").
  hr_en       Same session for the non-Spanish speakers of that day — run by Moira. Only
              exists when such hires start that day (so a mixed day has two HR sessions).
  it          On - Boarding IT Koronet 💻 — Spanish speakers only (Jeisson covers the others
              on Slack); Jeisson, 10:00 CO, 30 min, first Colombian business day from HR on.
  payroll     On - Boarding Payroll Koronet - 📊 — Colombian hires only; Dani, 11:00 CO, 30 min.
  product     Adrian's recurring "Monthly Product Onboarding": every hire of the day is added
              to the SAME occurrence (the nearest on/after the start date), never the series.
  first_month "{Nombre} / Guada - First month ✨" — one per hire, 30 min with Guadalupe around
              day 30 (Mon–Fri, no holiday; day 29 or 28 when day 30 doesn't work).

groupKey is the start date for the shared sessions and "fm:<batchId>" for First month.
A hire who joins a day whose session is already on the calendar shows up as "falta
agregar" and is added to that same event with one click (never a second event).

Holidays (python-holidays, any country) decide the business days, and every card
carries alerts: holidays in Colombia / the hires' / the hosts' countries, hosts out of
office in BambooHR, and hosts already busy at that slot.

Nothing touches the calendar until Junior clicks "Agendar" / "Agregar" — that click is the
approval. Sessions already on his calendar (e.g. Moira booked HR herself) are linked
instead of proposed. Scheduled sessions can be moved ("Reagendar"), and moves made
directly in Google Calendar are synced back.

States: pending_approval → (scheduling, transient) → scheduled | failed; or skipped.
Every transition is a conditional UPDATE, so a double click can't create two events.
"""

import json
import os
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from db.schema import get_db
from skills import holidays_check as hol
from skills.country_rules import COUNTRY_TIMEZONES, SPAIN_KEYWORDS, SPAIN_TZ, BASE_TZ

DEFAULT_TITLE = "On - Boarding HR Koronet 🚀"
DEFAULT_DURATION = 60
DEFAULT_EXTRA_ATTENDEES = os.getenv(
    "ONBOARDING_EXTRA_ATTENDEES", "guadalupe@kometsales.com,moira.gago@koronet.com"
)
MOIRA = "moira.gago@koronet.com"
PRODUCT_ONBOARDING_TITLE = os.getenv("PRODUCT_ONBOARDING_TITLE", "Monthly Product Onboarding")

SESSION_TYPES = {
    "hr": {"label": "Onboarding HR", "title": DEFAULT_TITLE, "duration": DEFAULT_DURATION,
           "hosts": DEFAULT_EXTRA_ATTENDEES, "keyword": "boarding hr", "idPrefix": "pbonb"},
    "hr_en": {"label": "Onboarding HR (inglés · Moira)", "title": DEFAULT_TITLE, "duration": DEFAULT_DURATION,
              "hosts": MOIRA, "keyword": "boarding hr", "idPrefix": "pbonbe"},
    "it": {"label": "Onboarding IT", "title": "On - Boarding IT Koronet 💻", "duration": 30, "time": "10:00",
           "hosts": os.getenv("ONBOARDING_IT_HOST", "jeisson@kometsales.com"), "keyword": "boarding it",
           "idPrefix": "pbit"},
    "payroll": {"label": "Onboarding Payroll", "title": "On - Boarding Payroll Koronet - 📊", "duration": 30,
                "time": "11:00", "hosts": os.getenv("ONBOARDING_PAYROLL_HOST", "danielp@koronet.com"),
                "keyword": "boarding payroll", "idPrefix": "pbpr"},
    "product": {"label": "Product Onboarding", "title": PRODUCT_ONBOARDING_TITLE, "duration": 60, "hosts": "",
                "keyword": PRODUCT_ONBOARDING_TITLE.lower()},
    "first_month": {"label": "First month", "title": "{first} / Guada - First month ✨", "duration": 30,
                    "hosts": os.getenv("ONBOARDING_FIRST_MONTH_HOST", "guadalupe@kometsales.com"),
                    "keyword": "first month", "idPrefix": "pbfm"},
}
SESSION_ORDER = list(SESSION_TYPES)
JUNIOR = os.getenv("JUNIOR_EMAIL", "junior@koronet.com")

EVENT_ID_RE = re.compile(r"[a-v0-9]{5,1024}")
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
# "08:00 am (hora Colombia)" — the session time as written in the welcome email (both languages).
EMAIL_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?\s*\(hora Colombia\)", re.I)
WORKDAY = ("08:00", "16:30")  # Colombia hours in which First month slots are offered (Guada is +2h)


def _local_zone(location: str):
    loc = (location or "").lower()
    if any(k in loc for k in SPAIN_KEYWORDS):
        return SPAIN_TZ
    return next((v for k, v in COUNTRY_TIMEZONES.items() if k in loc), None)


def _time_from_email(content: str) -> str | None:
    m = EMAIL_TIME_RE.search(content or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _bogota(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(ZoneInfo(BASE_TZ))


def _slot(d: str, t: str, minutes: int):
    start = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(BASE_TZ))
    return start, start + timedelta(minutes=int(minutes or 30))


def _fmt(d: str) -> str:
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m")


# ── BambooHR directory / time off (cached 10 min) ───────────────────────────

_cache = {}


def _cached(key, loader, minutes=10):
    hit = _cache.get(key)
    if hit and datetime.now() - hit[0] < timedelta(minutes=minutes):
        return hit[1]
    val = loader()
    if val is not None:
        _cache[key] = (datetime.now(), val)
        return val
    return hit[1] if hit else None


def _bamboo(endpoint):
    from skills.bamboo import _bamboo_get
    return _bamboo_get(endpoint)


def _directory() -> list:
    def load():
        data = _bamboo("employees/directory")
        if data is None:
            return None
        return [{"id": str(e.get("id")), "email": e["workEmail"], "location": e.get("location") or "",
                 "name": e.get("displayName") or f"{e.get('firstName', '')} {e.get('lastName', '')}".strip(),
                 "firstName": e.get("preferredName") or e.get("firstName") or "", "jobTitle": e.get("jobTitle") or ""}
                for e in data.get("employees", []) if e.get("workEmail")]
    return _cached("directory", load) or []


def _person(email: str) -> dict:
    e = (email or "").lower()
    return next((p for p in _directory() if p["email"].lower() == e), {})


def _short_name(email: str) -> str:
    p = _person(email)
    return p.get("firstName") or p.get("name") or email


def _time_off() -> list:
    """BambooHR who's-out for the next 120 days: [{email, start, end}]."""
    def load():
        today = date.today()
        data = _bamboo(f"time_off/whos_out?start={today}&end={today + timedelta(days=120)}")
        if data is None:
            return None
        by_id = {p["id"]: p["email"] for p in _directory()}
        return [{"email": by_id.get(str(x.get("employeeId"))), "start": x.get("start"), "end": x.get("end")}
                for x in data if x.get("type") == "timeOff" and by_id.get(str(x.get("employeeId")))]
    return _cached("time_off", load) or []


def search_directory(query: str, limit: int = 8) -> list:
    """BambooHR directory search for the invitee pickers: name → work email."""
    people = _directory()
    if not people:
        raise RuntimeError("No pude leer el directorio de BambooHR")
    words = _norm(query).split()
    if not words:
        return []
    hits = [p for p in people if all(w in _norm(f"{p['name']} {p['email']}") for w in words)]
    return [{"name": p["name"], "email": p["email"], "jobTitle": p["jobTitle"]}
            for p in sorted(hits, key=lambda p: p["name"])[:limit]]


def _host_locations(hosts: list) -> list:
    return [_person(h).get("location") for h in hosts if _person(h).get("location")]


# ── Calendar lookups ────────────────────────────────────────────────────────

def _product_instances(google, from_date: str, count: int = 3) -> list:
    """Next occurrences of Adrian's recurring Product Onboarding starting on/after from_date."""
    if not google:
        return []
    start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(BASE_TZ))
    evs = google.list_events(start.isoformat(), (start + timedelta(days=120)).isoformat(), PRODUCT_ONBOARDING_TITLE)
    out = []
    for e in evs:
        if (e.get("summary") or "").strip().lower() != PRODUCT_ONBOARDING_TITLE.lower() or not e.get("recurringEventId"):
            continue
        s, en = _bogota(e["start"]["dateTime"]), _bogota(e["end"]["dateTime"])
        out.append({"eventId": e["id"], "startDate": s.strftime("%Y-%m-%d"), "startTime": s.strftime("%H:%M"),
                    "durationMinutes": int((en - s).total_seconds() // 60), "link": e.get("htmlLink"),
                    "organizer": (e.get("organizer") or {}).get("email"),
                    "attendees": [a.get("email", "").lower() for a in e.get("attendees", [])]})
    return out[:count]


def first_month_slots(google, hire_date: str, hosts: list, hire_location: str, limit: int = 12) -> list:
    """
    Free 30-min slots for the First month session: day 30 after the start date,
    else day 29 or 28 (so a weekend/holiday day 30 still lands that week), else
    the next business days. Mon–Fri, no holiday in Colombia / the hire's / the
    hosts' countries, and Junior + the hosts free.
    """
    hire = datetime.strptime(hire_date, "%Y-%m-%d").date()
    locs = [hol.BASE_COUNTRY, hire_location or "", *_host_locations(hosts)]
    days = [hire + timedelta(days=n) for n in (30, 29, 28)] + [hire + timedelta(days=n) for n in range(31, 42)]
    days = [d for d in days if hol.is_business_day(d, locs) and not _hosts_out(d, hosts)]
    return _free_slots(google, days, [JUNIOR, *hosts], 30, hire, limit=limit)


def _hosts_out(d: date, hosts: list) -> list:
    """Hosts on leave that day according to BambooHR."""
    lower = [h.lower() for h in hosts]
    return [o["email"] for o in _time_off() if o["email"].lower() in lower and o["start"] <= d.isoformat() <= o["end"]]


def _free_slots(google, days: list, people: list, minutes: int, hire: date, limit: int = 12, per_day: int = 4,
                not_before: datetime | None = None, prefer: str | None = None) -> list:
    """
    Free slots (every 30 min within WORKDAY, Colombia time) on the given days for all
    `people`. At most `per_day` per day so the proposals spread over several days; the
    session's usual time (`prefer`) goes first when it's free.
    """
    if not days:
        return []
    tz = ZoneInfo(BASE_TZ)
    lo = datetime.combine(min(days), datetime.min.time(), tz)
    hi = datetime.combine(max(days) + timedelta(days=1), datetime.min.time(), tz)
    people = list(dict.fromkeys(p for p in people if p))
    busy = google.busy_slots(people, lo.isoformat(), hi.isoformat(), BASE_TZ) if google else {}
    blocks = [(_bogota(s), _bogota(e)) for v in busy.values() if v for s, e in v]
    earliest = max(filter(None, [not_before, datetime.now(tz)]))
    out = []
    for d in days:
        t = datetime.combine(d, datetime.strptime(WORKDAY[0], "%H:%M").time(), tz)
        close = datetime.combine(d, datetime.strptime(WORKDAY[1], "%H:%M").time(), tz) + timedelta(minutes=30)
        free = []
        while t + timedelta(minutes=minutes) <= close:
            end = t + timedelta(minutes=minutes)
            if t >= earliest and not any(s < end and t < e for s, e in blocks):
                free.append(t.strftime("%H:%M"))
            t += timedelta(minutes=30)
        if prefer in free:
            free.remove(prefer)
            free.insert(0, prefer)
        for hm in sorted(free[:per_day]):
            out.append({"startDate": d.isoformat(), "startTime": hm, "day": (d - hire).days})
        if len(out) >= limit:
            break
    return out[:limit]


# ── Rows ────────────────────────────────────────────────────────────────────

def _emails(m: dict) -> list:
    return [h["email"] for h in m["hires"] if h.get("email")]


def _missing(m: dict) -> list:
    """Hires of a scheduled session who aren't on the calendar event yet."""
    if m["status"] != "scheduled":
        return []
    invited = {e.lower() for e in m["invitedEmails"]}
    return [h for h in m["hires"] if (h.get("email") or "").lower() not in invited]


def _row(r) -> dict:
    d = dict(r)
    d["extraAttendees"] = [a for a in (d.get("extraAttendees") or "").split(",") if a]
    d["hires"] = json.loads(d["hires"]) if d.get("hires") else (  # pre-group rows: one hire in attendeeEmail
        [{"batchId": d["batchId"], "name": d.get("employeeName"), "hireDate": d.get("startDate"),
          "location": d.get("employeeLocation") or "", "language": "es", "email": d["attendeeEmail"],
          "emailSource": d.get("emailSource")}] if d.get("attendeeEmail") else [])
    d["invitedEmails"] = json.loads(d.get("invitedEmails") or "[]")
    d["sessionLabel"] = SESSION_TYPES.get(d.get("sessionType") or "hr", {}).get("label")
    d["employeeName"] = ", ".join(h["name"] for h in d["hires"]) or d.get("employeeName")
    d["hireDate"] = d["hires"][0]["hireDate"] if d["hires"] else None
    d["missingHires"] = [h["name"] for h in _missing(d)]
    d["localTime"] = None
    try:
        start = datetime.strptime(f"{d['startDate']} {d['startTime']}", "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(BASE_TZ))
        times = []
        for h in d["hires"]:
            zone = _local_zone(h.get("location"))
            if zone and zone[0] != BASE_TZ:
                t = f"{start.astimezone(ZoneInfo(zone[0])).strftime('%H:%M')} (hora {zone[1]})"
                if t not in times:
                    times.append(t)
        d["localTime"] = " · ".join(times) or None
    except (ValueError, TypeError):
        pass
    return d


def _load(where: str = "", args=()) -> list:
    conn = get_db()
    try:
        return [_row(r) for r in conn.execute(f"SELECT * FROM onboarding_meetings {where}", args).fetchall()]
    finally:
        conn.close()


def get_meeting(meeting_id: int) -> dict | None:
    rows = _load("WHERE id=?", (meeting_id,))
    return rows[0] if rows else None


def _set(meeting_id: int, where: str = "", **f) -> int:
    for k in ("hires", "invitedEmails"):
        if k in f and not isinstance(f[k], str):
            f[k] = json.dumps(f[k], ensure_ascii=False)
    f["updatedAt"] = datetime.now().isoformat()
    conn = get_db()
    try:
        cur = conn.execute(f"UPDATE onboarding_meetings SET {', '.join(k + '=?' for k in f)} WHERE id=? {where}",
                           [*f.values(), meeting_id])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _backfill_v1_rows() -> None:
    """Rows from before group sessions carry one hire in attendeeEmail; turn that into hires/invitedEmails."""
    conn = get_db()
    try:
        old = conn.execute("SELECT o.*, (SELECT MIN(employeeHireDate) FROM messages WHERE batchId=o.batchId) AS hd, "
                           "(SELECT language FROM messages WHERE batchId=o.batchId AND messageType='employee_email') AS lang "
                           "FROM onboarding_meetings o WHERE hires IS NULL").fetchall()
    finally:
        conn.close()
    for r in old:
        hire = {"batchId": r["batchId"], "name": r["employeeName"], "hireDate": r["hd"] or r["startDate"],
                "location": r["employeeLocation"] or "", "language": r["lang"] or "es",
                "email": r["attendeeEmail"], "emailSource": r["emailSource"]}
        _set(r["id"], hires=[hire], invitedEmails=[r["attendeeEmail"]] if r["status"] == "scheduled" and r["attendeeEmail"] else [])


# ── Proposals ───────────────────────────────────────────────────────────────

def _upcoming_hires(google, rows: list) -> list:
    """Hires with a welcome email starting today or later, each with its corporate email from NEW HIRES."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    try:
        found = conn.execute(
            "SELECT batchId, employeeName, employeeHireDate, employeeLocation, language, messageContent FROM messages "
            "WHERE messageType='employee_email' AND employeeHireDate >= ? AND status NOT IN ('rejected')",
            (today,)).fetchall()
    finally:
        conn.close()
    known = {}
    for m in rows:
        for h in m["hires"]:
            if h.get("email") and (h["batchId"] not in known or h.get("emailSource") == "editado por Junior"):
                known[h["batchId"]] = h
    out = []
    for r in found:
        k = known.get(r["batchId"])
        if k:
            email, source, error = k["email"], k.get("emailSource"), None
        elif google:
            look = _cached(f"mail:{r['batchId']}", lambda: google.find_corporate_email(r["employeeName"], r["employeeHireDate"]),
                           minutes=5) or {}
            email, source, error = look.get("email"), look.get("source"), look.get("error")
        else:
            email, source, error = None, None, "Google no está conectado"
        out.append({"batchId": r["batchId"], "name": r["employeeName"], "hireDate": r["employeeHireDate"],
                    "location": r["employeeLocation"] or "", "language": (r["language"] or "es").lower()[:2],
                    "email": email, "emailSource": source, "emailError": error,
                    "time": _time_from_email(r["messageContent"])})
    return out


def _wanted_sessions(hires: list) -> list:
    """(sessionType, groupKey, members) for every session the upcoming hires need."""
    out = []
    for day in sorted({h["hireDate"] for h in hires}):
        group = [h for h in hires if h["hireDate"] == day]
        es = [h for h in group if h["language"] == "es"]
        other = [h for h in group if h["language"] != "es"]
        co = [h for h in group if hol.country_code(h["location"]) == "CO"]
        out += [("hr", day, es), ("hr_en", day, other), ("it", day, es), ("payroll", day, co), ("product", day, group)]
        out += [("first_month", f"fm:{h['batchId']}", [h]) for h in group]
    return [w for w in out if w[2]]


def _hire_record(h: dict) -> dict:
    return {k: h.get(k) for k in ("batchId", "name", "hireDate", "location", "language", "email", "emailSource")}


def _new_row(google, kind: str, key: str, members: list, by_key: dict) -> dict:
    cfg = SESSION_TYPES[kind]
    hosts = [x for x in cfg["hosts"].split(",") if x]
    day = members[0]["hireDate"]
    first = (members[0]["name"] or "").split(" ")[0]
    row = dict(batchId=min(h["batchId"] for h in members), sessionType=kind, groupKey=key,
               hires=json.dumps([_hire_record(h) for h in members], ensure_ascii=False), invitedEmails="[]",
               extraAttendees=",".join(hosts), title=cfg["title"].format(first=first),
               durationMinutes=cfg["duration"], status="pending_approval", createdAt=datetime.now().isoformat())
    locs = [h["location"] for h in members]
    if kind in ("hr", "hr_en"):
        row.update(startDate=day, startTime=next((h["time"] for h in members if h.get("time")), None) or "08:00")
    elif kind in ("it", "payroll"):
        # Hosts work from Colombia: first Colombian (and hires' country) business day from the HR session on.
        hr_date = (by_key.get((key, "hr")) or {}).get("startDate") or day
        d = hol.next_business_day(hr_date, [hol.BASE_COUNTRY, *locs, *_host_locations(hosts)])
        row.update(startDate=d.isoformat(), startTime=cfg["time"])
    elif kind == "product":
        inst = _product_instances(google, day, 1)
        if inst:
            row.update(startDate=inst[0]["startDate"], startTime=inst[0]["startTime"], origin="instance",
                       durationMinutes=inst[0]["durationMinutes"], eventId=inst[0]["eventId"], eventLink=inst[0]["link"])
        else:
            row.update(startDate=day, startTime="09:00", origin="instance",
                       error=f"No encontré la próxima '{PRODUCT_ONBOARDING_TITLE}' en tu calendario")
    elif kind == "first_month":
        slots = first_month_slots(google, day, hosts, locs[0], 1) if google else []
        if slots:
            row.update(startDate=slots[0]["startDate"], startTime=slots[0]["startTime"])
        else:
            row.update(startDate=(date.fromisoformat(day) + timedelta(days=30)).isoformat(), startTime="09:00")
    missing = [h["name"] for h in members if not h.get("email")]
    if missing:
        row["error"] = next((h["emailError"] for h in members if h.get("emailError")), None) or \
            f"Falta el correo corporativo de {', '.join(missing)} (NEW HIRES)"
    return row


def ensure_proposals(google) -> None:
    """
    Keeps one proposal per (session, day) with the right hires: creates the missing ones,
    updates the members of pending ones, and marks hires who joined an already scheduled
    session (they're added to that same event when Junior approves).
    """
    _backfill_v1_rows()
    rows = _load()
    hires = _upcoming_hires(google, rows)
    by_key = {(m["groupKey"], m["sessionType"]): m for m in rows}
    upcoming_ids = {h["batchId"] for h in hires}
    wanted = _wanted_sessions(hires)
    wanted_keys = {(k, t) for t, k, _ in wanted}

    # A hire whose start date changed leaves the pending sessions of the old day.
    for m in rows:
        if (m["groupKey"], m["sessionType"]) in wanted_keys or m["status"] not in ("pending_approval", "failed"):
            continue
        ids = {h["batchId"] for h in m["hires"]}
        if ids and ids <= upcoming_ids:
            conn = get_db()
            try:
                conn.execute("DELETE FROM onboarding_meetings WHERE id=? AND status IN ('pending_approval','failed')",
                             (m["id"],))
                conn.commit()
            finally:
                conn.close()

    for kind, key, members in wanted:
        cur = by_key.get((key, kind))
        if not cur:
            row = _new_row(google, kind, key, members, by_key)
            conn = get_db()
            try:
                conn.execute(f"INSERT OR IGNORE INTO onboarding_meetings ({', '.join(row)}) "
                             f"VALUES ({', '.join('?' * len(row))})", list(row.values()))
                conn.commit()
            finally:
                conn.close()
            by_key[(key, kind)] = {"startDate": row["startDate"]}
            continue
        old = {h["batchId"]: h for h in cur["hires"]}
        if cur["status"] in ("pending_approval", "failed"):
            # Members follow the current hires of the day; an email Junior typed wins over the sheet.
            new = [_hire_record(old[h["batchId"]] if old.get(h["batchId"], {}).get("emailSource") == "editado por Junior"
                                else {**h, "email": h.get("email") or old.get(h["batchId"], {}).get("email")})
                   for h in members]
        else:
            new = cur["hires"] + [_hire_record(h) for h in members if h["batchId"] not in old]
            new = [{**x, "email": x.get("email") or next((h["email"] for h in members if h["batchId"] == x["batchId"]), None)}
                   for x in new]
        if new != cur["hires"]:
            fields = {"hires": new}
            missing = [h["name"] for h in new if not h.get("email")]
            if cur["status"] == "pending_approval":
                fields["error"] = f"Falta el correo corporativo de {', '.join(missing)} (NEW HIRES)" if missing else None
            _set(cur["id"], **fields)


def _recover_interrupted() -> None:
    """A 'scheduling' left behind by a restart becomes 'failed'; retrying reuses its eventId (no duplicate)."""
    cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE onboarding_meetings SET status='failed', "
            "error='Se interrumpió mientras se agendaba — dale Agendar de nuevo (no se duplica)' "
            "WHERE status='scheduling' AND updatedAt < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def _link_existing_and_sync(google, rows: list) -> None:
    """
    Pending sessions already on Junior's calendar (someone booked them by hand) are
    linked instead of proposed; scheduled ones follow moves/deletions/guest changes
    made in Calendar.
    """
    if not google or not rows:
        return
    pending = [r for r in rows if r["status"] in ("pending_approval", "failed") and _emails(r)]
    if pending:
        lo = (date.fromisoformat(min(r["startDate"] for r in pending)) - timedelta(days=10)).isoformat()
        hi = (date.fromisoformat(max(r["startDate"] for r in pending)) + timedelta(days=15)).isoformat()
        events = google.list_events(f"{lo}T00:00:00-05:00", f"{hi}T23:59:59-05:00")
        for r in pending:
            kw, mails = SESSION_TYPES[r["sessionType"]]["keyword"], {e.lower() for e in _emails(r)}
            hit = next((e for e in events if kw in (e.get("summary") or "").lower()
                        and e.get("status") != "cancelled" and (e.get("start") or {}).get("dateTime")
                        and mails & {a.get("email", "").lower() for a in e.get("attendees", [])}), None)
            if hit:
                s, en = _bogota(hit["start"]["dateTime"]), _bogota(hit["end"]["dateTime"])
                guests = [a.get("email", "").lower() for a in hit.get("attendees", [])]
                product = r["sessionType"] == "product"
                extra = {} if product else {"extraAttendees": ",".join(g for g in guests if g not in mails and g != JUNIOR.lower())}
                _set(r["id"], "AND status IN ('pending_approval','failed')", status="scheduled",
                     origin="instance" if product else "existing",
                     organizerEmail=(hit.get("organizer") or {}).get("email"), **extra,
                     invitedEmails=[e for e in _emails(r) if e.lower() in guests],
                     eventId=hit["id"], eventLink=hit.get("htmlLink"), meetLink=hit.get("hangoutLink"),
                     startDate=s.strftime("%Y-%m-%d"), startTime=s.strftime("%H:%M"),
                     durationMinutes=int((en - s).total_seconds() // 60), error=None,
                     scheduledAt=datetime.now().isoformat())

    today = datetime.now().strftime("%Y-%m-%d")
    for r in rows:
        if r["status"] != "scheduled" or not r.get("eventId") or r["startDate"] < today:
            continue
        ev = google.get_event(r["eventId"])
        inst = r.get("origin") == "instance"
        guests = {a.get("email", "").lower() for a in (ev or {}).get("attendees", [])}
        invited = [e for e in _emails(r) if e.lower() in guests]
        if ev is None or ev.get("status") == "cancelled" or (inst and not invited):
            _set(r["id"], "AND status='scheduled'", status="pending_approval" if inst else "failed",
                 eventId=r["eventId"] if inst else None, origin="instance" if inst else "created",
                 eventLink=None, meetLink=None, invitedEmails=[],
                 error="Ya no está en tu calendario (la borraron o quitaron a los ingresos) — dale Agendar para volver a crearla")
            continue
        fields = {}
        if invited != r["invitedEmails"]:
            fields["invitedEmails"] = invited
        if (ev.get("start") or {}).get("dateTime"):
            s, en = _bogota(ev["start"]["dateTime"]), _bogota(ev["end"]["dateTime"])
            new = (s.strftime("%Y-%m-%d"), s.strftime("%H:%M"), int((en - s).total_seconds() // 60))
            if new != (r["startDate"], r["startTime"], r["durationMinutes"]):
                fields.update(startDate=new[0], startTime=new[1], durationMinutes=new[2])
        if r.get("origin") == "existing":
            # Someone else's event: show its real guests and organizer (they may change them).
            mails = {e.lower() for e in _emails(r)}
            extra = ",".join(g for g in sorted(guests) if g not in mails and g != JUNIOR.lower())
            org = (ev.get("organizer") or {}).get("email")
            if (extra, org) != (",".join(sorted(r["extraAttendees"])), r.get("organizerEmail")):
                fields.update(extraAttendees=extra, organizerEmail=org)
        if fields:
            _set(r["id"], **fields)


# ── Alerts ──────────────────────────────────────────────────────────────────

def _alerts(m: dict, busy: dict, by_key: dict) -> list:
    out = []
    t = m["sessionType"]
    try:
        start, end = _slot(m["startDate"], m["startTime"], m["durationMinutes"])
    except (ValueError, TypeError):
        return out
    d = start.date()
    hosts = m["extraAttendees"]

    if m["missingHires"]:
        out.append({"level": "warn", "text": f"Falta agregar a {', '.join(m['missingHires'])} (ingresa el mismo día) "
                                             f"— dale ➕ Agregar para sumarlo a esta misma sesión"})
    if d.weekday() >= 5:
        out.append({"level": "warn", "text": "Cae en fin de semana"})
    base = hol.holiday_name(d, hol.BASE_COUNTRY)
    if base:
        if t == "hr":
            who = "Moira está invitada para darla" if MOIRA in hosts else "agrega a Moira para que ella la dé"
            out.append({"level": "warn", "text": f"Feriado en Colombia ({base}): ese día no trabajas — {who}"})
        elif t not in ("product", "hr_en"):
            out.append({"level": "warn", "text": f"Feriado en Colombia ({base})"})
    seen = {"CO"}
    for h in m["hires"]:
        code = hol.country_code(h.get("location") or "")
        name = hol.holiday_name(d, h.get("location") or "")
        if name and code not in seen:
            seen.add(code)
            who = ", ".join(x["name"].split(" ")[0] for x in m["hires"] if hol.country_code(x.get("location") or "") == code)
            out.append({"level": "warn", "text": f"Feriado en {h['location']} para {who} ({name})"})
    for h in hosts:
        loc = _person(h).get("location") or ""
        name = hol.holiday_name(d, loc) if loc else None
        if name and hol.country_code(loc) not in seen:
            out.append({"level": "warn", "text": f"Feriado en {loc} para {_short_name(h)} ({name})"})

    for off in _time_off():
        if off["email"] and off["email"].lower() in [x.lower() for x in hosts] and off["start"] <= m["startDate"] <= off["end"]:
            out.append({"level": "warn", "text": f"{_short_name(off['email'])} está ausente según BambooHR "
                                                 f"({_fmt(off['start'])}–{_fmt(off['end'])}) — cambia el responsable o la fecha"})

    if t in ("it", "payroll"):
        hr = by_key.get((m["groupKey"], "hr"))
        if hr and hr.get("startDate") and hr["status"] != "skipped":
            hr_start, hr_end = _slot(hr["startDate"], hr["startTime"], hr["durationMinutes"])
            if start < hr_end:
                out.append({"level": "warn", "text": f"Queda antes de que termine la sesión de HR "
                                                     f"({_fmt(hr['startDate'])} {hr_end.strftime('%H:%M')})"})
    if t == "first_month" and m["hireDate"]:
        day = (d - date.fromisoformat(m["hireDate"])).days
        out.append({"level": "info" if 28 <= day <= 30 else "warn", "text": f"Día {day} desde su ingreso"})

    if m["status"] in ("pending_approval", "failed") and t != "product":
        junior_needed = not (t == "hr" and base) and t != "hr_en"
        checks = list(dict.fromkeys(hosts + ([JUNIOR] if junior_needed else [])))
        free, busy_names, unknown = [], [], []
        for who in checks:
            slots = busy.get(who)
            name = "tú" if who == JUNIOR else _short_name(who)
            if slots is None:
                unknown.append(name)
            elif any(_bogota(s) < end and start < _bogota(e) for s, e in slots):
                busy_names.append(name)
            else:
                free.append(name)
        if busy_names:
            out.append({"level": "warn", "text": f"Ocupado a esa hora: {', '.join(busy_names)} — revisa antes de agendar"})
        if free:
            out.append({"level": "ok", "text": f"Libre a esa hora: {', '.join(free)}"})
        if unknown:
            out.append({"level": "info", "text": f"No pude ver el calendario de: {', '.join(unknown)}"})
    if m.get("origin") == "existing":
        org = m.get("organizerEmail")
        by = f" (la organiza {_short_name(org)})" if org and org.lower() != JUNIOR.lower() else ""
        out.append({"level": "info", "text": f"Ya estaba en tu calendario{by}: la vinculé en vez de crear otra"})
    return out


def list_meetings(google) -> list:
    _recover_interrupted()
    ensure_proposals(google)
    today = datetime.now().strftime("%Y-%m-%d")

    def current():
        return [m for m in _load() if m["startDate"] >= today or m["status"] in ("pending_approval", "failed")
                or (m["hireDate"] or "") >= today]
    rows = current()
    _link_existing_and_sync(google, rows)
    rows = current()

    by_key = {(m["groupKey"], m["sessionType"]): m for m in rows}
    pending = [m for m in rows if m["status"] in ("pending_approval", "failed") and m["sessionType"] != "product"]
    busy = {}
    if google and pending:
        people = sorted({p for m in pending for p in [JUNIOR, *m["extraAttendees"]]})
        lo = min(m["startDate"] for m in pending)
        hi = (date.fromisoformat(max(m["startDate"] for m in pending)) + timedelta(days=1)).isoformat()
        busy = google.busy_slots(people, f"{lo}T00:00:00-05:00", f"{hi}T00:00:00-05:00", BASE_TZ)

    product_opts = {}
    for m in rows:
        m["alerts"] = _alerts(m, busy, by_key)
        m["extraNames"] = {e: _person(e).get("name", "") for e in m["extraAttendees"]}
        if m["sessionType"] == "product" and google:
            hd = m["hireDate"] or m["startDate"]
            if hd not in product_opts:
                product_opts[hd] = _product_instances(google, hd, 3)
            m["productOptions"] = product_opts[hd]
    rows.sort(key=lambda m: (m["hireDate"] or "", SESSION_ORDER.index(m["sessionType"]), m["employeeName"] or ""))
    return rows


SLOT_TYPES = ("first_month", "it", "payroll")


def meeting_slots(meeting_id: int, google, from_date: str | None = None, hosts: list | None = None,
                  minutes: int | None = None) -> dict:
    """
    Suggested free times for First month / IT / Payroll. Without from_date: the default
    window (First month around day 30, IT/Payroll from the HR day). With from_date (the
    date Junior is looking at): that day first, then the following business days.
    `hosts` / `minutes` are the values on screen, even if not saved yet.
    `notice` explains why the chosen day itself has no slot.
    """
    m = get_meeting(meeting_id)
    if not m or m["sessionType"] not in SLOT_TYPES or not m["hires"]:
        return {"slots": [], "notice": None}
    t = m["sessionType"]
    hosts = [h for h in (hosts if hosts is not None else m["extraAttendees"]) if h]
    minutes = int(minutes or m["durationMinutes"] or 30)
    hire = date.fromisoformat(m["hireDate"] or m["startDate"])
    locs = [hol.BASE_COUNTRY, *[h.get("location") or "" for h in m["hires"]], *_host_locations(hosts)]

    not_before = None
    if t in ("it", "payroll"):
        hr = next(iter(_load("WHERE groupKey=? AND sessionType='hr' AND status!='skipped'", (m["groupKey"],))), None)
        if hr and hr.get("startDate") and hr.get("startTime"):
            not_before = _slot(hr["startDate"], hr["startTime"], hr["durationMinutes"])[1]

    if not from_date:
        if t == "first_month":
            return {"slots": first_month_slots(google, hire.isoformat(), hosts, m["hires"][0].get("location")),
                    "notice": None}
        from_date = not_before.date().isoformat() if not_before else hire.isoformat()
    try:
        start = date.fromisoformat(from_date)
    except ValueError:
        return {"slots": [], "notice": "Fecha inválida"}

    days = [start + timedelta(days=n) for n in range(0, 21)]
    days = [d for d in days if hol.is_business_day(d, locs) and not _hosts_out(d, hosts)][:6]
    slots = _free_slots(google, days, [JUNIOR, *hosts], minutes, hire, limit=12,
                        not_before=not_before, prefer=SESSION_TYPES[t].get("time"))

    notice = None
    if not any(s["startDate"] == start.isoformat() for s in slots):
        hols = hol.holidays_on(start, locs)
        out = _hosts_out(start, hosts)
        if start.weekday() >= 5:
            why = "es fin de semana"
        elif hols:
            why = "es feriado (" + "; ".join(f"{x['country']}: {x['name']}" for x in hols) + ")"
        elif out:
            why = f"{', '.join(_short_name(e) for e in out)} está ausente según BambooHR"
        elif not_before and start < not_before.date():
            why = "es antes de la sesión de HR"
        else:
            why = "no hay un espacio libre en común"
        nxt = " Te muestro los siguientes días con espacio." if slots else " Tampoco encontré espacio en los días siguientes."
        notice = f"El {_fmt(start.isoformat())} {why}.{nxt}"
    return {"slots": slots, "notice": notice}


# ── Edit / schedule / add hires / reschedule / skip ─────────────────────────

def _validate(m: dict, fields: dict) -> tuple[dict, str | None]:
    updates = {}
    if "startDate" in fields:
        try:
            datetime.strptime(fields["startDate"], "%Y-%m-%d")
        except (ValueError, TypeError):
            return {}, "Fecha inválida (usa AAAA-MM-DD)"
        updates["startDate"] = fields["startDate"]
    if "startTime" in fields:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", fields["startTime"] or ""):
            return {}, "Hora inválida (usa HH:MM, hora Colombia)"
        updates["startTime"] = fields["startTime"]
    if "durationMinutes" in fields:
        try:
            dur = int(fields["durationMinutes"])
        except (ValueError, TypeError):
            return {}, "Duración inválida"
        if not 5 <= dur <= 480:
            return {}, "La duración debe estar entre 5 y 480 minutos"
        updates["durationMinutes"] = dur
    if "hireEmails" in fields:
        hires = [dict(h) for h in m["hires"]]
        for h in hires:
            email = (fields["hireEmails"].get(str(h["batchId"])) or "").strip()
            if str(h["batchId"]) not in fields["hireEmails"]:
                continue
            if email and not EMAIL_RE.match(email):
                return {}, f"Correo inválido para {h['name']}: {email}"
            if email != (h.get("email") or ""):
                h.update(email=email or None, emailSource="editado por Junior")
        if hires != m["hires"]:
            updates["hires"] = hires
    if "extraAttendees" in fields:
        extras = fields["extraAttendees"]
        if isinstance(extras, str):
            extras = re.split(r"[,;\s]+", extras)
        extras = [e.strip() for e in extras if e and e.strip()]
        bad = [e for e in extras if not EMAIL_RE.match(e)]
        if bad:
            return {}, f"Correo(s) inválido(s): {', '.join(bad)}"
        updates["extraAttendees"] = ",".join(dict.fromkeys(extras))
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title:
            return {}, "El título no puede quedar vacío"
        updates["title"] = title
    if "eventId" in fields and m["sessionType"] == "product":
        updates["eventId"] = fields["eventId"]
    return updates, None


def _propagate_emails(m: dict, hires: list) -> None:
    """An email Junior typed for a hire applies to that hire's other pending sessions too."""
    edited = {h["batchId"]: h for h in hires if h.get("emailSource") == "editado por Junior"}
    if not edited:
        return
    for other in _load("WHERE id != ? AND status IN ('pending_approval','failed')", (m["id"],)):
        new = [{**h, "email": edited[h["batchId"]]["email"], "emailSource": "editado por Junior"}
               if h["batchId"] in edited else h for h in other["hires"]]
        if new != other["hires"]:
            _set(other["id"], hires=new)


def update_meeting(meeting_id: int, fields: dict, google=None) -> dict:
    """Edits a proposal before it is scheduled. Validates everything Junior typed."""
    m = get_meeting(meeting_id)
    if not m:
        return {"ok": False, "error": "Agendamiento no encontrado"}
    if m["status"] not in ("pending_approval", "failed"):
        return {"ok": False, "error": f"Ya no se puede editar (estado: {m['status']})"}
    updates, err = _validate(m, fields)
    if err:
        return {"ok": False, "error": err}
    if m["sessionType"] == "product" and "eventId" in updates:
        inst = next((i for i in _product_instances(google, m["hireDate"] or m["startDate"], 6)
                     if i["eventId"] == updates["eventId"]), None)
        if not inst:
            return {"ok": False, "error": "Esa fecha de Product Onboarding ya no está en tu calendario"}
        updates.update(startDate=inst["startDate"], startTime=inst["startTime"],
                       durationMinutes=inst["durationMinutes"], eventLink=inst["link"])
    if "hires" in updates and not [h for h in updates["hires"] if not h.get("email")]:
        updates["error"] = None
    if updates:
        if not _set(meeting_id, "AND status IN ('pending_approval','failed')", **updates):
            return {"ok": False, "error": "Ya no se puede editar: alguien lo agendó mientras tanto"}
        if "hires" in updates:
            _propagate_emails(m, updates["hires"])
    return {"ok": True, "meeting": get_meeting(meeting_id)}


EVENT_DESCRIPTION = (
    "Hi there! 👋\n\n"
    "This session is part of your onboarding at Koronet. It's a relaxed space to help you "
    "get started, so feel free to bring any questions.\n\n"
    "Can't make it? Just reply to this invite and we'll find another time.\n\n"
    "See you soon! 🚀"
)


def _description(m: dict) -> str:
    # Same friendly text for every session Junior creates (Product is Adrian's event: untouched).
    return EVENT_DESCRIPTION


def _add_to_instance(google, event_id: str, emails: list) -> tuple[list, str | None]:
    added, err = [], None
    for e in emails:
        res = google.set_attendee(event_id, e, True)
        if res.get("success"):
            added.append(e)
        else:
            err = res.get("error")
    return added, err


def schedule_meeting(meeting_id: int, google) -> dict:
    """Junior's approval: creates the event (or adds the hires to one Product Onboarding occurrence). Never raises."""
    m = get_meeting(meeting_id)
    if not m:
        return {"ok": False, "error": "Agendamiento no encontrado"}
    if m["status"] == "scheduled":
        return {"ok": True, "meeting": m, "note": "Ya estaba agendado"}
    missing = [h["name"] for h in m["hires"] if not h.get("email")]
    if missing or not m["hires"]:
        return {"ok": False, "error": f"Falta el correo corporativo de {', '.join(missing) or 'el ingreso'} (NEW HIRES)",
                "meeting": m}
    if not google:
        return {"ok": False, "error": "Google Calendar no está conectado", "meeting": m}

    product = m["sessionType"] == "product"
    if product:
        event_id = m.get("eventId")
        if not event_id:
            return {"ok": False, "error": f"Elige una fecha de {PRODUCT_ONBOARDING_TITLE}", "meeting": m}
    else:
        # Google only accepts base32hex ids (a-v, 0-9, 5-1024 chars); an id it rejected
        # never created an event, so replacing it can't duplicate anything. The id is
        # reused on retries, so a timeout + retry finds the same event.
        event_id = m.get("eventId")
        if not (event_id and EVENT_ID_RE.fullmatch(event_id)):
            prefix = SESSION_TYPES[m["sessionType"]]["idPrefix"]
            event_id = f"{prefix}{abs(int(m['batchId']))}{uuid.uuid4().hex[:12]}"
    if not _set(meeting_id, "AND status IN ('pending_approval','failed')", status="scheduling", eventId=event_id):
        return {"ok": False, "error": "Ya se está agendando (doble clic)", "meeting": get_meeting(meeting_id)}

    invited = []
    try:
        if product:
            invited, err = _add_to_instance(google, event_id, _emails(m))
            res = {"success": bool(invited), "error": err, "link": m.get("eventLink")}
        else:
            start, end = _slot(m["startDate"], m["startTime"], m["durationMinutes"] or DEFAULT_DURATION)
            res = google.create_onboarding_event(
                event_id=event_id, title=m["title"] or SESSION_TYPES[m["sessionType"]]["title"],
                start_iso=start.strftime("%Y-%m-%dT%H:%M:00"), end_iso=end.strftime("%Y-%m-%dT%H:%M:00"),
                time_zone=BASE_TZ, attendees=list(dict.fromkeys([*_emails(m), *m["extraAttendees"]])),
                description=_description(m))
            invited = _emails(m)
    except Exception as e:
        res = {"success": False, "error": str(e)}

    if res.get("success"):
        _set(meeting_id, status="scheduled", eventLink=res.get("link"), meetLink=res.get("meetLink"), error=None,
             invitedEmails=invited, scheduledAt=datetime.now().isoformat())
    else:
        _set(meeting_id, status="failed", error=res.get("error") or "error desconocido")
    return {"ok": bool(res.get("success")), "error": res.get("error"), "meeting": get_meeting(meeting_id)}


def add_missing_hires(meeting_id: int, google) -> dict:
    """Adds hires who joined the day after the session was scheduled to that same event."""
    m = get_meeting(meeting_id)
    if not m:
        return {"ok": False, "error": "Agendamiento no encontrado"}
    missing = _missing(m)
    if not missing:
        return {"ok": True, "meeting": m, "note": "No falta nadie"}
    if [h for h in missing if not h.get("email")]:
        return {"ok": False, "error": "Falta el correo corporativo de "
                + ", ".join(h["name"] for h in missing if not h.get("email"))}
    added, err = _add_to_instance(google, m["eventId"], [h["email"] for h in missing])
    if added:
        _set(meeting_id, invitedEmails=m["invitedEmails"] + added)
    if err:
        if "403" in err or "forbidden" in err.lower():
            err = "Esta sesión la organiza otra persona: pídele que agregue al ingreso"
        return {"ok": False, "error": err, "meeting": get_meeting(meeting_id)}
    return {"ok": True, "meeting": get_meeting(meeting_id)}


def reschedule_meeting(meeting_id: int, fields: dict, google) -> dict:
    """Moves an already scheduled session (new date/time, or another Product Onboarding occurrence)."""
    m = get_meeting(meeting_id)
    if not m:
        return {"ok": False, "error": "Agendamiento no encontrado"}
    if m["status"] != "scheduled" or not m.get("eventId"):
        return {"ok": False, "error": "Solo se puede reagendar una sesión ya agendada"}
    if not google:
        return {"ok": False, "error": "Google Calendar no está conectado"}

    if m["sessionType"] == "product":
        new_id = fields.get("eventId")
        inst = next((i for i in _product_instances(google, m["hireDate"] or m["startDate"], 6)
                     if i["eventId"] == new_id), None)
        if not inst:
            return {"ok": False, "error": "Elige otra fecha de Product Onboarding"}
        if new_id == m["eventId"]:
            return {"ok": True, "meeting": m}
        added, err = _add_to_instance(google, new_id, m["invitedEmails"] or _emails(m))
        if not added:
            return {"ok": False, "error": err}
        for e in added:
            google.set_attendee(m["eventId"], e, False)
        _set(meeting_id, eventId=new_id, eventLink=inst["link"], invitedEmails=added, startDate=inst["startDate"],
             startTime=inst["startTime"], durationMinutes=inst["durationMinutes"])
        return {"ok": True, "meeting": get_meeting(meeting_id)}

    merged = {k: fields.get(k, m[k]) for k in ("startDate", "startTime", "durationMinutes")}
    updates, err = _validate(m, merged)
    if err:
        return {"ok": False, "error": err}
    start, end = _slot(updates["startDate"], updates["startTime"], updates["durationMinutes"])
    res = google.move_event(m["eventId"], start.strftime("%Y-%m-%dT%H:%M:00"), end.strftime("%Y-%m-%dT%H:%M:00"), BASE_TZ)
    if not res.get("success"):
        err = res.get("error") or ""
        if "403" in err or "forbidden" in err.lower():
            err = "Esta sesión la organiza otra persona: pídele que la mueva desde su calendario"
        return {"ok": False, "error": err}
    _set(meeting_id, **updates)
    return {"ok": True, "meeting": get_meeting(meeting_id)}


def skip_meeting(meeting_id: int) -> dict:
    if not _set(meeting_id, "AND status IN ('pending_approval','failed')", status="skipped"):
        return {"ok": False, "error": "Solo se puede omitir un agendamiento pendiente o fallido"}
    return {"ok": True, "meeting": get_meeting(meeting_id)}
