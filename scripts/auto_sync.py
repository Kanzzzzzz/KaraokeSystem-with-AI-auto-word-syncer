"""
Run automatic word sync from the command line.

    python scripts/auto_sync.py --song 1004          one song, by its number code
    python scripts/auto_sync.py --queued             every song waiting in the queue
    python scripts/auto_sync.py --song 1004 --no-isolate   skip Demucs (faster, worse)

Testing accuracy without changing anything:

    python scripts/auto_sync.py --song 1004 --dry-run --anchors lrclib --out result.json

--dry-run changes no lyric file and nothing in the database (only the stems
cache in data/library/autosync_cache/ is filled): the result goes to the --out
file, for scripts/measure_drift.py to compare against timing
you tapped by hand. --anchors lrclib takes the line times from LRCLIB rather
than from the song's .lrc, so hand-tapped line times can never leak into the
test and flatter the result.

Locked songs (see song_overrides.json) are refused, dry run or not.
This script runs with the normal Python; it starts the .venv-align worker
itself, exactly as the server does.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import _bootstrap  # noqa: F401  - must run first; puts backend/ on sys.path

from database import init_db, session
from models import song as song_model
from models.song import SongLockedError
from utils import autosync
from utils.importer import apply_sync_locks


def show_progress(message: dict) -> None:
    """One line that updates in place: '  [ 42%] separating - ...'."""
    line = f"  [{message['percent']:5.1f}%] {message['stage']:<12} {message['message']}"
    sys.stdout.write("\r" + line[:100].ljust(100))
    sys.stdout.flush()


def describe(result: dict) -> None:
    summary = result["summary"]
    print(f"\n  mode: {result['mode']}, {summary['words']} words, "
          f"{summary['high_confidence']} high confidence ({summary['high_ratio']:.0%})")
    if result.get("offset"):
        o = result["offset"]
        print(f"  minus track vs original: offset {o['seconds']:+.2f}s, match {o['match']:.2f}, "
              f"drift {o['drift']:.2f}s -> {'OK' if o['ok'] else 'POOR'}")
    if result.get("vocal_level_db") is not None:
        print(f"  vocals level: {result['vocal_level_db']} dB "
              f"({'cached stems' if result['stems_cached'] else 'separated now'})")
    print(f"  time per stage (s): {result['timings']}")
    print(f"  suggested status: {summary['status']}")
    for reason in summary["reasons"]:
        print(f"    - {reason}")


def run_one(song: dict, args) -> bool:
    print(f"\n[{song['code']}] {song['artist']} - {song['title']}")
    started = time.time()
    options = {"isolate_vocals": not args.no_isolate}

    try:
        if args.dry_run:
            with session() as conn:
                job = autosync.prepare_job(conn, song, anchors=args.anchors, **options)
            print(f"  anchors: {job['anchor_source'] or 'none'}; original: "
                  f"{Path(job['original_audio']).name if job['original_audio'] else 'none'}")
            result = autosync.run_worker(job, show_progress)
            result["anchor_source"] = job["anchor_source"]
            describe(result)
            out = Path(args.out or f"autosync-{song['code']}.json")
            out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  result written to {out} (lyrics and database untouched)")
        else:
            outcome = autosync.process_song(song["id"], options, show_progress)
            print(f"\n  -> {outcome['status']}: {outcome['message']}")
            if outcome["lrc"]:
                print(f"  wrote {outcome['lrc']}" + (f" (old file kept as {outcome['backup']})"
                                                      if outcome["backup"] else ""))
            print(f"  confidence details: {outcome['sidecar']}")
    except SongLockedError:
        print("  refused: this song is locked against auto sync.")
        return False
    except autosync.AutoSyncError as error:
        print(f"\n  failed: {error}")
        return False

    print(f"  took {time.time() - started:.0f}s")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatic word-by-word lyric sync.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--song", help="the song's number code, e.g. 1004")
    target.add_argument("--queued", action="store_true", help="every queued song")
    parser.add_argument("--no-isolate", action="store_true", help="skip vocal isolation")
    parser.add_argument("--dry-run", action="store_true",
                        help="write only the --out file; change no lyrics and no database")
    parser.add_argument("--anchors", choices=["current", "lrclib"], default="current",
                        help="where line times come from (dry runs only)")
    parser.add_argument("--out", help="result file for --dry-run")
    args = parser.parse_args()

    if args.anchors != "current" and not args.dry_run:
        parser.error("--anchors lrclib is only for --dry-run tests")

    init_db()
    with session() as conn:
        apply_sync_locks(conn)
        if args.song:
            song = song_model.get_by_code(conn, args.song)
            if song is None:
                print(f"No song with code {args.song}.")
                return 1
            songs = [song]
        else:
            songs = song_model.list_by_sync_status(conn, "queued")
            if not songs:
                print("No songs are queued for auto sync.")
                return 0

    ok = [run_one(song, args) for song in songs]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
