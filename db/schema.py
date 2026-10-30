"""
Panda Bear — Database Schema
All tables for the agent's persistent state and memory.
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "panda_bear.db"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── OPERATIONAL TABLES (inherited from v3) ──────────────────

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
            batchId INTEGER,
            FOREIGN KEY(batchId) REFERENCES batches(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            createdAt TEXT NOT NULL,
            status TEXT DEFAULT 'pending_approval',
            messageCount INTEGER DEFAULT 0,
            employeeName TEXT,
            approvedAt TEXT,
            rejectedAt TEXT
        )
    """)

    # ── AGENT MEMORY TABLES ─────────────────────────────────────

    c.execute("""
        CREATE TABLE IF NOT EXISTS employee_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bambooId TEXT UNIQUE,
            employeeNumber TEXT,
            name TEXT,
            email TEXT,
            country TEXT,
            language TEXT,
            hireDate TEXT,
            jobTitle TEXT,
            department TEXT,
            managerName TEXT,
            onboardingStatus TEXT DEFAULT 'detected',
            batchId INTEGER,
            imagePath TEXT,
            notes TEXT,
            createdAt TEXT NOT NULL,
            updatedAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS manager_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slackId TEXT,
            communicationStyle TEXT DEFAULT 'professional',
            preferredLanguage TEXT DEFAULT 'es',
            messagesSent INTEGER DEFAULT 0,
            avgResponseDays REAL,
            notes TEXT,
            updatedAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS country_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT UNIQUE NOT NULL,
            language TEXT NOT NULL,
            timezone TEXT,
            onboardingTime TEXT,
            ccEmail TEXT,
            notes TEXT,
            updatedAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS approval_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            messageId INTEGER,
            employeeName TEXT,
            messageType TEXT,
            action TEXT NOT NULL,
            originalContent TEXT,
            finalContent TEXT,
            hrNotes TEXT,
            createdAt TEXT NOT NULL,
            FOREIGN KEY(messageId) REFERENCES messages(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons_learned (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employeeName TEXT,
            employeeId TEXT,
            country TEXT,
            department TEXT,
            messagesApproved INTEGER DEFAULT 0,
            messagesEdited INTEGER DEFAULT 0,
            messagesRejected INTEGER DEFAULT 0,
            whatWorked TEXT,
            whatFailed TEXT,
            insights TEXT,
            createdAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skillName TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasoning TEXT,
            context TEXT,
            outcome TEXT,
            confidence REAL DEFAULT 1.0,
            createdAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sessionId TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            bambooContext TEXT,
            createdAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userId TEXT NOT NULL,
            actionType TEXT NOT NULL,
            payload TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            createdAt TEXT NOT NULL,
            resolvedAt TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            createdAt TEXT NOT NULL,
            expiresAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS meeting_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            fathomMeetingId TEXT,
            transcriptText TEXT,
            summaryText TEXT,
            rawActionItems TEXT,
            status TEXT DEFAULT 'awaiting_format_choice',
            outputFormat TEXT,
            docLink TEXT,
            slidesLink TEXT,
            createdAt TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meetingNoteId INTEGER,
            description TEXT NOT NULL,
            owner TEXT,
            dueHint TEXT,
            status TEXT DEFAULT 'open',
            createdAt TEXT NOT NULL,
            remindedAt TEXT,
            FOREIGN KEY(meetingNoteId) REFERENCES meeting_notes(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            type TEXT NOT NULL,
            error TEXT,
            cause TEXT,
            solution TEXT,
            recoverable INTEGER DEFAULT 0,
            recovered INTEGER DEFAULT 0,
            resolved INTEGER DEFAULT 0,
            recoveryAttempts INTEGER DEFAULT 0,
            createdAt TEXT NOT NULL,
            resolvedAt TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Panda Bear DB inicializada — todas las tablas listas")


def migrate_db():
    """Adds new columns to existing tables without breaking old data."""
    conn = get_db()
    c = conn.cursor()
    migrations = [
        "ALTER TABLE batches ADD COLUMN employeeName TEXT",
        "ALTER TABLE messages ADD COLUMN batchId INTEGER",
        "ALTER TABLE messages ADD COLUMN retries INTEGER DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN deliveryError TEXT",
        "ALTER TABLE messages ADD COLUMN deliveredAt TEXT",
        "ALTER TABLE messages ADD COLUMN managerMessageDate TEXT",
        "ALTER TABLE messages ADD COLUMN emailMessageDate TEXT",
        "ALTER TABLE messages ADD COLUMN koronetMessageDate TEXT",
        "ALTER TABLE messages ADD COLUMN remindedAt TEXT",
        "ALTER TABLE messages ADD COLUMN draftNotifiedAt TEXT",
        "ALTER TABLE batches ADD COLUMN photoStatus TEXT",
        "ALTER TABLE batches ADD COLUMN bambooEmployeeId TEXT",
        # Per-message approval (each of the 3 onboarding messages is approved
        # on its own) + proof of what was actually created for it.
        "ALTER TABLE messages ADD COLUMN approvedAt TEXT",
        "ALTER TABLE messages ADD COLUMN draftId TEXT",
        "ALTER TABLE messages ADD COLUMN ccEmail TEXT",
        "ALTER TABLE messages ADD COLUMN slackTarget TEXT",
        "ALTER TABLE messages ADD COLUMN slackDraftAttempts INTEGER DEFAULT 0",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
            conn.commit()
        except Exception:
            pass

    # One row per (hire, Colombia calendar day) a pending-approval reminder
    # went out. The UNIQUE constraint is what guarantees "never more than one
    # reminder per hire per day", even if two checks race each other.
    c.execute("""
        CREATE TABLE IF NOT EXISTS hire_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batchId INTEGER NOT NULL,
            reminderDate TEXT NOT NULL,
            kind TEXT NOT NULL,
            pendingTypes TEXT,
            sentAt TEXT NOT NULL,
            UNIQUE(batchId, reminderDate)
        )
    """)
    # Onboarding sessions per hire (HR, IT, Payroll, Product, First month), proposed
    # by Panda Bear and created on Junior's calendar only when he clicks "Agendar"
    # (an approval step). origin: created (by us) | existing (found on the
    # calendar) | instance (hire added to one occurrence of a recurring event).
    # A session belongs to a group: hires starting the same day share HR / IT / Payroll /
    # Product (groupKey = start date); First month is per hire (groupKey = "fm:<batchId>").
    # hires = JSON list of the hires in the session; invitedEmails = those already on the event.
    cols = [r[1] for r in c.execute("PRAGMA table_info(onboarding_meetings)").fetchall()]
    rebuild = bool(cols) and "groupKey" not in cols
    if rebuild:
        c.execute("ALTER TABLE onboarding_meetings RENAME TO onboarding_meetings_old")
        conn.commit()
    c.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batchId INTEGER NOT NULL,
            sessionType TEXT NOT NULL DEFAULT 'hr',
            groupKey TEXT,
            hires TEXT,
            invitedEmails TEXT,
            employeeName TEXT,
            employeeLocation TEXT,
            attendeeEmail TEXT,
            emailSource TEXT,
            extraAttendees TEXT,
            title TEXT,
            startDate TEXT,
            startTime TEXT,
            durationMinutes INTEGER DEFAULT 60,
            status TEXT DEFAULT 'pending_approval',
            eventId TEXT,
            eventLink TEXT,
            meetLink TEXT,
            error TEXT,
            createdAt TEXT NOT NULL,
            updatedAt TEXT,
            scheduledAt TEXT,
            origin TEXT DEFAULT 'created',
            organizerEmail TEXT,
            UNIQUE(groupKey, sessionType)
        )
    """)
    if rebuild:
        new_cols = {r[1] for r in c.execute("PRAGMA table_info(onboarding_meetings)").fetchall()}
        keep = [x for x in cols if x in new_cols and x != "sessionType"]
        st = "o.sessionType" if "sessionType" in cols else "'hr'"
        c.execute(f"""
            INSERT INTO onboarding_meetings ({", ".join(keep)}, sessionType, groupKey)
            SELECT {", ".join("o." + x for x in keep)}, {st},
                   CASE WHEN {st} = 'first_month' THEN 'fm:' || o.batchId
                        ELSE COALESCE((SELECT MIN(employeeHireDate) FROM messages WHERE batchId = o.batchId), o.startDate) END
            FROM onboarding_meetings_old o""")
        c.execute("DROP TABLE onboarding_meetings_old")
    conn.commit()
    conn.close()
