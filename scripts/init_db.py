"""
Create the karaoke database.

    python scripts/init_db.py           create any missing tables
    python scripts/init_db.py --reset   delete the database and start over

Running it without --reset is always safe: schema.sql only creates tables that
do not already exist, so your songs and scores are left alone.
"""

import argparse

import _bootstrap  # noqa: F401  - must run first; puts backend/ on sys.path

from config import DATABASE_PATH
from database import init_db, session
from models import song as song_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the karaoke database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the existing database file first (this erases all songs and scores)",
    )
    args = parser.parse_args()

    if args.reset and DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
        # WAL mode leaves these alongside the database; remove them too so the
        # next run starts from a genuinely clean state.
        for extra in (".db-wal", ".db-shm"):
            leftover = DATABASE_PATH.with_suffix(extra)
            if leftover.exists():
                leftover.unlink()
        print(f"Deleted {DATABASE_PATH}")

    init_db()
    print(f"Database ready at {DATABASE_PATH}")

    with session() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        print(f"Tables: {', '.join(tables)}")
        print(f"Songs in library: {song_model.count(conn)}")

    print("\nNext step:  python scripts/import_songs.py")


if __name__ == "__main__":
    main()
