"""
Proves that a locked song can never be touched by automatic word sync.

    python tests/test_sync_lock.py

It runs against a throwaway COPY of data/ (via KARAOKE_DATA_DIR), never your
real library, and tries every way auto sync could reach a song:

    queueing it directly           the POST endpoint
    the server's queue pick-up     import_songs.py's auto-trigger
    preparing a job                applying a finished result
    writing the files              the command-line script
    locking a song mid-run

For each locked song it records a SHA-256 of every lyric file before and
after, and checks that no .autosync.json, no .bak and no job folder appeared.

A test that only ever sees "nothing happened" could be passing because
nothing CAN happen. So the same steps are also run on an UNLOCKED song (the
demo song), where they must succeed and write files - proof that the test
would notice a write.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = PROJECT_ROOT / "data"

# --- Make the throwaway copy BEFORE any backend module reads config --------
TEMP = Path(tempfile.mkdtemp(prefix="karaoke-locktest-"))
DATA = TEMP / "data"
shutil.copytree(REAL_DATA, DATA, ignore=shutil.ignore_patterns("autosync_cache"))
os.environ["KARAOKE_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import config  # noqa: E402
import database  # noqa: E402
from models import song as song_model  # noqa: E402
from models.song import SongLockedError  # noqa: E402
from utils import autosync, lyrics_store  # noqa: E402
from utils.importer import apply_sync_locks, import_all  # noqa: E402

assert config.DATA_DIR == DATA.resolve(), "refusing to run: not pointed at the copy"

passed = 0


def check(condition: bool, label: str) -> None:
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    passed += 1
    print(f"  ok  {label}")


def lyric_files(song: dict) -> dict[str, str]:
    """SHA-256 of every file in the lyrics folder that belongs to this song."""
    lrc = lyrics_store.lyrics_file_path(song, ".lrc")
    stem = lrc.stem
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in lrc.parent.iterdir() if p.name.startswith(stem)}


def fake_result(song: dict) -> dict:
    """A plausible finished result, as the worker would return it."""
    current = autosync.read_current_lyrics(song)
    lines = []
    for line in current["lines"]:
        words = line["text"].split()
        lines.append({
            "text": line["text"], "time": line["time"], "anchor": line["time"],
            "words": [{"text": w, "start": line["time"] + 0.3 * i, "end": line["time"] + 0.3 * i + 0.25,
                       "score": 0.9, "flags": [], "confidence": "high"} for i, w in enumerate(words)],
            "words_end": line["time"] + 0.3 * len(words), "confidence": "high"})
    return {"mode": "anchored", "lines": lines, "offset": None, "timings": {},
            "summary": {"status": "done", "reasons": [], "words": 1, "high_confidence": 1,
                        "high_ratio": 1.0}}


def main() -> None:
    database.init_db()
    with database.session() as conn:
        changed = apply_sync_locks(conn)
    print(f"Copy of data/ at {DATA}\nLocks applied from song_overrides.json: "
          f"{[s['title'] for s in changed]}")

    with database.session() as conn:
        songs = song_model.list_all(conn)
    locked = [s for s in songs if s["sync_locked"]]
    unlocked = [s for s in songs if not s["sync_locked"]]
    check({s["title"] for s in locked} == {"Sunflower", "Totoong Tayo"},
          "exactly Sunflower and Totoong Tayo are locked")
    check(any(s["title"] == "Demo Song" for s in unlocked), "the demo song is unlocked (control)")
    demo = next(s for s in unlocked if s["title"] == "Demo Song")

    before = {s["id"]: lyric_files(s) for s in locked}

    from app import create_app
    app, _ = create_app()
    client = app.test_client()

    runner_calls = []

    def fake_runner(job, on_progress):
        runner_calls.append(job["song_id"])
        return fake_result(song_model.get_by_id(database.connect(), job["song_id"]))

    print("\nLocked songs - every path must refuse:")
    for song in locked:
        name = song["title"]
        with database.session() as conn:
            try:
                song_model.set_sync_status(conn, song["id"], "queued")
                check(False, f"{name}: set_sync_status refuses")
            except SongLockedError:
                check(True, f"{name}: set_sync_status refuses")

        try:
            autosync.service.enqueue(song["id"])
            check(False, f"{name}: service.enqueue refuses")
        except SongLockedError:
            check(True, f"{name}: service.enqueue refuses")

        response = client.post(f"/api/songs/{song['id']}/auto-sync", json={})
        check(response.status_code == 423, f"{name}: POST /auto-sync answers 423 Locked")
        state = client.get(f"/api/songs/{song['id']}/auto-sync").get_json()
        check(state["sync_locked"] and state["sync_status"] == "none",
              f"{name}: GET /auto-sync reports locked, status 'none'")

        with database.session() as conn:
            try:
                autosync.prepare_job(conn, song)
                check(False, f"{name}: prepare_job refuses")
            except SongLockedError:
                check(True, f"{name}: prepare_job refuses")

        try:
            autosync.process_song(song["id"], runner=fake_runner)
            check(False, f"{name}: process_song refuses")
        except SongLockedError:
            check(song["id"] not in runner_calls, f"{name}: process_song refuses before running")

        with database.session() as conn:
            try:
                autosync.apply_result(conn, song["id"], fake_result(song))
                check(False, f"{name}: apply_result refuses")
            except SongLockedError:
                check(True, f"{name}: apply_result refuses a finished result")
            try:
                lyrics_store.save_autosync(conn, song, "[00:01.00]x\n", {"x": 1})
                check(False, f"{name}: save_autosync refuses")
            except SongLockedError:
                check(True, f"{name}: save_autosync (the only writer) refuses")

        # Someone sets 'queued' behind the model's back, with raw SQL.
        raw = sqlite3.connect(config.DATABASE_PATH)
        raw.execute("UPDATE songs SET sync_status = 'queued' WHERE id = ?", (song["id"],))
        raw.commit()
        with database.session() as conn:
            listed = [s["id"] for s in song_model.list_by_sync_status(conn, "queued")]
        check(song["id"] not in listed, f"{name}: the server's queue pick-up ignores it even if marked queued")
        autosync.service._pick_up_queued()
        check(song["id"] not in autosync.service._waiting, f"{name}: the worker thread never takes it")
        raw.execute("UPDATE songs SET sync_status = 'none' WHERE id = ?", (song["id"],))
        raw.commit()
        raw.close()

        cli = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "auto_sync.py"), "--song", song["code"]],
            capture_output=True, text=True, env=dict(os.environ))
        check(cli.returncode != 0 and "locked" in cli.stdout, f"{name}: scripts/auto_sync.py refuses")
        cli = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "auto_sync.py"), "--song", song["code"],
             "--dry-run", "--out", str(TEMP / "x.json")],
            capture_output=True, text=True, env=dict(os.environ))
        check(cli.returncode != 0 and not (TEMP / "x.json").exists(),
              f"{name}: scripts/auto_sync.py --dry-run refuses too")

    print("\nMinus-one generation from originals:")
    originals = DATA / "library" / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    for song in locked:
        fake = originals / f"{song['artist']} - {song['title']} (Official Audio).mp3"
        fake.write_bytes(b"not really audio")
    with database.session() as conn:
        targets = [original.name for original, _ in autosync.originals_needing_minus(conn)]
    check(not any(("Sunflower" in n or "Totoong" in n) for n in targets),
          "an original dropped in for a locked song never gets a minus-one made")

    print("\nImport auto-trigger - a fresh import of the same files:")
    config.DATABASE_PATH.unlink()
    for extra in ("-wal", "-shm"):
        Path(str(config.DATABASE_PATH) + extra).unlink(missing_ok=True)
    database.init_db()
    with database.session() as conn:
        imported = import_all(conn, verbose=False)
        queued = autosync.queue_new_songs(conn, imported["added"])
    queued_titles = {s["title"] for s in queued}
    check(not queued_titles & {"Sunflower", "Totoong Tayo"},
          "import_songs.py's auto-trigger does not queue the locked songs")
    check("Demo Song" in queued_titles, "...but does queue the unlocked demo song (control)")
    with database.session() as conn:
        relocked = {s["title"] for s in song_model.list_all(conn) if s["sync_locked"]}
    check(relocked == {"Sunflower", "Totoong Tayo"}, "a rebuilt database is locked again from the overrides file")

    print("\nFiles of the locked songs:")
    with database.session() as conn:
        songs_now = {s["title"]: s for s in song_model.list_all(conn)}
    for song in locked:
        after = lyric_files(songs_now[song["title"]])
        check(after == before[song["id"]], f"{song['title']}: every lyric file byte-for-byte unchanged")
        check(not any(n.endswith(".autosync.json") for n in after),
              f"{song['title']}: no .autosync.json created")
    jobs = config.AUTOSYNC_CACHE_DIR / "jobs"
    made = sorted(p.name for p in jobs.iterdir()) if jobs.is_dir() else []
    locked_ids = {f"song-{songs_now[s['title']]['id']}" for s in locked}
    check(not locked_ids & set(made), "no job folder was created for a locked song")

    print("\nControl - the same paths on the unlocked demo song must WRITE:")
    demo = songs_now["Demo Song"]
    demo_before = lyric_files(demo)
    outcome = autosync.process_song(demo["id"], runner=fake_runner)
    demo_after = lyric_files(song_model.get_by_id(database.connect(), demo["id"]))
    check(outcome["lrc"] is not None and demo_after != demo_before,
          "demo song: .lrc rewritten - the test does detect writes")
    check(any(n.endswith(".autosync.json") for n in demo_after), "demo song: sidecar written")

    print("\nLocking a song while its job is running:")
    demo_before = lyric_files(song_model.get_by_id(database.connect(), demo["id"]))

    def lock_midway(job, on_progress):
        with database.session() as conn:
            song_model.set_sync_locked(conn, job["song_id"], True)
        return fake_runner(job, on_progress)

    try:
        autosync.process_song(demo["id"], runner=lock_midway)
        check(False, "a song locked mid-run is not written")
    except SongLockedError:
        after = lyric_files(song_model.get_by_id(database.connect(), demo["id"]))
        check(after == demo_before, "a song locked mid-run is not written")

    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        # sqlite may keep files open a moment on Windows; failing to clean up
        # a temp folder is harmless.
        shutil.rmtree(TEMP, ignore_errors=True)
