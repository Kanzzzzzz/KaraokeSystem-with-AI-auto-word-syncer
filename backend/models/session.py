"""
Karaoke sessions - one sitting of singing.

Everything in the queue and on the leaderboard belongs to a session, which is
what makes the leaderboard "tonight's scores" rather than an all-time list.
The app calls get_or_create_active() on startup, so a session begins the first
time you launch it and continues until you explicitly end it.
"""

import sqlite3


def get_active(conn: sqlite3.Connection) -> dict | None:
    """The most recent session that has not been ended, if there is one."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def create(conn: sqlite3.Connection, label: str | None = None) -> dict:
    cursor = conn.execute("INSERT INTO sessions (label) VALUES (?)", (label,))
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def get_or_create_active(conn: sqlite3.Connection) -> dict:
    """Reuse the open session if there is one, otherwise start a new one."""
    return get_active(conn) or create(conn)


def end(conn: sqlite3.Connection, session_id: int) -> None:
    """
    Close a session. Its queue and scores stay in the database as history;
    the next request simply starts a fresh session.
    """
    conn.execute(
        "UPDATE sessions SET ended_at = datetime('now') WHERE id = ?", (session_id,)
    )
