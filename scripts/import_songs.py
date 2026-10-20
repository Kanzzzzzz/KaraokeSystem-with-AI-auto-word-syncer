"""
Add songs from the media folders into the database.

    python scripts/import_songs.py              import anything new
    python scripts/import_songs.py --list       just show what is already imported
    python scripts/import_songs.py --no-lrclib  import without going online
    python scripts/import_songs.py --no-auto-sync  do not queue new songs for auto sync
    python scripts/import_songs.py --no-minus   do not make minus-one tracks from originals

Drop new files into data/library/audio/ (plus matching lyrics in
data/library/lyrics/ if you have them) and run this again.

Or drop in just the ORIGINAL recording, with vocals, in
data/library/originals/: the first step below makes a minus-one track from
it (vocals removed with Demucs, about 4 minutes per song), which is then
imported like any other song. Songs already
imported are skipped, so it is safe to run as often as you like.

After importing, every song that still has no timing is looked up on LRCLIB
(https://lrclib.net). A song LRCLIB cannot time is flagged "needs manual sync"
and will be tried again the next time this runs - by then someone may have
added it.

Finally, each NEW song is queued for automatic word sync (unless it is locked
in song_overrides.json). The karaoke server works through the queue in the
background; without the server running, `python scripts/auto_sync.py --queued`
does the same from the command line.
"""

import argparse
import sys

import _bootstrap  # noqa: F401  - must run first; puts backend/ on sys.path

from database import init_db, session
from models import song as song_model
from utils import lrclib
from utils.autosync import (AutoSyncError, make_minus_one, originals_needing_minus,
                            queue_new_songs)
from utils.importer import import_all


def show_library(conn) -> None:
    songs = song_model.list_all(conn)

    if not songs:
        print("No songs imported yet.")
        return

    print(f"\n{'CODE':<6} {'ARTIST':<26} {'TITLE':<32} {'TIME':>6}  LYRICS")
    print("-" * 82)

    for s in songs:
        if s["duration_sec"]:
            minutes, seconds = divmod(int(s["duration_sec"]), 60)
            length = f"{minutes}:{seconds:02d}"
        else:
            length = "-"

        if s["is_synced"]:
            lyric_state = "synced"
        elif s["source_text_path"]:
            lyric_state = "needs manual sync"
        else:
            lyric_state = "needs manual sync (no lyrics yet)"

        if s["sync_locked"]:
            lyric_state += ", auto sync locked"
        elif s["sync_status"] != "none":
            lyric_state += f", auto sync: {s['sync_status']}"

        print(
            f"{s['code']:<6} {s['artist'][:26]:<26} {s['title'][:32]:<32} "
            f"{length:>6}  {lyric_state}"
        )


def make_missing_minus_tracks(conn) -> None:
    """
    Make a minus-one track for every original recording that has none yet.

    Runs before the import, so the new tracks are imported in the same run.
    Slow - Demucs takes a few minutes per song on a laptop - but only once
    per original; the separation is cached and reused by auto sync later.
    """
    pending = originals_needing_minus(conn)
    if not pending:
        return

    print(f"Making {len(pending)} minus-one track(s) from original recordings...")
    for original, output in pending:
        print(f"  {original.name}")

        def show(message):
            # A carriage return (backslash-r) goes back to the start of the line,
            # so the percentage updates in place.
            sys.stdout.write(f"\r    {message['percent']:5.1f}%  {message['message'][:60]:<60}")
            sys.stdout.flush()

        try:
            result = make_minus_one(original, output, show)
        except AutoSyncError as error:
            print(f"\n    could not make it: {error}")
            continue
        took = "from cache" if result["stems_cached"] else f"{result['seconds']:.0f}s"
        print(f"\n    -> {output.name} ({took})")
    print()


def fetch_missing_timing(conn) -> None:
    """
    Ask LRCLIB for timing for every song that still has none.

    This covers the songs just imported AND older ones still waiting for
    timing, so a song LRCLIB lacked last time gets another chance.
    """
    pending = [s for s in song_model.list_all(conn) if not s["is_synced"]]
    if not pending:
        return

    print(f"\nLooking up {len(pending)} song(s) without timing on LRCLIB...")

    for song in pending:
        label = f"  [{song['code']}] {song['artist']} - {song['title']}"

        try:
            result = lrclib.fetch_for_song(conn, song)
        except lrclib.LrclibError as error:
            # When one request cannot get through, the rest will not either.
            # Stop rather than waiting out a timeout for every song.
            print(f"{label}: {error}")
            print("  Skipped the rest. Run this again once you are online.")
            return

        # Commit after each song, so an interrupted run keeps what it found.
        conn.commit()

        status = result["status"]
        if status == "synced":
            m = result["matched"]
            print(
                f"{label}: timed from LRCLIB (id {m['id']}, {m['matched_by']})\n"
                f"      LRCLIB {m['duration']}s vs your file {song['duration_sec']}s"
                f" - {m['duration_gap']}s apart, {result['line_count']} lines"
            )
        elif status == "plain_only":
            print(
                f"{label}: LRCLIB has the words but no timing that fits this"
                " recording.\n      Words saved - needs manual sync."
            )
        else:
            print(f"{label}: not on LRCLIB - needs manual sync.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import songs into the karaoke library.")
    parser.add_argument("--list", action="store_true", help="only list the current library")
    parser.add_argument("--no-lrclib", action="store_true",
                        help="do not look anything up online")
    parser.add_argument("--no-auto-sync", action="store_true",
                        help="do not queue new songs for automatic word sync")
    parser.add_argument("--no-minus", action="store_true",
                        help="do not make minus-one tracks from original recordings")
    args = parser.parse_args()

    # Creating the tables here means a first-time user can run this script
    # without having remembered to run init_db.py first.
    init_db()

    with session() as conn:
        if args.list:
            show_library(conn)
            return

        if not args.no_minus:
            make_missing_minus_tracks(conn)

        print("Scanning media folders...")
        result = import_all(conn)

        print(f"\nImported {len(result['added'])} new song(s).")
        if result["relinked_lyrics"]:
            print(f"Linked timing for {len(result['relinked_lyrics'])} existing song(s):")
            for s in result["relinked_lyrics"]:
                print(f"  [{s['code']}] {s['artist']} - {s['title']}")
        if result["skipped"]:
            print(f"Skipped {len(result['skipped'])} already in the library.")

        if not args.no_lrclib:
            fetch_missing_timing(conn)

        # After LRCLIB on purpose: its line times become the anchors that
        # keep every auto-synced word inside its own line.
        if result["added"] and not args.no_auto_sync:
            queued = queue_new_songs(conn, result["added"])
            conn.commit()
            if queued:
                print(f"\nQueued {len(queued)} new song(s) for automatic word sync.")
                print("  They are processed in the background while the server runs,")
                print("  or run now with:  python scripts/auto_sync.py --queued")

        still_pending = [s for s in song_model.list_all(conn) if not s["is_synced"]]
        if still_pending:
            print("\nThese songs need manual sync:")
            for s in still_pending:
                print(f"  [{s['code']}] {s['artist']} - {s['title']}")
            print(
                "\nOpen http://localhost:5000/sync to time them - or to paste their\n"
                "lyrics first, if they have none. Until then they play, but the\n"
                "lyrics do not scroll."
            )

        show_library(conn)


if __name__ == "__main__":
    main()
