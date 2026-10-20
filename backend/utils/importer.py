"""
Scans the media folders and adds any new songs to the database.

The importer works out a song's title and artist from its filename, pairs it
with a matching lyric file, reads its duration, and assigns the next free
number code.

Expected filename convention:

    Artist - Title (minus).mp3      ->  artist "Artist",  title "Title"
    Artist - Title.mp3              ->  same
    Title.mp3                       ->  artist "Unknown Artist"

Lyrics are matched by comparing normalised filenames, so
"Artist - Title (minus).mp3" pairs with "Artist - Title.txt" even though the
names are not identical.
"""

import json
import re
from pathlib import Path

from config import AUDIO_EXTENSIONS, DATA_DIR, MEDIA_SOURCES, OVERRIDES_PATH
from models import song as song_model

# Suffixes that describe the RECORDING rather than the song, and so should not
# end up in the displayed title. Karaoke tracks are full of these.
_NOISE_SUFFIXES = re.compile(
    r"\s*[\(\[]\s*(minus|minus one|instrumental|karaoke|backing track|off ?vocal|"
    r"no vocals?|lyrics|audio|official.*?|hd|hq)\s*[\)\]]",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    """Strip recording-description suffixes and tidy up whitespace."""
    cleaned = _NOISE_SUFFIXES.sub("", name)
    return re.sub(r"\s+", " ", cleaned).strip(" -_")


def normalise_for_matching(name: str) -> str:
    """
    Reduce a filename to a comparable key so audio and lyrics can be paired.

    "Rex Orange County - Sunflower (minus)" and "Rex Orange County - Sunflower"
    both reduce to "rexorangecountysunflower", so they match.
    """
    return re.sub(r"[^a-z0-9]", "", clean_name(name).lower())


def split_artist_title(stem: str) -> tuple[str, str]:
    """
    Split "Artist - Title" into its two halves.

    Only the FIRST " - " is used as the separator, so a title that itself
    contains a dash ("Artist - Song - Live") keeps the rest intact.
    """
    cleaned = clean_name(stem)

    if " - " in cleaned:
        artist, title = cleaned.split(" - ", 1)
        return artist.strip(), title.strip()

    return "Unknown Artist", cleaned


def load_overrides() -> dict:
    """
    Read data/song_overrides.json, if it exists.

    The file maps an audio filename to corrected fields:

        { "Totoong Tayo - Jin DC (minus).mp3": {
              "artist": "Jin DC", "title": "Totoong Tayo", "language": "Filipino" } }

    This exists because filename conventions are not consistent - one file may
    be "Artist - Title" and the next "Title - Artist", and no amount of parsing
    can tell those apart. Keeping the corrections in a data file (rather than
    editing the database by hand) means they are reapplied automatically every
    time the library is rebuilt.

    Keys are matched case-insensitively, and either the full filename or the
    name without its extension will work.

    One more field, "sync_locked": true, protects a song from auto sync - see
    apply_sync_locks() below.
    """
    if not OVERRIDES_PATH.is_file():
        return {}

    try:
        raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # A broken overrides file should warn, not halt the whole import.
        print(f"  ! Could not read {OVERRIDES_PATH.name}: {exc}")
        return {}

    normalised = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        normalised[key.lower()] = value
        normalised[Path(key).stem.lower()] = value

    return normalised


def save_override(audio_filename: str, fields: dict) -> None:
    """
    Add or update one song's entry in song_overrides.json.

    Used when a song is added through the website: the artist, title and
    language typed into the form are recorded here, so they are applied on
    import - and again after any database reset - exactly like a correction
    written by hand. Other entries, and any fields of this entry not given,
    are left as they were.
    """
    raw = {}
    if OVERRIDES_PATH.is_file():
        raw = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))

    entry = raw.get(audio_filename, {})
    entry.update({key: value for key, value in fields.items() if value not in (None, "")})
    raw[audio_filename] = entry

    temp = OVERRIDES_PATH.with_name(OVERRIDES_PATH.name + ".partial")
    temp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(OVERRIDES_PATH)


def apply_sync_locks(conn, overrides: dict | None = None) -> list[dict]:
    """
    Copy every "sync_locked" setting from song_overrides.json into the database.

    The override file is the lasting record of which songs are locked: the
    database can be rebuilt at any time, the override file cannot be lost that
    way. So this runs whenever the app starts, on every import, and before any
    auto-sync run - a lock written in the file is always in force.

    Only songs whose override actually mentions "sync_locked" are changed, so
    a lock set some other way is not undone by an override that says nothing.

    Returns the songs whose lock changed.
    """
    if overrides is None:
        overrides = load_overrides()

    changed = []
    for song in song_model.list_all(conn, limit=100_000):
        override = overrides.get(Path(song["audio_path"]).name.lower())
        if not override or "sync_locked" not in override:
            continue

        locked = bool(override["sync_locked"])
        if song["sync_locked"] != locked:
            changed.append(song_model.set_sync_locked(conn, song["id"], locked))

    return changed


def read_duration(audio_path: Path) -> float | None:
    """
    Read the track length in seconds.

    mutagen is an optional convenience: if it is missing or the file has no
    readable tags, we return None and the app simply shows no duration rather
    than refusing to import the song.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(audio_path)
        if audio is not None and audio.info is not None:
            return round(float(audio.info.length), 2)
    except Exception:
        # Deliberately broad: a corrupt tag should never stop an import.
        pass

    return None


def find_lyrics(audio_stem: str, lyrics_dir: Path) -> tuple[Path | None, Path | None]:
    """
    Look for lyric files belonging to this song.

    Returns (lrc_path, plain_text_path). Either may be None. A .lrc means the
    song is ready to play; a .txt means it still needs the sync editor.
    """
    if not lyrics_dir.is_dir():
        return None, None

    target = normalise_for_matching(audio_stem)
    lrc_path = None
    text_path = None

    for candidate in lyrics_dir.iterdir():
        if not candidate.is_file():
            continue
        if normalise_for_matching(candidate.stem) != target:
            continue

        suffix = candidate.suffix.lower()
        if suffix == ".lrc":
            lrc_path = candidate
        elif suffix == ".txt":
            text_path = candidate

    return lrc_path, text_path


def to_relative(path: Path | None) -> str | None:
    """
    Store paths relative to data/ (e.g. "library/audio/song.mp3").

    Relative paths keep the database portable and, just as importantly, stop
    any absolute filesystem path from being handed out over the API.
    """
    if path is None:
        return None
    return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()


def import_all(conn, verbose: bool = True) -> dict:
    """
    Scan every configured media folder and import anything new.

    Re-running this is safe: songs already in the database are skipped by
    their audio path, so nothing is duplicated.
    """
    added = []
    skipped = []
    missing_lyrics = []
    relinked_lyrics = []

    overrides = load_overrides()

    # Locks first, so no later step in this import can see a song as unlocked.
    apply_sync_locks(conn, overrides)

    for audio_dir, lyrics_dir in MEDIA_SOURCES:
        if not audio_dir.is_dir():
            continue

        # sorted() makes code assignment deterministic - the same folder
        # always produces the same numbers, which matters if you rebuild the
        # database and have written codes down somewhere.
        for audio_file in sorted(audio_dir.iterdir()):
            if not audio_file.is_file():
                continue
            if audio_file.suffix.lower() not in AUDIO_EXTENSIONS:
                continue

            relative_audio = to_relative(audio_file)

            existing = song_model.get_by_audio_path(conn, relative_audio)
            if existing:
                # The audio was imported already, but a matching .lrc may
                # have been dropped in afterwards - by hand, or downloaded
                # from a lyrics site once one was found. Without this check,
                # re-running the importer would never notice it: the song is
                # already in the database, so the scan below (which only
                # looks at brand-new audio files) never reaches this song
                # again, and the .lrc would sit on disk unused forever.
                if not existing["is_synced"]:
                    lrc_path, _ = find_lyrics(audio_file.stem, lyrics_dir)
                    if lrc_path is not None:
                        relinked = song_model.set_lyrics_path(
                            conn, existing["id"], to_relative(lrc_path)
                        )
                        relinked_lyrics.append(relinked)
                        if verbose:
                            print(
                                f"  [{relinked['code']}] {relinked['artist']} - "
                                f"{relinked['title']}  (linked existing {lrc_path.name})"
                            )
                skipped.append(audio_file.name)
                continue

            artist, title = split_artist_title(audio_file.stem)
            lrc_path, text_path = find_lyrics(audio_file.stem, lyrics_dir)

            # A hand-written override wins over whatever the filename implied.
            override = overrides.get(audio_file.name.lower(), {})
            artist = override.get("artist", artist)
            title = override.get("title", title)
            language = override.get("language")
            sync_locked = bool(override.get("sync_locked", False))

            record = song_model.add(
                conn,
                title=title,
                artist=artist,
                audio_path=relative_audio,
                lyrics_path=to_relative(lrc_path),
                source_text_path=to_relative(text_path),
                duration_sec=read_duration(audio_file),
                language=language,
                sync_locked=sync_locked,
            )
            added.append(record)

            if lrc_path is None:
                missing_lyrics.append(record)

            if verbose:
                # Not "needs manual sync" yet: LRCLIB has not been asked.
                state = "synced" if lrc_path else "no timing yet"
                print(f"  [{record['code']}] {artist} - {title}  ({state})")

    return {
        "added": added,
        "skipped": skipped,
        "missing_lyrics": missing_lyrics,
        "relinked_lyrics": relinked_lyrics,
    }
