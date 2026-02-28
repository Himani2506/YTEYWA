#!/usr/bin/env python3
"""
manage_session.py — Per-user session storage in SQLite.

Usage:
  python3 manage_session.py get   "<user_id>" <key>
  python3 manage_session.py set   "<user_id>" <key> "<value>"
  python3 manage_session.py clear "<user_id>"
  python3 manage_session.py add_history "<user_id>" <role> "<message>"
  python3 manage_session.py get_history  "<user_id>"

Valid keys: video_id, video_title, language, duration
"""

import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import SESSIONS_DB, out, error


# ── DB initialisation ──────────────────────────────────────────────────────
def _conn():
    c = sqlite3.connect(SESSIONS_DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id     TEXT PRIMARY KEY,
            video_id    TEXT,
            video_title TEXT,
            language    TEXT DEFAULT 'en',
            duration    INTEGER DEFAULT 0,
            created_at  TEXT,
            updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    c.commit()
    return c


# ── Operations ─────────────────────────────────────────────────────────────
def session_get(user_id, key):
    c = _conn()
    row = c.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    if row is None:
        # Return default values rather than null for language
        defaults = {"video_id": None, "video_title": None, "language": "en", "duration": 0}
        out({"value": defaults.get(key)})
    out({"value": dict(row).get(key)})


def session_set(user_id, key, value):
    allowed = {"video_id", "video_title", "language", "duration"}
    if key not in allowed:
        error("BAD_KEY", f"Key must be one of: {allowed}")
    now = datetime.utcnow().isoformat()
    c = _conn()
    exists = c.execute("SELECT 1 FROM sessions WHERE user_id=?", (user_id,)).fetchone()
    if exists:
        c.execute(f"UPDATE sessions SET {key}=?, updated_at=? WHERE user_id=?", (value, now, user_id))
    else:
        defaults = {"video_id": None, "video_title": None, "language": "en", "duration": 0}
        defaults[key] = value
        c.execute(
            "INSERT INTO sessions (user_id,video_id,video_title,language,duration,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, defaults["video_id"], defaults["video_title"], defaults["language"], defaults["duration"], now, now)
        )
    c.commit()
    c.close()
    out({"saved": True, "key": key, "value": value})


def session_clear(user_id):
    c = _conn()
    c.execute("DELETE FROM sessions     WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM chat_history WHERE user_id=?", (user_id,))
    c.commit()
    c.close()
    out({"cleared": True})


def add_history(user_id, role, message):
    now = datetime.utcnow().isoformat()
    c = _conn()
    c.execute(
        "INSERT INTO chat_history (user_id,role,message,created_at) VALUES (?,?,?,?)",
        (user_id, role, message, now)
    )
    # Keep last 20 per user
    c.execute("""
        DELETE FROM chat_history WHERE user_id=? AND id NOT IN (
          SELECT id FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 20
        )
    """, (user_id, user_id))
    c.commit()
    c.close()
    out({"saved": True})


def get_history(user_id):
    c = _conn()
    rows = c.execute(
        "SELECT role, message FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 6",
        (user_id,)
    ).fetchall()
    c.close()
    history = [{"role": r["role"], "message": r["message"]} for r in reversed(rows)]
    out({"history": history})


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        error("USAGE", "python3 manage_session.py <action> <user_id> [key] [value]")

    action, user_id = args[0], args[1]

    if action == "get":
        key = args[2] if len(args) > 2 else None
        if not key:
            error("MISSING_ARG", "Key required for 'get'")
        session_get(user_id, key)

    elif action == "set":
        key   = args[2] if len(args) > 2 else None
        value = args[3] if len(args) > 3 else None
        if not key or value is None:
            error("MISSING_ARG", "Key and value required for 'set'")
        session_set(user_id, key, value)

    elif action == "clear":
        session_clear(user_id)

    elif action == "add_history":
        role    = args[2] if len(args) > 2 else None
        message = args[3] if len(args) > 3 else None
        if not role or message is None:
            error("MISSING_ARG", "role and message required for 'add_history'")
        add_history(user_id, role, message)

    elif action == "get_history":
        get_history(user_id)

    else:
        error("UNKNOWN_ACTION", f"Unknown action: {action}")
