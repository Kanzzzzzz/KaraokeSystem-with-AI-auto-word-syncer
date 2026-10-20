"""
Writing a song's lyric files to disk, and recording them in the database.

Lyrics reach a song in three ways:

  - the sync editor saves timing tapped by hand       -> .lrc
  - LRCLIB supplies timing, or just the plain words  -> .lrc or .txt
  - the user pastes plain words into the sync editor -> .txt

All three go through this module, so every lyric file is named, placed and
recorded the same way no matter where it came from.

The two file types:

  .lrc  timed lyrics. Setting one marks the song as synced.
  .txt  plain, untimed lyrics. This is what the sync editor times, so the
        song still needs manual sync until an .lrc exists.
"""

import json
import re
import shutil
from pathlib import Path

from config import DATA_DIR
from models import song as song_model

# Characters Windows does not allow in file names. A song called "AC/DC" or
# "What?" would otherwise fail to save - or worse, "/" would be read as a
# folder separator and write the file somewhere unexpected.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(text: str) -> str:
    return _UNSAFE_FILENAME.sub("_", text).strip(" .") or "untitled"


def _relative(path: Path) -> str:
    """Stored paths are relative to data/, so the database stays portable."""
    return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()


def lyrics_file_path(song: dict, suffix: str) -> Path:
    """
    Where this song's `.lrc` or `.txt` belongs.

    In order of preference:
      1. the file of this type it already has - replace it in place
      2. next to its other lyric file, so a song's files stay together
      3. data/library/lyrics/Artist - Title<suffix>
    """
    if suffix == ".lrc":
        own, sibling = song.get("lyrics_path"), song.get("source_text_path")
    else:
        own, sibling = song.get("source_text_path"), song.get("lyrics_path")

    if own:
        return DATA_DIR / own
    if sibling:
        return (DATA_DIR / sibling).with_suffix(suffix)

    name = _safe_name(f"{song['artist']} - {song['title']}")
    return DATA_DIR / "library" / "lyrics" / f"{name}{suffix}"


def _write(path: Path, text: str) -> str | None:
    """
    Write a file, keeping the previous version as `<name>.bak` if it differs.

    Replacing timing is not something to do irreversibly: a file you tapped
    against your own audio may be better than a downloaded one. One backup is
    enough to undo the most recent replacement. The `.bak` extension also
    means the importer ignores it - it only looks for `.lrc` and `.txt`.

    Returns the backup's relative path, or None when nothing was replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    backup = None
    if path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        if old != text:
            backup_path = path.with_name(path.name + ".bak")
            shutil.copyfile(path, backup_path)
            backup = _relative(backup_path)

    path.write_text(text, encoding="utf-8")
    return backup


def save_synced_lrc(conn, song: dict, lrc_text: str) -> dict:
    """
    Save timed lyrics and mark the song as synced.

    Returns {"song", "path", "backup"}.
    """
    path = lyrics_file_path(song, ".lrc")
    backup = _write(path, lrc_text)
    relative = _relative(path)
    updated = song_model.set_lyrics_path(conn, song["id"], relative)
    return {"song": updated, "path": relative, "backup": backup}


def autosync_sidecar_path(song: dict) -> Path:
    """
    Where auto sync keeps its detailed result: next to the song's .lrc, as
    "Artist - Title.autosync.json". It holds what an .lrc cannot - each word's
    confidence and flags - for the sync editor to highlight.
    """
    lrc = lyrics_file_path(song, ".lrc")
    return lrc.with_name(lrc.stem + ".autosync.json")


def save_autosync(conn, song: dict, lrc_text: str | None, sidecar: dict) -> dict:
    """
    Write auto sync's output: always the sidecar, plus the .lrc when
    `lrc_text` is given (None = keep the result as a draft only).

    This is the ONLY place auto sync writes lyric files, and it re-reads the
    song's lock from the database immediately before writing. So even a song
    locked while its job was running is never written to: no .lrc, no .bak,
    no sidecar.

    Returns {"sidecar", "lrc", "backup", "song"}.
    """
    if song_model.is_sync_locked(conn, song["id"]):
        raise song_model.SongLockedError(f"Song {song['id']} is locked against auto sync.")

    sidecar_path = autosync_sidecar_path(song)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    temp = sidecar_path.with_name(sidecar_path.name + ".partial")
    temp.write_text(json.dumps(sidecar, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(sidecar_path)

    saved = {"sidecar": _relative(sidecar_path), "lrc": None, "backup": None, "song": song}
    if lrc_text is not None:
        written = save_synced_lrc(conn, song, lrc_text)
        saved.update(lrc=written["path"], backup=written["backup"], song=written["song"])
    return saved


def mark_autosync_reviewed(conn, song: dict) -> bool:
    """
    Record in the sidecar that auto sync's result has been checked by a
    person - saved from the sync editor - so its low-confidence words are no
    longer highlighted. Returns False (and writes nothing) for a locked song
    or one with no auto-sync result.
    """
    if song_model.is_sync_locked(conn, song["id"]):
        return False
    path = autosync_sidecar_path(song)
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(reviewed=True, applied=True)
    temp = path.with_name(path.name + ".partial")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    return True


def save_plain_text(conn, song: dict, text: str) -> dict:
    """
    Save untimed lyrics. The song stays flagged as needing manual sync.

    Returns {"song", "path", "backup"}.
    """
    # Normalise line endings and drop trailing whitespace, so a paste from a
    # website or Word document does not leave stray "\r" characters behind.
    clean = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    clean = clean.strip() + "\n"

    path = lyrics_file_path(song, ".txt")
    backup = _write(path, clean)
    relative = _relative(path)
    updated = song_model.set_source_text_path(conn, song["id"], relative)
    return {"song": updated, "path": relative, "backup": backup}
