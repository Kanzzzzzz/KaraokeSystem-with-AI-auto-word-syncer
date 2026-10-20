"""
Database operations for the song library.

Every function here takes an open connection as its first argument rather than
opening its own. That keeps the model layer independent of Flask (the import
script reuses these exact functions) and lets several calls share one
transaction.
"""

import sqlite3

from config import FIRST_SONG_CODE, CODE_WIDTH


# Columns returned to the frontend. Listed explicitly instead of SELECT *, so
# adding an internal column later does not accidentally expose it in the API.
_COLUMNS = """
    id, code, title, artist, audio_path, lyrics_path, source_text_path,
    duration_sec, language, is_synced, date_added,
    sync_status, sync_message, sync_locked
"""

# Every value sync_status may hold - see schema.sql.
SYNC_STATUSES = {"none", "queued", "processing", "done", "needs_review", "failed"}


class SongLockedError(Exception):
    """Auto sync was asked to touch a song whose sync_locked flag is set."""


def _to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a database row into a plain dict that Flask can send as JSON."""
    if row is None:
        return None

    song = dict(row)
    # SQLite has no boolean type, so is_synced is stored as 0/1. Convert it
    # here so the JavaScript side can just write `if (song.is_synced)`.
    song["is_synced"] = bool(song["is_synced"])
    song["sync_locked"] = bool(song["sync_locked"])
    return song


def next_code(conn: sqlite3.Connection) -> str:
    """
    Work out the next free song code, e.g. "1003".

    Codes are stored as text, so CAST is needed to find the numeric maximum -
    without it "999" would sort above "1000" alphabetically.
    """
    row = conn.execute("SELECT MAX(CAST(code AS INTEGER)) AS max_code FROM songs").fetchone()
    highest = row["max_code"]

    next_number = FIRST_SONG_CODE if highest is None else int(highest) + 1
    return str(next_number).zfill(CODE_WIDTH)


def get_by_id(conn: sqlite3.Connection, song_id: int) -> dict | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM songs WHERE id = ?", (song_id,)).fetchone()
    return _to_dict(row)


def get_by_code(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM songs WHERE code = ?", (code,)).fetchone()
    return _to_dict(row)


def get_by_audio_path(conn: sqlite3.Connection, audio_path: str) -> dict | None:
    """Used by the importer to tell an already-imported file from a new one."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM songs WHERE audio_path = ?", (audio_path,)
    ).fetchone()
    return _to_dict(row)


def list_all(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM songs ORDER BY CAST(code AS INTEGER) LIMIT ?", (limit,)
    ).fetchall()
    return [_to_dict(r) for r in rows]


def search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """
    The one search users actually touch: it accepts either a number code or
    free text, and works out which is which.

    Typing digits searches codes by PREFIX, so "10" already lists 1001, 1002,
    1003 while you are still typing - the same feel as a real karaoke remote.
    An exact code match is forced to the top of the list.

    Typing anything else searches title and artist with a contains-match.
    """
    query = (query or "").strip()
    if not query:
        return list_all(conn, limit)

    if query.isdigit():
        # The ESCAPE clause below lets us treat % and _ literally. Codes are
        # digits only so it is not strictly needed here, but it keeps this
        # query safe if code formats ever change.
        rows = conn.execute(
            f"""
            SELECT {_COLUMNS} FROM songs
            WHERE code LIKE ? || '%'
            ORDER BY
                CASE WHEN code = ? THEN 0 ELSE 1 END,   -- exact match first
                CAST(code AS INTEGER)
            LIMIT ?
            """,
            (query, query, limit),
        ).fetchall()
        return [_to_dict(r) for r in rows]

    # Text search. The % wildcards are put into the PARAMETER, never into the
    # SQL string itself - that is what keeps this safe from SQL injection.
    pattern = f"%{query}%"
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS} FROM songs
        WHERE title LIKE ? OR artist LIKE ?
        ORDER BY
            CASE
                WHEN title  LIKE ? THEN 0   -- title starts with the query
                WHEN artist LIKE ? THEN 1   -- artist starts with the query
                ELSE 2                      -- matched somewhere in the middle
            END,
            title
        LIMIT ?
        """,
        (pattern, pattern, f"{query}%", f"{query}%", limit),
    ).fetchall()
    return [_to_dict(r) for r in rows]


def add(
    conn: sqlite3.Connection,
    *,
    title: str,
    artist: str,
    audio_path: str,
    code: str | None = None,
    lyrics_path: str | None = None,
    source_text_path: str | None = None,
    duration_sec: float | None = None,
    language: str | None = None,
    sync_locked: bool = False,
) -> dict:
    """
    Insert a song and return the stored row.

    `is_synced` is derived rather than passed in: a song counts as synced only
    if it actually has an .lrc path, so the flag can never disagree with the
    file situation.
    """
    if code is None:
        code = next_code(conn)

    cursor = conn.execute(
        """
        INSERT INTO songs
            (code, title, artist, audio_path, lyrics_path, source_text_path,
             duration_sec, language, is_synced, sync_locked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code, title, artist, audio_path, lyrics_path, source_text_path,
            duration_sec, language, 1 if lyrics_path else 0, 1 if sync_locked else 0,
        ),
    )
    return get_by_id(conn, cursor.lastrowid)


class SongEditError(Exception):
    """An edit was refused: bad value, or a code another song already has."""


def update(
    conn: sqlite3.Connection,
    song_id: int,
    *,
    title: str | None = None,
    artist: str | None = None,
    language: str | None = None,
    code: str | None = None,
) -> dict | None:
    """
    Edit a song's details; fields left as None are not touched. Returns the
    updated song, or None if there is no such song.

    A code must be digits only and unused by any other song. Changing it is
    safe for the queue and scores: they point at the song's id, not its code.
    """
    song = get_by_id(conn, song_id)
    if song is None:
        return None

    changes = {}
    if title is not None:
        title = title.strip()
        if not title:
            raise SongEditError("The title cannot be empty.")
        changes["title"] = title
    if artist is not None:
        artist = artist.strip()
        if not artist:
            raise SongEditError("The artist cannot be empty.")
        changes["artist"] = artist
    if language is not None:
        changes["language"] = language.strip() or None
    if code is not None:
        code = code.strip()
        if not code.isdigit():
            raise SongEditError("A song code is numbers only, like 1001.")
        code = code.zfill(CODE_WIDTH)
        other = get_by_code(conn, code)
        if other is not None and other["id"] != song_id:
            raise SongEditError(f"Code {code} is already used by {other['artist']} - {other['title']}.")
        changes["code"] = code

    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        conn.execute(f"UPDATE songs SET {assignments} WHERE id = ?",
                     (*changes.values(), song_id))
    return get_by_id(conn, song_id)


def delete(conn: sqlite3.Connection, song_id: int) -> bool:
    """
    Remove a song from the library. Returns False if there was no such song.

    Its queue entries and scores go with it: both reference the song and the
    database would refuse the delete otherwise. Files on disk are not touched
    here; the route decides about those.
    """
    if get_by_id(conn, song_id) is None:
        return False
    conn.execute("DELETE FROM scores WHERE song_id = ?", (song_id,))
    conn.execute("DELETE FROM queue_entries WHERE song_id = ?", (song_id,))
    conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    return True


def set_lyrics_path(conn: sqlite3.Connection, song_id: int, lyrics_path: str) -> dict | None:
    """Called after the sync editor saves an .lrc file."""
    conn.execute(
        "UPDATE songs SET lyrics_path = ?, is_synced = 1 WHERE id = ?",
        (lyrics_path, song_id),
    )
    return get_by_id(conn, song_id)


def set_source_text_path(conn: sqlite3.Connection, song_id: int, text_path: str) -> dict | None:
    """
    Record where a song's plain (untimed) lyrics live.

    Used when lyrics arrive after the song was imported - pasted by hand, or
    fetched from LRCLIB without timing. It does not touch is_synced: plain text
    is what the sync editor times, not timing itself.
    """
    conn.execute(
        "UPDATE songs SET source_text_path = ? WHERE id = ?",
        (text_path, song_id),
    )
    return get_by_id(conn, song_id)


def set_sync_locked(conn: sqlite3.Connection, song_id: int, locked: bool) -> dict | None:
    """
    Lock or unlock a song against auto sync.

    Locking also cancels a queued run. A run already in progress finishes,
    but its result is thrown away: the lock is checked again right before
    anything is written. Either way the song goes back to 'none'.
    """
    conn.execute(
        """
        UPDATE songs
        SET sync_locked = ?,
            sync_status = CASE WHEN ? = 1 AND sync_status IN ('queued', 'processing')
                               THEN 'none' ELSE sync_status END
        WHERE id = ?
        """,
        (1 if locked else 0, 1 if locked else 0, song_id),
    )
    return get_by_id(conn, song_id)


def is_sync_locked(conn: sqlite3.Connection, song_id: int) -> bool:
    """
    Read the lock straight from the database.

    Callers use this instead of a `song` dict they fetched earlier: a dict can
    be minutes old by the time a long job finishes, and the lock may have been
    switched on in the meantime. A missing song counts as locked - there is
    nothing safe to do to it.
    """
    row = conn.execute("SELECT sync_locked FROM songs WHERE id = ?", (song_id,)).fetchone()
    return row is None or bool(row["sync_locked"])


def set_sync_status(
    conn: sqlite3.Connection, song_id: int, status: str, message: str | None = None
) -> dict | None:
    """
    Record where a song's auto sync stands.

    Refuses (SongLockedError) for a locked song. The check is in the SQL
    itself - "AND sync_locked = 0" - so it cannot be skipped by a caller that
    forgot to check first: a locked song's row simply does not match.
    """
    if status not in SYNC_STATUSES:
        raise ValueError(f"Unknown sync status: {status!r}")

    cursor = conn.execute(
        "UPDATE songs SET sync_status = ?, sync_message = ? WHERE id = ? AND sync_locked = 0",
        (status, message, song_id),
    )
    if cursor.rowcount == 0:
        raise SongLockedError(f"Song {song_id} is locked against auto sync (or does not exist).")
    return get_by_id(conn, song_id)


def list_by_sync_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Songs in one auto-sync state, oldest first. Locked songs are never listed."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM songs WHERE sync_status = ? AND sync_locked = 0 ORDER BY id",
        (status,),
    ).fetchall()
    return [_to_dict(r) for r in rows]


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM songs").fetchone()["n"]
