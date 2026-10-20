"""
SQLite access layer.

Two ways to get a database connection:

  get_db()          - inside a Flask request. Reuses one connection per request
                      and closes it automatically when the request ends.
  connect()         - anywhere else (CLI scripts, background threads). You are
                      responsible for closing it; a `with` block does that.

Why the split: SQLite connections cannot be shared between threads, and the
app runs SocketIO in threading mode, so handing every context its own
connection is the safe approach.
"""

import sqlite3
from contextlib import contextmanager

from flask import g

from config import DATABASE_PATH, SCHEMA_PATH, DATA_DIR


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply the settings every connection in this app should have."""

    # Rows come back as sqlite3.Row instead of plain tuples, so we can write
    # row["title"] instead of row[2]. Much easier to read and it does not
    # break when a column is added in the middle of the table.
    conn.row_factory = sqlite3.Row

    # SQLite ignores REFERENCES clauses unless this is switched on, and it
    # resets on every new connection - so it has to be set here, not just in
    # schema.sql.
    conn.execute("PRAGMA foreign_keys = ON")

    # WAL (write-ahead logging) lets one writer and several readers work at
    # the same time instead of blocking each other. With phones polling the
    # queue while the host writes to it, this avoids "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def connect() -> sqlite3.Connection:
    """Open a brand-new connection. Use this outside of a Flask request."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    return _configure(conn)


@contextmanager
def session():
    """
    Connection as a context manager, committing on success and rolling back
    if anything raises:

        with session() as conn:
            conn.execute(...)
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """
    The connection for the current Flask request.

    `g` is Flask's per-request scratch space, so the first call in a request
    opens the connection and every later call in that same request reuses it.
    """
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exception=None) -> None:
    """Registered as a teardown handler in app.py - do not call directly."""
    conn = g.pop("db", None)
    if conn is not None:
        # A request that raised should not silently save half its work.
        if exception is None:
            conn.commit()
        else:
            conn.rollback()
        conn.close()


# Columns added to existing tables after the first release, as
# (table, column, definition). schema.sql already has them for NEW databases,
# but "CREATE TABLE IF NOT EXISTS" does nothing to a table that exists, so an
# older karaoke.db needs them added one by one. Listing them here means
# upgrading never requires `init_db.py --reset` (which would erase scores).
_ADDED_COLUMNS = [
    ("songs", "sync_status", "TEXT NOT NULL DEFAULT 'none'"),
    ("songs", "sync_message", "TEXT"),
    ("songs", "sync_locked", "INTEGER NOT NULL DEFAULT 0"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an older database up to date with schema.sql."""
    for table, column, definition in _ADDED_COLUMNS:
        # PRAGMA table_info lists a table's columns; row["name"] is each name.
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Create any missing tables by running schema.sql, then add new columns."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with session() as conn:
        # executescript runs multiple statements separated by semicolons,
        # which execute() cannot do.
        conn.executescript(schema_sql)
        _add_missing_columns(conn)


def init_app(app) -> None:
    """Attach the teardown handler so connections never leak."""
    app.teardown_appcontext(close_db)
