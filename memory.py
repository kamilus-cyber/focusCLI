"""
memory.py - Persistent memory for Focus CLI
Stores tasks, session history, and learned patterns
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".focus_cli" / "memory.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            status TEXT DEFAULT 'active',   -- active | done | dropped
            priority INTEGER DEFAULT 2,     -- 1=high 2=normal 3=low
            created_at TEXT,
            updated_at TEXT,
            session_id TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            started_at TEXT,
            ended_at TEXT,
            summary TEXT,
            focus_score INTEGER           -- 1-5, how focused was the session
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,                    -- user | assistant
            content TEXT,
            timestamp TEXT
        );

        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            category TEXT,               -- preference | pattern | reminder
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def get_conn():
    return sqlite3.connect(DB_PATH)


# ── Tasks ──────────────────────────────────────────────────────────────────

def add_task(text, priority=2, session_id=None):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (text, priority, created_at, updated_at, session_id) VALUES (?,?,?,?,?)",
            (text, priority, now, now, session_id)
        )


def get_active_tasks():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, priority FROM tasks WHERE status='active' ORDER BY priority, created_at"
        ).fetchall()
    return [{"id": r[0], "text": r[1], "priority": r[2]} for r in rows]


def complete_task(task_id):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', updated_at=? WHERE id=?",
            (now, task_id)
        )


def drop_task(task_id):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status='dropped', updated_at=? WHERE id=?",
            (now, task_id)
        )


# ── Sessions ───────────────────────────────────────────────────────────────

def start_session(session_id):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?,?)",
            (session_id, now)
        )


def end_session(session_id, summary="", focus_score=3):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at=?, summary=?, focus_score=? WHERE id=?",
            (now, summary, focus_score, session_id)
        )


def get_last_session_summary():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary, ended_at FROM sessions WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1"
        ).fetchone()
    if row:
        return {"summary": row[0], "ended_at": row[1]}
    return None


# ── Messages ───────────────────────────────────────────────────────────────

def save_message(session_id, role, content):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (session_id, role, content, now)
        )


def get_session_messages(session_id, limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


# ── Facts ──────────────────────────────────────────────────────────────────

def save_fact(content, category="preference"):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO facts (content, category, created_at) VALUES (?,?,?)",
            (content, category, now)
        )


def get_facts(category=None):
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT content FROM facts WHERE category=? ORDER BY created_at DESC LIMIT 10",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content FROM facts ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
    return [r[0] for r in rows]
