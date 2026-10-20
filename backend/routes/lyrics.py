"""
Getting timestamped lyrics onto disk.

    POST /api/lyrics/<song_id>/lrc

Body: {"lines": [{"time": 12.4, "text": "I want to know"}, ...],
       "words_included": true}

Writes a real .lrc file next to the song's other lyrics and flips the song's
is_synced flag so the player will use it.

A line may also carry word timings, written as Enhanced LRC:

    {"time": 12.4, "text": "I want to know",
     "words": [{"time": 12.4, "text": "I"}, {"time": 12.8, "text": "want"}, ...],
     "words_end": 14.1}

"words_included" says the sender is sending ALL the word timings it wants
kept, so none are carried over from the old file (see save_lrc).

"autosync_reviewed": true says the editor had an auto-sync result loaded and
a person has now checked it. The result's highlights are then switched off
and the song's sync_status becomes 'done' (never for a locked song).

    POST /api/lyrics/<song_id>/fetch

Body (optional): {"force": true}

Looks the song up on LRCLIB and saves whatever fits: timing if a version
matching our audio exists, otherwise just the words for manual syncing. A song
that already has timing is left alone unless "force" is sent.

    POST /api/lyrics/<song_id>/text

Body: {"text": "first line\\nsecond line\\n..."}

Saves lyrics pasted by hand as the song's plain lyric sheet, so the sync
editor can time them. The fallback when LRCLIB has nothing.
"""

import re

from flask import Blueprint, jsonify, request

from config import DATA_DIR
from database import get_db
from models import song as song_model
from utils import lrclib, lyrics_store
from utils.lrc import build_lrc, parse_lrc

lyrics_bp = Blueprint("lyrics", __name__, url_prefix="/api/lyrics")


def _match_key(text: str) -> str:
    """Normalise a lyric line so small formatting differences still match."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def carry_over_word_timings(timed_lines: list[dict], existing_lrc: str) -> int:
    """
    Keep measured word timings alive across a sync-editor save.

    The sync editor only ever deals in whole lines, so the lines it posts back
    carry no word data. Writing those straight out would silently destroy the
    word-level timings of an Enhanced LRC file - the singer would go
    from a real per-word wipe back to an estimated one, with no warning and no
    way to undo it.

    So before writing, any line that still matches one in the existing file
    reclaims that line's word timings. If the line was nudged in the editor,
    the words are shifted by exactly the same amount, which keeps their spacing
    within the line intact - nudging a line moves its words with it.

    Lines whose text was edited find no match and simply lose their word data,
    which is correct: those words are no longer the words that were measured.

    Returns how many lines kept their word timings, for the response.
    """
    try:
        existing = parse_lrc(existing_lrc)["lines"]
    except Exception:
        # A corrupt or unreadable old file must never block a save.
        return 0

    # Only lines that actually have word data are worth matching against.
    pool: dict[str, list[dict]] = {}
    for line in existing:
        if line.get("words"):
            pool.setdefault(_match_key(line["text"]), []).append(line)

    if not pool:
        return 0

    carried = 0
    for line in timed_lines:
        candidates = pool.get(_match_key(line["text"]))
        if not candidates:
            continue

        # A chorus appears several times with identical text, so pick the
        # occurrence nearest in time and consume it, rather than giving every
        # repeat the first one's timings.
        best = min(candidates, key=lambda c: abs(c["time"] - line["time"]))
        candidates.remove(best)

        # Shift the words by however far the line itself moved.
        delta = line["time"] - best["time"]

        line["words"] = [
            {"time": w["time"] + delta, "text": w["text"]} for w in best["words"]
        ]
        if best.get("words_end") is not None:
            line["words_end"] = best["words_end"] + delta

        carried += 1

    return carried


def _check_words(raw_words, text: str, raw_end):
    """
    Validate one line's word timings before anything touches the disk.

    Returns (words, words_end, error). Rejected, with a reason:

      - anything that is not a list of {time, text}
      - words out of time order: build_lrc() writes words sorted by time, so
        out-of-order words would come back from the file SHUFFLED - the lyric
        text itself would change
      - words that do not spell out the line: the display text of an Enhanced
        LRC line is made from its words, so a mismatch would silently change
        the lyrics
      - an end before the last word starts
    """
    if not isinstance(raw_words, list) or not raw_words:
        return None, None, "'words' must be a non-empty list."

    words = []
    for raw in raw_words:
        if not isinstance(raw, dict):
            return None, None, "Each word must be {time, text}."
        try:
            words.append({"time": float(raw["time"]), "text": str(raw["text"]).strip()})
        except (KeyError, TypeError, ValueError):
            return None, None, f"Invalid word timing: {raw!r}"

    for before, after in zip(words, words[1:]):
        if after["time"] < before["time"]:
            return None, None, (
                f'"{after["text"]}" is timed before "{before["text"]}" in "{text}".'
            )

    spelled = " ".join(w["text"] for w in words)
    if spelled.split() != text.split():
        return None, None, f'The words do not match the line "{text}".'

    words_end = None
    if raw_end is not None:
        try:
            words_end = float(raw_end)
        except (TypeError, ValueError):
            return None, None, f"Invalid words_end in \"{text}\"."
        if words_end < words[-1]["time"]:
            return None, None, f'The end of "{text}" comes before its last word.'

    return words, words_end, None


@lyrics_bp.route("/<int:song_id>/lrc", methods=["POST"])
def save_lrc(song_id: int):
    conn = get_db()
    song = song_model.get_by_id(conn, song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404

    payload = request.get_json(silent=True) or {}
    lines = payload.get("lines")

    if not isinstance(lines, list) or not lines:
        return jsonify({"error": "Expected a non-empty 'lines' array"}), 400

    # Keep only lines that were actually stamped, and coerce the values now so
    # a malformed entry fails here with a clear message rather than writing a
    # corrupt file.
    timed_lines = []
    for line in lines:
        if not isinstance(line, dict) or line.get("time") is None:
            continue
        try:
            entry = {
                "time": float(line["time"]),
                "text": str(line.get("text", "")),
            }
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid timestamp in line: {line!r}"}), 400

        if line.get("words") is not None:
            words, words_end, error = _check_words(
                line["words"], entry["text"], line.get("words_end")
            )
            if error:
                return jsonify({"error": error}), 400

            entry["words"] = words
            if words_end is not None:
                entry["words_end"] = words_end
            # A line starts when its first word is sung. Enforced here rather
            # than trusted from the sender, because the player activates a
            # line at its own time - a line starting later than its first
            # word would skip that word's wipe.
            entry["time"] = words[0]["time"]

        timed_lines.append(entry)

    if not timed_lines:
        return jsonify({"error": "No lines had timestamps"}), 400

    # Line-only saves (anything without "words_included") re-attach the word
    # timings the previous file had, so saving never silently downgrades a
    # word-timed song back to estimated timing. The sync editor sends every
    # word timing it holds and sets the flag, so it is trusted as complete -
    # otherwise a line whose words you deliberately cleared would get its old
    # timings back.
    if not payload.get("words_included") and song["lyrics_path"]:
        existing_path = DATA_DIR / song["lyrics_path"]
        if existing_path.is_file():
            carry_over_word_timings(
                timed_lines, existing_path.read_text(encoding="utf-8", errors="replace")
            )

    lrc_text = build_lrc(timed_lines, title=song["title"], artist=song["artist"])

    # The shared writer decides where the file goes and keeps the previous
    # version as a .bak - see utils/lyrics_store.py.
    saved = lyrics_store.save_synced_lrc(conn, song, lrc_text)

    reviewed = False
    if payload.get("autosync_reviewed") and lyrics_store.mark_autosync_reviewed(conn, song):
        song_model.set_sync_status(conn, song_id, "done", "Checked and saved in the sync editor.")
        saved["song"] = song_model.get_by_id(conn, song_id)
        reviewed = True

    return jsonify({
        "autosync_reviewed": reviewed,
        "ok": True,
        "song": saved["song"],
        "lyrics_path": saved["path"],
        "backup": saved["backup"],
        "line_count": len(timed_lines),
        "word_timed_lines": sum(1 for line in timed_lines if line.get("words")),
    })


@lyrics_bp.route("/<int:song_id>/fetch", methods=["POST"])
def fetch_from_lrclib(song_id: int):
    conn = get_db()
    song = song_model.get_by_id(conn, song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404

    payload = request.get_json(silent=True) or {}

    try:
        result = lrclib.fetch_for_song(conn, song, force=bool(payload.get("force")))
    except lrclib.LrclibError as error:
        # 502 Bad Gateway: the problem is the service we depend on, not this
        # request - which is what the status code means.
        return jsonify({"error": str(error)}), 502

    # "not_found" is still a 200: the lookup worked, it just found nothing.
    # The page reads `status` to decide what to tell the user.
    return jsonify({"ok": True, **result})


@lyrics_bp.route("/<int:song_id>/text", methods=["POST"])
def save_pasted_text(song_id: int):
    conn = get_db()
    song = song_model.get_by_id(conn, song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", ""))

    if not text.strip():
        return jsonify({"error": "Paste some lyrics first."}), 400

    # A generous cap - the longest songs run to a few thousand characters -
    # that stops an accidental paste of a whole web page filling the disk.
    if len(text) > 20_000:
        return jsonify({"error": "That is too long to be one song's lyrics."}), 400

    saved = lyrics_store.save_plain_text(conn, song, text)
    return jsonify({"ok": True, "song": saved["song"], "lyrics_path": saved["path"]})
