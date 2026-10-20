"""
The shared singing queue.

Queue rows join to songs, so every read here returns the singer's details AND
the song details together - the frontend needs both to draw a queue row and
should not have to make a second request per entry.

Status values:
    waiting  - in line
    playing  - currently on screen (at most one per session)
    done     - finished normally
    skipped  - removed by the host mid-song
"""

import sqlite3

# Finished entries stay in the table for the session history, so "the queue"
# always means the entries still waiting to be sung.
ACTIVE_STATUSES = ("waiting", "playing")

# One SELECT reused by every read below. The song columns are aliased with a
# song_ prefix so they cannot collide with the queue's own id/status columns.
_SELECT = """
    SELECT
        q.id, q.session_id, q.song_id, q.singer_name, q.position,
        q.status, q.created_at,
        s.code        AS song_code,
        s.title       AS song_title,
        s.artist      AS song_artist,
        s.duration_sec AS song_duration,
        s.is_synced   AS song_is_synced
    FROM queue_entries q
    JOIN songs s ON s.id = q.song_id
"""


def _to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    entry = dict(row)
    entry["song_is_synced"] = bool(entry["song_is_synced"])
    return entry


def list_queue(conn: sqlite3.Connection, session_id: int, include_done: bool = False) -> list[dict]:
    """The queue in singing order."""
    if include_done:
        rows = conn.execute(
            f"{_SELECT} WHERE q.session_id = ? ORDER BY q.position", (session_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SELECT} WHERE q.session_id = ? AND q.status IN (?, ?) ORDER BY q.position",
            (session_id, *ACTIVE_STATUSES),
        ).fetchall()

    return [_to_dict(r) for r in rows]


def get(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    return _to_dict(conn.execute(f"{_SELECT} WHERE q.id = ?", (entry_id,)).fetchone())


def get_now_playing(conn: sqlite3.Connection, session_id: int) -> dict | None:
    return _to_dict(
        conn.execute(
            f"{_SELECT} WHERE q.session_id = ? AND q.status = 'playing' LIMIT 1",
            (session_id,),
        ).fetchone()
    )


def get_next_waiting(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """The entry that should play next - lowest position still waiting."""
    return _to_dict(
        conn.execute(
            f"{_SELECT} WHERE q.session_id = ? AND q.status = 'waiting' "
            f"ORDER BY q.position LIMIT 1",
            (session_id,),
        ).fetchone()
    )


def add(conn: sqlite3.Connection, session_id: int, song_id: int, singer_name: str) -> dict:
    """Append to the end of the queue."""
    # COALESCE turns the NULL you get from MAX() on an empty queue into 0, so
    # the first entry lands at position 1 instead of failing on NULL + 1.
    row = conn.execute(
        "SELECT COALESCE(MAX(position), 0) AS last FROM queue_entries WHERE session_id = ?",
        (session_id,),
    ).fetchone()

    cursor = conn.execute(
        """
        INSERT INTO queue_entries (session_id, song_id, singer_name, position)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, song_id, singer_name.strip(), row["last"] + 1),
    )
    return get(conn, cursor.lastrowid)


def set_status(conn: sqlite3.Connection, entry_id: int, status: str) -> dict | None:
    conn.execute("UPDATE queue_entries SET status = ? WHERE id = ?", (status, entry_id))
    return get(conn, entry_id)


def remove(conn: sqlite3.Connection, entry_id: int) -> bool:
    """
    Delete a waiting entry outright.

    Entries that already played are kept as history instead - removing one
    would break the score row that references it.
    """
    entry = get(conn, entry_id)
    if entry is None:
        return False

    if entry["status"] in ("done", "skipped"):
        return False

    conn.execute("DELETE FROM queue_entries WHERE id = ?", (entry_id,))
    return True


def reorder(conn: sqlite3.Connection, session_id: int, ordered_ids: list[int]) -> list[dict]:
    """
    Apply a new order given as a list of entry ids, first to last.

    Only ids belonging to this session are touched, so a bad request cannot
    reorder somebody else's queue. Positions are rewritten from 1 upward,
    which keeps them tidy no matter how many drags have happened.
    """
    valid_ids = {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM queue_entries WHERE session_id = ?", (session_id,)
        )
    }

    position = 1
    for entry_id in ordered_ids:
        if entry_id not in valid_ids:
            continue
        conn.execute(
            "UPDATE queue_entries SET position = ? WHERE id = ? AND session_id = ?",
            (position, entry_id, session_id),
        )
        position += 1

    return list_queue(conn, session_id)


def advance(conn: sqlite3.Connection, session_id: int, finished_status: str = "done") -> dict | None:
    """
    Finish whatever is playing and start the next song.

    Returns the newly playing entry, or None when the queue has run out.
    This is the single place the "what plays next" rule lives, so the auto-
    advance at song end and the host's skip button behave identically.
    """
    current = get_now_playing(conn, session_id)
    if current is not None:
        set_status(conn, current["id"], finished_status)

    nxt = get_next_waiting(conn, session_id)
    if nxt is None:
        return None

    return set_status(conn, nxt["id"], "playing")


def clear(conn: sqlite3.Connection, session_id: int) -> None:
    """Remove every entry that has not been sung yet."""
    conn.execute(
        "DELETE FROM queue_entries WHERE session_id = ? AND status IN (?, ?)",
        (session_id, *ACTIVE_STATUSES),
    )
