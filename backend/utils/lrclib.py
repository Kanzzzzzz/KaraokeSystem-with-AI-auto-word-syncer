"""
Fetching synced lyrics from LRCLIB (https://lrclib.net), a free, open lyrics
database with no API key.

The main entry point is fetch_for_song(), used by scripts/import_songs.py when
songs are added and by the sync editor's "Fetch from LRCLIB" button.

-------------------------------------------------------------------------------
HOW A MATCH IS FOUND
-------------------------------------------------------------------------------

The same song often exists on LRCLIB several times, in different versions:
"Totoong Tayo" has entries at 180s, 227s, 228s and 275s. The timestamps in an
LRC file only line up with the version they were made for, so choosing the
right one matters as much as finding the song at all.

That is why our own track's DURATION (read from the audio file on import) is
used throughout:

  1. /api/get with title, artist and duration. LRCLIB treats duration as a
     hard requirement here, so a hit is the right version by definition.
  2. /api/search by title and artist, choosing the result whose duration is
     closest to ours - but only within DURATION_TOLERANCE. A version more
     than a few seconds longer or shorter is a different edit, and its
     timestamps would drift out of step with our audio.
  3. The same search as free text ("artist title"), which copes with small
     spelling differences such as "feat." credits.

Only if no version has usable timing do we settle for plain, untimed words.

-------------------------------------------------------------------------------
WHAT COUNTS AS A RESULT
-------------------------------------------------------------------------------

  synced          timing saved as an .lrc - the song is ready to sing
  plain_only      LRCLIB had the words but no timing that fits our audio;
                  the words are saved as .txt for the sync editor
  not_found       nothing usable - the song needs manual sync
  already_synced  the song had timing already and replacing it was not asked
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from utils import lyrics_store
from utils.lrc import build_lrc, parse_lrc

BASE_URL = "https://lrclib.net/api"

# LRCLIB asks clients to identify themselves in the User-Agent header, so the
# people running it can see who is using the service.
USER_AGENT = "KaraokeSystem-SchoolProject/1.0 (educational, non-commercial)"

# Most answers arrive in well under a second, but the network is sometimes
# slow. Long enough for a slow answer, short enough that a dead connection
# does not hang the import script or the sync editor.
TIMEOUT_SECONDS = 12

# Two attempts per request, one second apart - see _get() for why.
ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 1.0

# How far LRCLIB's duration may be from ours for its timestamps to be trusted.
# Encoders round durations differently, so the same recording can differ by a
# second or two; a larger gap means a different edit of the song.
DURATION_TOLERANCE = 3.0


class LrclibError(Exception):
    """LRCLIB could not be reached, or answered with something unusable."""


# ==========================================================================
# Talking to the API
# ==========================================================================

def _get(path: str, params: dict):
    """
    Call one LRCLIB endpoint and return its decoded JSON.

    A 404 is a normal answer ("no such track") and returns None. Anything that
    means the service itself could not be used raises LrclibError, so callers
    can tell "LRCLIB has no lyrics" apart from "LRCLIB is unreachable".

    Errors that are usually temporary - a dropped connection, a timeout, the
    server being busy - get ONE retry after a short pause. In testing, the
    occasional request to LRCLIB failed out of the blue and worked a second
    later. Without a retry, one such blip would stop the import script looking
    up every song after it.
    """
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )

    for attempt in range(1, ATTEMPTS + 1):
        last_try = attempt == ATTEMPTS

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            # 429 (too many requests) and 5xx (server trouble) can pass on
            # their own. Anything else - a 400, say - would fail the same
            # way again, so there is no point retrying it.
            if (error.code == 429 or error.code >= 500) and not last_try:
                time.sleep(RETRY_PAUSE_SECONDS)
                continue
            raise LrclibError(f"LRCLIB answered with HTTP {error.code}.") from error

        except (urllib.error.URLError, TimeoutError) as error:
            if not last_try:
                time.sleep(RETRY_PAUSE_SECONDS)
                continue
            raise LrclibError("Could not reach LRCLIB. Check the internet connection.") from error

        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise LrclibError("LRCLIB sent back something that was not valid JSON.") from error


# ==========================================================================
# Choosing the right version
# ==========================================================================

def _usable(record) -> bool:
    """A real track with lyrics. Instrumental entries have no words at all."""
    return isinstance(record, dict) and not record.get("instrumental")


def _duration_gap(record: dict, duration: float | None) -> float:
    """Seconds between LRCLIB's duration and ours (0 when either is unknown)."""
    if duration is None or record.get("duration") is None:
        return 0.0
    return abs(float(record["duration"]) - float(duration))


def choose_best(records: list, duration: float | None):
    """
    Pick the best result from a search.

    Returns (synced, plain):
      synced  the timed version closest to our duration, within tolerance
      plain   the version closest to our duration that has plain words,
              kept as a fallback if nothing timed fits

    min() returns the first of several equally close results, which keeps
    LRCLIB's own ranking as the tie-breaker.
    """
    candidates = [r for r in records or [] if _usable(r)]

    timed = [
        r for r in candidates
        if r.get("syncedLyrics") and _duration_gap(r, duration) <= DURATION_TOLERANCE
    ]
    worded = [r for r in candidates if r.get("plainLyrics")]

    synced = min(timed, key=lambda r: _duration_gap(r, duration), default=None)
    plain = min(worded, key=lambda r: _duration_gap(r, duration), default=None)
    return synced, plain


def find_lyrics(title: str, artist: str, duration: float | None = None) -> dict:
    """
    Search LRCLIB for one song, following the order described at the top.

    Returns {"synced": record | None, "plain": record | None, "matched_by": str}.
    Raises LrclibError if LRCLIB cannot be reached.
    """
    known_artist = artist and artist != "Unknown Artist"
    plain_fallback = None

    # 1. Exact lookup. Only possible when we know the duration, which is what
    #    makes LRCLIB return the right version.
    if duration and known_artist:
        exact = _get("/get", {
            "track_name": title,
            "artist_name": artist,
            "duration": round(duration),
        })
        if _usable(exact):
            if exact.get("syncedLyrics"):
                return {"synced": exact, "plain": exact, "matched_by": "exact match"}
            plain_fallback = exact

    # 2. Search by title and artist.
    if known_artist:
        results = _get("/search", {"track_name": title, "artist_name": artist})
        synced, plain = choose_best(results, duration)
        if synced:
            return {"synced": synced, "plain": synced, "matched_by": "search"}
        plain_fallback = plain_fallback or plain

    # 3. Free-text search, for spelling differences between our file names and
    #    LRCLIB's entries. With no known artist, the title is all we have.
    query = f"{artist} {title}" if known_artist else title
    results = _get("/search", {"q": query})
    synced, plain = choose_best(results, duration)
    if synced:
        return {"synced": synced, "plain": synced, "matched_by": "text search"}

    return {"synced": None, "plain": plain_fallback or plain, "matched_by": None}


# ==========================================================================
# Saving the result for a song
# ==========================================================================

def _describe(record: dict, song: dict, matched_by: str | None) -> dict:
    """What was matched, so the user can check it is the right song."""
    return {
        "id": record.get("id"),
        "artist": record.get("artistName"),
        "title": record.get("trackName"),
        "album": record.get("albumName"),
        "duration": record.get("duration"),
        "duration_gap": round(_duration_gap(record, song.get("duration_sec")), 2),
        "matched_by": matched_by,
    }


def fetch_for_song(conn, song: dict, force: bool = False) -> dict:
    """
    Look a library song up on LRCLIB and save whatever fits.

    Returns {"status", "song", ...} - see the top of this file for the
    statuses. With force=False a song that already has timing is left alone:
    timing you tapped against your own audio may well be better than a
    download. With force=True it is replaced, and the old file is kept as a
    .bak so the replacement can be undone.

    Raises LrclibError if LRCLIB cannot be reached; the song is not changed.
    """
    if song["is_synced"] and not force:
        return {"status": "already_synced", "song": song}

    found = find_lyrics(song["title"], song["artist"], song.get("duration_sec"))

    record = found["synced"]
    if record:
        lines = parse_lrc(record["syncedLyrics"])["lines"]

        if any(line["text"].strip() for line in lines):
            lrc_text = build_lrc(
                lines,
                title=song["title"],
                artist=song["artist"],
                source=f"LRCLIB (id {record.get('id')})",
            )
            saved = lyrics_store.save_synced_lrc(conn, song, lrc_text)
            return {
                "status": "synced",
                "song": saved["song"],
                "path": saved["path"],
                "backup": saved["backup"],
                "line_count": len(lines),
                "matched": _describe(record, song, found["matched_by"]),
            }

    # No timing that fits. The words alone are still worth having - they are
    # exactly what the sync editor needs to time the song by hand - but only
    # when the song has no lyric sheet of its own. A sheet you supplied is
    # never overwritten by a download.
    record = found["plain"]
    if record and record.get("plainLyrics") and not song.get("source_text_path"):
        saved = lyrics_store.save_plain_text(conn, song, record["plainLyrics"])
        return {
            "status": "plain_only",
            "song": saved["song"],
            "path": saved["path"],
            "matched": _describe(record, song, "plain words only"),
        }

    return {"status": "not_found", "song": song}
