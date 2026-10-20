"""
Performance scores and the session leaderboard.

The scoring itself happens in the browser (see frontend/js/scoring/), because
that is where the microphone is. This module only stores the result and ranks
it.
"""

import json
import sqlite3

_SELECT = """
    SELECT
        sc.id, sc.session_id, sc.queue_entry_id, sc.song_id, sc.singer_name,
        sc.final_score, sc.timing_score, sc.energy_score, sc.pitch_score,
        sc.energy_timeline, sc.created_at,
        s.code   AS song_code,
        s.title  AS song_title,
        s.artist AS song_artist
    FROM scores sc
    JOIN songs s ON s.id = sc.song_id
"""


def _to_dict(row: sqlite3.Row | None, include_timeline: bool = True) -> dict | None:
    """
    Convert a row to a dict, decoding energy_timeline back into a real list.

    The timeline is only useful on the recap screen, so leaderboard queries
    drop it - sending a few hundred numbers per row to build a ranked list
    would be wasteful.
    """
    if row is None:
        return None

    score = dict(row)
    raw_timeline = score.pop("energy_timeline", None)

    if include_timeline:
        try:
            score["energy_timeline"] = json.loads(raw_timeline) if raw_timeline else []
        except (json.JSONDecodeError, TypeError):
            # Corrupt JSON should not break the recap screen.
            score["energy_timeline"] = []

    return score


def save(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    song_id: int,
    singer_name: str,
    final_score: int,
    queue_entry_id: int | None = None,
    timing_score: float | None = None,
    energy_score: float | None = None,
    pitch_score: float | None = None,
    energy_timeline: list | None = None,
) -> dict:
    """
    Store one finished performance.

    `pitch_score` is accepted but will be None until the pitch-matching
    upgrade lands - the parameter exists now so that upgrade needs no change
    here either.
    """
    # Clamp to the advertised 0-100 range so a frontend bug can never put an
    # impossible score on the leaderboard.
    final_score = max(0, min(100, int(round(final_score))))

    cursor = conn.execute(
        """
        INSERT INTO scores
            (session_id, queue_entry_id, song_id, singer_name, final_score,
             timing_score, energy_score, pitch_score, energy_timeline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id, queue_entry_id, song_id, singer_name.strip(), final_score,
            timing_score, energy_score, pitch_score,
            json.dumps(energy_timeline) if energy_timeline else None,
        ),
    )

    row = conn.execute(f"{_SELECT} WHERE sc.id = ?", (cursor.lastrowid,)).fetchone()
    return _to_dict(row)


def get(conn: sqlite3.Connection, score_id: int) -> dict | None:
    return _to_dict(conn.execute(f"{_SELECT} WHERE sc.id = ?", (score_id,)).fetchone())


def leaderboard(conn: sqlite3.Connection, session_id: int, limit: int = 20) -> list[dict]:
    """
    Tonight's top performances, highest first.

    Ties break on who got there first, so the ranking never reshuffles itself
    between refreshes.
    """
    rows = conn.execute(
        f"{_SELECT} WHERE sc.session_id = ? ORDER BY sc.final_score DESC, sc.created_at ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [_to_dict(r, include_timeline=False) for r in rows]


def recent(conn: sqlite3.Connection, session_id: int, limit: int = 10) -> list[dict]:
    """Most recent performances, newest first."""
    rows = conn.execute(
        f"{_SELECT} WHERE sc.session_id = ? ORDER BY sc.created_at DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [_to_dict(r, include_timeline=False) for r in rows]
