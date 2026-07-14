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
    ]
    for sql in migrations:
        try:
            c.execute(sql)
            conn.commit()
        except Exception:
            pass
    conn.close()
