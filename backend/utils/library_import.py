"""
Adding a song through the website.

The sync editor's "Add a song" form uploads the audio from your computer.
This module does what you would otherwise do by hand:

  1. save_upload()  puts the files where scripts/import_songs.py expects them
                    and records the artist, title and language in
                    song_overrides.json:

        original (with vocals)  -> data/library/originals/Artist - Title.mp3
        your own minus-one      -> data/library/audio/Artist - Title (minus).mp3
        pasted lyrics           -> data/library/lyrics/Artist - Title.txt

  2. run_import()   runs in the server's background worker (it takes minutes):
                    makes the minus-one from the original if you gave none,
                    imports the song, looks its timing up on LRCLIB, and
                    queues it for auto sync.

So a song added through the website ends up exactly like one added by
dropping files into the folders and running import_songs.py.
"""

from pathlib import Path

import config
import database
from models import song as song_model
from utils import autosync, lrclib
from utils.importer import clean_name, import_all, normalise_for_matching, save_override, to_relative
from utils.lyrics_store import _safe_name

# Big enough for a long song in WAV (a 6-minute WAV is about 60 MB).
MAX_UPLOAD_MB = 200

# MP3/WAV/OGG can be separated and aligned. M4A cannot be read without ffmpeg,
# so it is only accepted as a ready-made minus-one (the browser plays it fine).
ORIGINAL_EXTENSIONS = {".mp3", ".wav", ".ogg"}
MINUS_EXTENSIONS = config.AUDIO_EXTENSIONS

LANGUAGES = {"English", "Filipino", ""}


class UploadError(Exception):
    """The upload cannot be accepted; the message says why."""


def _extension(file_storage, allowed: set[str], what: str) -> str:
    suffix = Path(file_storage.filename or "").suffix.lower()
    if suffix not in allowed:
        raise UploadError(f"The {what} must be one of: {', '.join(sorted(allowed))}.")
    return suffix


def _already_in_library(conn, artist: str, title: str) -> bool:
    wanted = normalise_for_matching(f"{artist} - {title}")
    for song in song_model.list_all(conn, limit=100_000):
        for name in (f"{song['artist']} - {song['title']}", f"{song['title']} - {song['artist']}"):
            if normalise_for_matching(name) == wanted:
                return True
    return False


def save_upload(conn, *, original, minus, artist: str, title: str,
                language: str, lyrics: str, auto_sync: bool) -> dict:
    """
    Check an upload and save its files. Returns the job for run_import().

    `original` and `minus` are Flask FileStorage objects or None; at least one
    is needed. Raises UploadError with a message for the user.
    """
    artist, title = artist.strip(), title.strip()
    if not artist or not title:
        raise UploadError("Please fill in both the artist and the title.")
    if language not in LANGUAGES:
        raise UploadError("Unknown language.")
    if original is None and minus is None:
        raise UploadError("Choose the original recording (or a minus-one) to upload.")
    if len(lyrics) > 20_000:
        raise UploadError("Those lyrics are too long to be one song.")

    original_ext = _extension(original, ORIGINAL_EXTENSIONS, "original recording") if original else None
    minus_ext = _extension(minus, MINUS_EXTENSIONS, "minus-one") if minus else None

    if _already_in_library(conn, artist, title):
        raise UploadError(f"{artist} - {title} is already in the library.")

    # The same "Artist - Title" naming the importer understands. Characters
    # Windows forbids in file names are replaced, as for lyric files.
    name = _safe_name(f"{artist} - {title}")
    targets = {}
    if original:
        targets["original"] = config.LIBRARY_DIR / "originals" / f"{name}{original_ext}"
    if minus:
        targets["minus"] = config.LIBRARY_DIR / "audio" / f"{name} (minus){minus_ext}"
    # A file with this name is only a problem if a song in the library uses
    # it. Otherwise it is left over from a deleted song (deleting keeps the
    # original recording) and the new upload simply replaces it.
    for path in targets.values():
        if path.exists() and song_model.get_by_audio_path(conn, to_relative(path)):
            raise UploadError(f"A file called {path.name} is already in the library folders.")

    for key, upload in (("original", original), ("minus", minus)):
        if upload is None:
            continue
        path = targets[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".partial")
        upload.save(str(temp))
        temp.replace(path)

    # The lyric sheet, if pasted: used for timing when LRCLIB has no lyrics.
    if lyrics.strip():
        lyric_path = config.LIBRARY_DIR / "lyrics" / f"{name}.txt"
        if not lyric_path.exists():
            lyric_path.parent.mkdir(parents=True, exist_ok=True)
            lyric_path.write_text(lyrics.strip() + "\n", encoding="utf-8")

    # Name of the playback file the importer will see: yours, or the one
    # that will be generated from the original (see autosync.originals_needing_minus).
    playback_name = (targets["minus"].name if minus
                     else f"{clean_name(targets['original'].stem)} (minus).mp3")
    save_override(playback_name, {"artist": artist, "title": title, "language": language})

    return {
        "title": f"{artist} - {title}",
        "original": str(targets["original"]) if original else None,
        "minus": str(targets["minus"]) if minus else None,
        "auto_sync": bool(auto_sync),
    }


def run_import(job: dict, on_progress) -> dict:
    """
    Import an uploaded song (runs in the background worker).

    Returns {"songs": [the new song rows], "notes": [things to tell the user]}.
    """
    notes = []

    # 1. The minus-one, when only the original was uploaded.
    if job["original"] and not job["minus"]:
        with database.session() as conn:
            pending = autosync.originals_needing_minus(conn)
        original = Path(job["original"])
        for source, output in pending:
            if source.resolve() != original.resolve():
                continue

            def progress(message):
                on_progress({"percent": 0.9 * (message.get("percent") or 0),
                             "stage": "making minus-one",
                             "message": "Removing the vocals to make the minus-one (Demucs)..."})

            on_progress({"percent": 0, "stage": "making minus-one",
                         "message": "Removing the vocals to make the minus-one (Demucs)..."})
            autosync.make_minus_one(source, output, progress)

    # 2. Import, 3. LRCLIB, 4. queue for auto sync.
    on_progress({"percent": 92, "stage": "importing", "message": "Adding it to the library..."})
    with database.session() as conn:
        imported = import_all(conn, verbose=False)
        new_songs = imported["added"]

        on_progress({"percent": 95, "stage": "lrclib", "message": "Looking up its timing on LRCLIB..."})
        for song in new_songs:
            try:
                result = lrclib.fetch_for_song(conn, song)
                if result["status"] == "synced":
                    notes.append("Line timing found on LRCLIB.")
                elif result["status"] == "plain_only":
                    notes.append("LRCLIB has the words but no timing that fits.")
                else:
                    notes.append("LRCLIB does not have this song.")
            except lrclib.LrclibError as error:
                notes.append(f"LRCLIB could not be reached ({error}).")

        if job["auto_sync"]:
            with_original = [s for s in new_songs if autosync.find_original(s)]
            if len(with_original) < len(new_songs):
                notes.append("No original recording, so the words cannot be timed automatically.")
            if autosync.queue_new_songs(conn, with_original):
                notes.append("Queued for auto sync.")

        songs = [song_model.get_by_id(conn, s["id"]) for s in new_songs]

    if not songs:
        raise autosync.AutoSyncError("The files were saved, but the import found no new song.")
    return {"songs": songs, "notes": notes}
