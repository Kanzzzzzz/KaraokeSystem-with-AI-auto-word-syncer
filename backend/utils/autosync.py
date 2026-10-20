"""
Automatic word sync - the karaoke server's side of it.

It also makes MINUS-ONE tracks: give it only the original recording (in
data/library/originals/) and it separates the vocals out, and the rest of the
song becomes the karaoke's playback track (make_missing_minus_tracks).

The alignment itself (backend/autosync_engine/) needs torch, Demucs and a
1.2 GB speech model. None of that is imported here. Instead the server:

  1. prepares a JOB: which audio to use, the lyric lines and their LRCLIB
     times (prepare_job)
  2. runs scripts/autosync_worker.py as a SEPARATE PROCESS with the
     .venv-align Python, reading its progress line by line (run_worker)
  3. applies the RESULT: writes the .lrc and the confidence file, or keeps it
     as a draft, and sets the song's sync_status (apply_result)

Why a separate process rather than importing the libraries into Flask:
  - the karaoke app keeps its small requirements.txt and starts on any
    computer, with or without the machine-learning libraries installed
  - if the alignment runs out of memory or crashes, only the worker dies;
    the karaoke screen and the queue carry on
  - the worker can be run by hand for testing (scripts/auto_sync.py)

-------------------------------------------------------------------------------
LOCKED SONGS
-------------------------------------------------------------------------------

A song with sync_locked set is never touched by auto sync. The lock is checked
at EVERY step, not just the first: when queueing (models/song.py refuses in
SQL), when a job is prepared, and again immediately before any file is
written (lyrics_store.save_autosync) - so a song locked while its job is
already running is still protected.

-------------------------------------------------------------------------------
HAND-TAPPED SONGS
-------------------------------------------------------------------------------

If a song's current .lrc has word timing tapped by hand, the result is saved
only as a DRAFT (the .autosync.json file) and the .lrc is left alone - your
own timing may well be better. The sync editor offers "Load auto-sync draft".
Songs with only line timing get the result applied directly, and the old .lrc
is kept as .lrc.bak, as with every other save.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import config
import database
from models import song as song_model
from models.song import SongLockedError
from utils import lrclib, lyrics_store
from utils.importer import clean_name, load_overrides, normalise_for_matching
from utils.lrc import build_lrc, parse_lrc, read_plain_lines

# Written in the [re:] tag of every .lrc auto sync produces, so the editor
# and the draft rule can tell auto-synced timing from hand-tapped timing.
AUTO_SOURCE = "Karaoke System auto-sync"

# The editor, and the song_overrides.json "language" field, use names; the
# Whisper fallback needs codes.
_LANGUAGE_CODES = {"filipino": "tl", "tagalog": "tl", "taglish": "tl", "english": "en"}


class AutoSyncError(Exception):
    """Auto sync could not run or finish; the message says why."""


# ==========================================================================
# 1. Preparing a job
# ==========================================================================

def find_original(song: dict, overrides: dict | None = None) -> Path | None:
    """
    The original recording (with vocals) for a minus-one track, if one exists.

    Looked for in data/library/originals/ (and data/samples/originals/) by
    the same forgiving filename match used for lyrics, so
    "Artist - Title (minus).mp3" finds "Artist - Title (Official Audio).mp3".
    An "original": "<filename>" entry in song_overrides.json wins over the
    automatic match, for files named too differently to be paired.
    """
    if overrides is None:
        overrides = load_overrides()
    audio_name = Path(song["audio_path"]).name
    override = overrides.get(audio_name.lower(), {})

    candidates = [p for folder in config.ORIGINALS_DIRS if folder.is_dir()
                  for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in config.AUDIO_EXTENSIONS]

    if override.get("original"):
        wanted = override["original"].lower()
        return next((p for p in candidates if p.name.lower() == wanted), None)

    target = normalise_for_matching(Path(audio_name).stem)
    return next((p for p in candidates if normalise_for_matching(p.stem) == target), None)


def language_code(song: dict) -> str | None:
    """'Filipino' -> 'tl', 'English' -> 'en', unknown -> None (Whisper guesses)."""
    return _LANGUAGE_CODES.get((song.get("language") or "").strip().lower())


def read_current_lyrics(song: dict) -> dict:
    """
    What lyrics the song has now:
        {"kind": "lrc",   "metadata": {...}, "lines": [{"time", "text", "words"}]}
        {"kind": "plain", "lines": [{"text", "kind"}]}
        {"kind": "none"}
    """
    if song.get("lyrics_path"):
        path = config.DATA_DIR / song["lyrics_path"]
        if path.is_file():
            parsed = parse_lrc(path.read_text(encoding="utf-8", errors="replace"))
            return {"kind": "lrc", **parsed}
    if song.get("source_text_path"):
        path = config.DATA_DIR / song["source_text_path"]
        if path.is_file():
            return {"kind": "plain",
                    "lines": read_plain_lines(path.read_text(encoding="utf-8", errors="replace"))}
    return {"kind": "none"}


def has_hand_word_timing(current: dict) -> bool:
    """True if the song's .lrc has word timing that auto sync did not write."""
    if current["kind"] != "lrc":
        return False
    written_by = current["metadata"].get("re", "")
    has_words = any(line.get("words") for line in current["lines"])
    return has_words and not written_by.startswith(AUTO_SOURCE)


def _lrclib_lines(song: dict) -> list[dict] | None:
    """Timed lines from LRCLIB (not saved anywhere), or None."""
    try:
        found = lrclib.find_lyrics(song["title"], song["artist"], song.get("duration_sec"))
    except lrclib.LrclibError:
        return None
    if not found["synced"]:
        return None
    lines = parse_lrc(found["synced"]["syncedLyrics"])["lines"]
    return [{"time": line["time"], "text": line["text"]} for line in lines]


def _previous_anchors(song: dict) -> tuple[list[dict] | None, str | None]:
    """
    The line times the LAST auto-sync run started from, as recorded in the
    song's .autosync.json - used offline when LRCLIB cannot be asked again.
    """
    sidecar = lyrics_store.autosync_sidecar_path(song)
    if not sidecar.is_file():
        return None, None
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    lines = [{"time": line.get("anchor"), "text": line["text"]} for line in data.get("lines", [])]
    if not any(line["time"] is not None for line in lines):
        return None, None
    return lines, data.get("anchor_source") or "the previous auto-sync run"


def prepare_job(conn, song: dict, isolate_vocals: bool = True, anchors: str = "current") -> dict:
    """
    Describe everything the worker needs for one song.

    Where the lyric lines and their anchor times come from, in order:
      1. the song's current .lrc (whatever made it: LRCLIB, the editor...)
      2. LRCLIB, looked up now - also used first when anchors="lrclib", which
         the accuracy tests use so they never anchor on hand-tapped times
      3. the plain .txt lyric sheet, with no times (aligned unanchored)
      4. nothing: Whisper transcribes the vocals

    Raises SongLockedError for a locked song, AutoSyncError if the song
    cannot be synced at all.
    """
    if song_model.is_sync_locked(conn, song["id"]):
        raise SongLockedError(f"{song['artist']} - {song['title']} is locked against auto sync.")

    playback = config.DATA_DIR / song["audio_path"]
    if not playback.is_file():
        raise AutoSyncError(f"The audio file is missing: {song['audio_path']}")

    current = read_current_lyrics(song)
    lines, anchor_source = None, None

    if anchors == "lrclib":
        lines = _lrclib_lines(song)
        anchor_source = "LRCLIB" if lines else None

    # A .lrc that auto sync itself wrote must NOT supply the line times for
    # the next run: its mistakes would be fed back in as anchors and repeat
    # forever. Its original anchors are fetched from LRCLIB again instead - or,
    # offline, taken from the last result file, which records them.
    auto_written = (current["kind"] == "lrc"
                    and current["metadata"].get("re", "").startswith(AUTO_SOURCE))
    if lines is None and auto_written:
        lines = _lrclib_lines(song)
        anchor_source = "LRCLIB" if lines else None
        if lines is None:
            lines, anchor_source = _previous_anchors(song)

    if (lines is None and current["kind"] == "lrc" and not auto_written
            and any(l["text"].strip() for l in current["lines"])):
        lines = [{"time": line["time"], "text": line["text"]} for line in current["lines"]]
        anchor_source = current["metadata"].get("re") or "the song's .lrc file"

    if lines is None and anchors != "lrclib":
        lines = _lrclib_lines(song)
        anchor_source = "LRCLIB" if lines else None

    if lines is None and current["kind"] == "plain":
        lines = [{"time": None, "text": line["text"]}
                 for line in current["lines"] if line["kind"] == "lyric"] or None
        anchor_source = None

    job_dir = config.AUTOSYNC_CACHE_DIR / "jobs" / f"song-{song['id']}"
    job_dir.mkdir(parents=True, exist_ok=True)
    original = find_original(song)

    return {
        "song_id": song["id"],
        "playback_audio": str(playback),
        "original_audio": str(original) if original else None,
        "isolate_vocals": bool(isolate_vocals),
        "lines": lines,
        "anchor_source": anchor_source,
        "language": language_code(song),
        "cache_dir": str(config.AUTOSYNC_CACHE_DIR),
        "job_dir": str(job_dir),
        "result_path": str(job_dir / "result.json"),
    }


# ==========================================================================
# 2. Running the worker
# ==========================================================================

def run_worker(job: dict, on_progress=lambda message: None) -> dict:
    """
    Run scripts/autosync_worker.py on a job and return its result.

    Progress messages ({"percent", "stage", "message"}) are passed to
    `on_progress` as they arrive. The worker's own log - warnings, and the
    full error if it crashes - goes to worker.log in the job's folder.
    """
    python = config.AUTOSYNC_PYTHON
    if not python.is_file():
        raise AutoSyncError(
            "The auto-sync environment is not installed. Run: python scripts/setup_autosync.py")

    job_dir = Path(job["job_dir"])
    job_file = job_dir / "job.json"
    job_file.write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    result_path = Path(job["result_path"])
    result_path.unlink(missing_ok=True)   # never mistake an old result for a new one

    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    # On Windows, stop a console window flashing up for the worker.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    error, finished_path = None, None
    with open(job_dir / "worker.log", "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(python), str(config.AUTOSYNC_WORKER_SCRIPT), str(job_file)],
            stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8",
            cwd=str(config.PROJECT_ROOT), env=environment, creationflags=flags,
        )
        for raw in process.stdout:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue   # not one of ours; ignore rather than crash
            if message.get("type") == "progress":
                on_progress(message)
            elif message.get("type") == "result":
                finished_path = Path(message["path"])
            elif message.get("type") == "error":
                error = message.get("message")
        code = process.wait()

    if error:
        raise AutoSyncError(error)
    if code != 0 or finished_path is None or not finished_path.is_file():
        raise AutoSyncError(
            f"The auto-sync worker stopped unexpectedly (exit code {code}). "
            f"See {job_dir / 'worker.log'}.")

    return json.loads(finished_path.read_text(encoding="utf-8"))


# ==========================================================================
# Making minus-one tracks from originals
# ==========================================================================

def originals_needing_minus(conn=None) -> list[tuple[Path, Path]]:
    """
    Every original recording that has no playback track yet, paired with
    where its minus-one should go:

        data/library/originals/Artist - Title (Official Audio).mp3
          -> data/library/audio/Artist - Title (minus).mp3

    "No playback track yet" uses the same forgiving name match as everywhere
    else, so an original is skipped when you supplied your own minus-one for
    it. With a database connection, songs already in the library also count,
    matched by artist and title in EITHER order - a file named
    "Title - Artist" and an original named "Artist - Title" are the same song.
    That is what keeps a locked song safe here: it is already in the library,
    so no second copy of it is ever made (and then auto-synced).
    """
    known = set()
    if conn is not None:
        for song in song_model.list_all(conn, limit=100_000):
            known.add(normalise_for_matching(f"{song['artist']} - {song['title']}"))
            known.add(normalise_for_matching(f"{song['title']} - {song['artist']}"))

    pairs = []
    for originals_dir in config.ORIGINALS_DIRS:
        if not originals_dir.is_dir():
            continue
        audio_dir = originals_dir.parent / "audio"
        have = set()
        if audio_dir.is_dir():
            have = {normalise_for_matching(p.stem) for p in audio_dir.iterdir()
                    if p.suffix.lower() in config.AUDIO_EXTENSIONS}

        for original in sorted(originals_dir.iterdir()):
            if not original.is_file() or original.suffix.lower() not in config.AUDIO_EXTENSIONS:
                continue
            name = normalise_for_matching(original.stem)
            if name in have or name in known:
                continue
            pairs.append((original, audio_dir / f"{clean_name(original.stem)} (minus).mp3"))
    return pairs


def make_minus_one(original: Path, output: Path, on_progress=lambda message: None) -> dict:
    """Run the worker's make_minus task: Demucs, then save the instrumental."""
    job_dir = config.AUTOSYNC_CACHE_DIR / "jobs" / f"minus-{normalise_for_matching(original.stem)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "task": "make_minus",
        "original_audio": str(original),
        "output_path": str(output),
        "cache_dir": str(config.AUTOSYNC_CACHE_DIR),
        "job_dir": str(job_dir),
        "result_path": str(job_dir / "result.json"),
    }
    return run_worker(job, on_progress)


# ==========================================================================
# 3. Applying the result
# ==========================================================================

def result_to_lrc_lines(result: dict) -> list[dict]:
    """The result's lines in the shape build_lrc() takes (see utils/lrc.py)."""
    out = []
    for line in result["lines"]:
        if line["words"]:
            out.append({
                "time": line["words"][0]["start"],
                "text": line["text"],
                "words": [{"time": w["start"], "text": w["text"]} for w in line["words"]],
                "words_end": line["words_end"],
            })
        elif line.get("time") is not None:
            out.append({"time": line["time"], "text": line["text"]})   # break marker
    return out


def apply_result(conn, song_id: int, result: dict, job: dict | None = None) -> dict:
    """
    Save a finished result for a song and set its sync_status.

    Returns {"status", "message", "draft", "sidecar", "lrc", "backup"}.
    Raises SongLockedError if the song is locked - checked again here, since
    the lock may have been set while the job was running.
    """
    song = song_model.get_by_id(conn, song_id)
    if song is None or song["sync_locked"]:
        raise SongLockedError(f"Song {song_id} is locked against auto sync (or was deleted).")

    draft = has_hand_word_timing(read_current_lyrics(song))
    summary = result["summary"]

    sidecar = {
        "format": "karaoke-autosync-1",
        "created": datetime.now().isoformat(timespec="seconds"),
        "applied": not draft,
        "anchor_source": (job or {}).get("anchor_source"),
        "original_audio": Path(job["original_audio"]).name if job and job.get("original_audio") else None,
        **result,
    }

    lrc_text = None
    if not draft:
        lrc_text = build_lrc(result_to_lrc_lines(result), title=song["title"],
                             artist=song["artist"], source=AUTO_SOURCE)

    saved = lyrics_store.save_autosync(conn, song, lrc_text, sidecar)

    status = summary["status"]
    parts = []
    if draft:
        parts.append("Saved as a draft: this song has word timing you tapped by hand, "
                     "which was kept. Load the draft in the sync editor to compare.")
    parts += summary["reasons"]
    parts.append(f"{summary['high_confidence']} of {summary['words']} words high confidence.")
    message = " ".join(parts)

    song_model.set_sync_status(conn, song_id, status, message)
    return {"status": status, "message": message, "draft": draft, **saved}


# ==========================================================================
# The whole thing, for one song
# ==========================================================================

def process_song(song_id: int, options: dict | None = None,
                 on_progress=lambda message: None, runner=run_worker) -> dict:
    """
    Prepare, run and apply auto sync for one song, keeping sync_status up to
    date throughout. Each step uses its own short database session, so the
    database is never held open during the minutes the worker runs.
    """
    options = options or {}
    with database.session() as conn:
        song = song_model.get_by_id(conn, song_id)
        if song is None:
            raise AutoSyncError(f"No song with id {song_id}.")
        job = prepare_job(conn, song, isolate_vocals=options.get("isolate_vocals", True))
        song_model.set_sync_status(conn, song_id, "processing", None)

    try:
        result = runner(job, on_progress)
    except Exception as error:
        with database.session() as conn:
            try:
                song_model.set_sync_status(conn, song_id, "failed", str(error))
            except SongLockedError:
                pass
        raise

    with database.session() as conn:
        return apply_result(conn, song_id, result, job)


def queue_new_songs(conn, songs: list[dict]) -> list[dict]:
    """Mark newly imported songs as waiting for auto sync. Locked songs are skipped."""
    queued = []
    for song in songs:
        try:
            queued.append(song_model.set_sync_status(conn, song["id"], "queued", None))
        except SongLockedError:
            continue
    return queued


# ==========================================================================
# The background worker thread inside the server
# ==========================================================================

class AutoSyncService:
    """
    Runs the slow background jobs one at a time, in a single thread:

      "sync"    auto sync for one song                  (process_song)
      "import"  a song uploaded through the website:    (library_import.py)
                make its minus-one, import it, look it up on LRCLIB

    One at a time, because Demucs and the alignment model each use several GB
    of memory - two at once would not fit in a typical laptop. Requests only
    ever add a job and return immediately; the browser then asks for
    progress about once a second.
    """

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._waiting: set[int] = set()            # songs queued or running
        self._progress: dict[int, dict] = {}       # per song
        self._imports: dict[int, dict] = {}        # per import job: its state
        self._next_import = 1
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the thread. Called once, by app.py, when the server starts."""
        if self._thread is not None:
            return
        with database.session() as conn:
            # A song left 'processing' means the server stopped mid-run.
            for song in song_model.list_by_sync_status(conn, "processing"):
                song_model.set_sync_status(conn, song["id"], "queued",
                                           "Restarted: the server stopped during the last run.")
        self._thread = threading.Thread(target=self._loop, name="autosync", daemon=True)
        self._thread.start()

    # --- auto sync ---------------------------------------------------------

    def enqueue(self, song_id: int, options: dict | None = None) -> dict:
        """
        Queue a song. Raises SongLockedError for a locked song, LookupError
        for an unknown one, and RuntimeError if it is already waiting or running.
        """
        with database.session() as conn:
            song = song_model.get_by_id(conn, song_id)
            if song is None:
                raise LookupError(f"No song with id {song_id}.")
            with self._guard:
                if song_id in self._waiting:
                    raise RuntimeError("Auto sync is already queued or running for this song.")
            song = song_model.set_sync_status(conn, song_id, "queued", None)
        self._add(song_id, options or {})
        return song

    def progress(self, song_id: int) -> dict | None:
        with self._guard:
            return self._progress.get(song_id)

    def _add(self, song_id: int, options: dict) -> None:
        with self._guard:
            if song_id in self._waiting:
                return
            self._waiting.add(song_id)
            self._progress[song_id] = {"percent": 0, "stage": "queued",
                                       "message": "Waiting for its turn..."}
        self._jobs.put(("sync", song_id, options))

    def _pick_up_queued(self) -> None:
        """Songs queued from outside the server, e.g. by import_songs.py."""
        with database.session() as conn:
            for song in song_model.list_by_sync_status(conn, "queued"):
                self._add(song["id"], {})

    # --- imports -------------------------------------------------------------

    def submit_import(self, job: dict) -> int:
        """Queue an uploaded song for importing; returns the job's number."""
        with self._guard:
            job_id = self._next_import
            self._next_import += 1
            self._imports[job_id] = {"id": job_id, "status": "queued", "percent": 0,
                                     "stage": "queued", "message": "Waiting for its turn...",
                                     "songs": [], "notes": [], "error": None,
                                     "title": job.get("title")}
        self._jobs.put(("import", job_id, job))
        return job_id

    def import_status(self, job_id: int) -> dict | None:
        with self._guard:
            state = self._imports.get(job_id)
            return dict(state) if state else None

    def is_busy_with(self, song_id: int) -> bool:
        """True while auto sync for this song is queued or running."""
        with self._guard:
            return song_id in self._waiting

    def activity(self) -> dict:
        """
        Everything the background thread is working on or has waiting, so any
        page can show it: {"imports": [...], "syncs": [{"song_id", ...}]}.
        """
        with self._guard:
            imports = [{"title": state.get("title"), "status": state["status"],
                        "percent": state.get("percent"), "message": state.get("message")}
                       for state in self._imports.values()
                       if state["status"] in ("queued", "running")]
            syncs = [{"song_id": song_id, **progress}
                     for song_id, progress in self._progress.items()]
        return {"imports": imports, "syncs": syncs}

    def _run_import(self, job_id: int, job: dict) -> None:
        from utils import library_import   # imported here: it imports this module

        def on_progress(message):
            with self._guard:
                self._imports[job_id].update(
                    status="running", **{k: message.get(k) for k in ("percent", "stage", "message")})

        try:
            outcome = library_import.run_import(job, on_progress)
            with self._guard:
                self._imports[job_id].update(status="done", percent=100, stage="finished",
                                             message="Imported.", **outcome)
            print(f"  [import] {job.get('title')}: done")
        except Exception as error:   # keep the thread alive for the next job
            with self._guard:
                self._imports[job_id].update(status="failed", error=str(error),
                                             message=str(error))
            print(f"  [import] {job.get('title')}: failed - {error}")
        # The import may have queued the new song for auto sync.
        self._pick_up_queued()

    # --- the thread ----------------------------------------------------------

    def _loop(self) -> None:
        self._pick_up_queued()
        while True:
            try:
                kind, key, payload = self._jobs.get(timeout=config.AUTOSYNC_POLL_SECONDS)
            except queue.Empty:
                self._pick_up_queued()
                continue

            if kind == "import":
                self._run_import(key, payload)
            else:
                self._run_sync(key, payload)

    def _run_sync(self, song_id: int, options: dict) -> None:
        def on_progress(message):
            with self._guard:
                self._progress[song_id] = {key: message.get(key)
                                           for key in ("percent", "stage", "message")}

        started = time.time()
        try:
            outcome = process_song(song_id, options, on_progress)
            print(f"  [auto sync] song {song_id}: {outcome['status']} "
                  f"in {time.time() - started:.0f}s")
        except SongLockedError:
            print(f"  [auto sync] song {song_id}: skipped - it is locked")
        except Exception as error:   # keep the thread alive for the next job
            print(f"  [auto sync] song {song_id}: failed - {error}")
        finally:
            with self._guard:
                self._waiting.discard(song_id)
                self._progress.pop(song_id, None)


# The one service the whole server shares.
service = AutoSyncService()
