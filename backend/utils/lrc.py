"""
LRC lyric format helpers.

LRC is the standard timestamped-lyrics format. Each line looks like:

    [00:12.40]I want to know

meaning "show this line 12.40 seconds in". A file may also start with
metadata tags such as [ti:Title] or [ar:Artist].

This module handles three jobs:

  read_plain_lines()  - turn a plain .txt lyric file into the lines the sync
                        editor will timestamp, separating real sung lines from
                        section headers like [Chorus]
  build_lrc()         - turn timestamped lines back into .lrc file text
  parse_lrc()         - read an .lrc file back into timed lines

The frontend has its own parser (js/player/lrcParser.js) because the browser
needs to parse lyrics without a round trip to the server. This Python version
is what validates and writes files when the sync editor saves.
"""

import math
import re

# Matches one timestamp tag: [mm:ss.xx] or [mm:ss] (the centiseconds are
# optional in the LRC spec, though we always write them).
#   (\d+)      minutes
#   (\d{1,2})  seconds
#   (\d{1,3})  optional fractional part
TIMESTAMP_RE = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")

# Matches a WORD timestamp: <mm:ss.xx>, from the "Enhanced LRC" (A2) format:
#
#     [00:12.40]<00:12.40>Woah <00:12.80>I <00:13.00>want <00:13.40>
#
# Angle brackets instead of square ones, so a word tag can never be mistaken
# for the line timestamp. A bare trailing tag marks where the last word ends.
# Enhanced LRC is a strict superset of plain LRC, so ordinary files are
# unaffected by any of this.
WORD_RE = re.compile(r"<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>")

# Matches a metadata tag like [ti:Sunflower]. The key is letters only, which
# is what separates it from a timestamp (digits) or a section header (words).
METADATA_RE = re.compile(r"^\[([a-zA-Z]+):(.*)\]$")

# Matches a section header like [Chorus] or [Verse 1]. These are labels in the
# lyric sheet, not words anybody sings, so they must never get a timestamp.
SECTION_RE = re.compile(r"^\[([^\]:]+)\]$")


def format_timestamp(seconds: float) -> str:
    """
    Turn 72.45 into "[01:12.45]".

    Negative values are clamped to zero so a mis-tap in the sync editor can
    never write a malformed file.
    """
    if seconds < 0:
        seconds = 0.0

    # Work in whole hundredths of a second, rounded ONCE, up front. Rounding
    # first means a value like 59.996 simply becomes 6000 hundredths, which
    # splits cleanly into "01:00.00" - no special case needed to stop it
    # coming out as the impossible "[00:59.100]".
    #
    # floor(x + 0.5) rounds halves UP. Python's round() would instead round
    # an exact half to the nearest EVEN number (round(34.5) == 34), while the
    # browser's formatter rounds halves up. Both sides use this same
    # expression, so the sync editor's preview is character-for-character the
    # file that gets saved.
    total = math.floor(seconds * 100 + 0.5)

    minutes, rest = divmod(total, 6000)          # 6000 hundredths per minute
    whole_seconds, centiseconds = divmod(rest, 100)

    return f"[{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}]"


def format_word_timestamp(seconds: float) -> str:
    """
    Turn 72.45 into "<01:12.45>".

    Deliberately built from format_timestamp() rather than repeating the
    formatting: that function already handles the awkward case where rounding
    pushes the centiseconds to 100 and the value has to carry into the next
    second. Duplicating it would mean two places to get that wrong.
    """
    return "<" + format_timestamp(seconds)[1:-1] + ">"


def parse_word_tags(body: str) -> tuple[str, list[dict], float | None]:
    """
    Pull word timings out of a line body.

    Returns (plain_text, words, words_end). `plain_text` always has every tag
    stripped, so callers that only care about the words themselves - the sync
    editor, the scoring timeline - behave exactly as they did before.

    `words` is an empty list for an ordinary line, which is the signal to fall
    back to estimating word timings.
    """
    matches = list(WORD_RE.finditer(body))

    if not matches:
        return body.strip(), [], None

    words = []
    words_end = None

    for index, match in enumerate(matches):
        time = parse_timestamp(match.group(1), match.group(2), match.group(3))

        # The word is whatever sits between the end of this tag and the start
        # of the next one (or the end of the line, for the last tag).
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        word = body[start:stop].strip()

        if word:
            words.append({"time": time, "text": word})
        elif index == len(matches) - 1:
            # Nothing after the final tag: it marks the end of the last word
            # rather than introducing a new one.
            words_end = time

    # Replace tags with a SPACE, not an empty string - a file written without
    # spaces between words would otherwise run them together.
    plain_text = re.sub(r"\s+", " ", WORD_RE.sub(" ", body)).strip()

    return plain_text, words, words_end


def parse_timestamp(minutes: str, seconds: str, fraction: str | None) -> float:
    """Turn the three captured groups of TIMESTAMP_RE into seconds."""
    total = int(minutes) * 60 + int(seconds)

    if fraction:
        # ".4" means 4 tenths, ".40" means 40 hundredths, ".400" thousandths.
        # Dividing by 10**len handles all three without special-casing.
        total += int(fraction) / (10 ** len(fraction))

    return total


def read_plain_lines(text: str) -> list[dict]:
    """
    Prepare a plain-text lyric sheet for the sync editor.

    Returns one dict per line:
        {"text": "I want to know", "kind": "lyric"}
        {"text": "Chorus",         "kind": "section"}

    Section headers are kept (they help the person syncing keep their place)
    but marked so the editor skips over them rather than asking for a
    timestamp. Blank lines and the "Lyrics of ..." title line that these
    files often start with are dropped.
    """
    lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            continue

        # A leading "Lyrics of Song ..." header is not part of the song.
        if stripped.lower().startswith("lyrics of"):
            continue

        section_match = SECTION_RE.match(stripped)
        if section_match:
            lines.append({"text": section_match.group(1).strip(), "kind": "section"})
            continue

        # Some sheets write section names without brackets, e.g. a bare
        # "pre-chorus" line. Catch the common ones so they are not mistaken
        # for lyrics the singer has to hit.
        lowered = stripped.lower().rstrip(":")
        if lowered in {
            "intro", "verse", "pre-chorus", "prechorus", "chorus",
            "bridge", "outro", "refrain", "hook", "post-chorus",
        }:
            lines.append({"text": stripped.rstrip(":"), "kind": "section"})
            continue

        lines.append({"text": stripped, "kind": "lyric"})

    return lines


def build_lrc(
    timed_lines: list[dict],
    title: str = "",
    artist: str = "",
    source: str = "Karaoke System sync editor",
) -> str:
    """
    Build .lrc file text from the sync editor's output.

    `timed_lines` is a list of {"time": float, "text": str}. Lines with no
    time (never stamped) are skipped, and the rest are sorted by time so the
    file is always in playback order even if the editor recorded them out of
    sequence.

    A line may also carry measured word timings:

        {"time": 12.4, "text": "...",
         "words": [{"time": 12.4, "text": "Woah"}, ...],
         "words_end": 13.9}

    When present these are written as Enhanced LRC word tags, which the player
    uses instead of estimating word timings. Lines without them are written as
    ordinary LRC, so the two can be mixed freely in one file.

    `source` goes in the [re:] tag, which records where the timing came from
    ("Karaoke System sync editor", or "LRCLIB (id 1881315)"). Being able to
    see that later matters: it tells you whether a file was tapped against
    your own audio or downloaded.
    """
    out = []

    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    # Credit the tool that produced the file - standard practice in LRC files.
    out.append(f"[re:{source}]")
    out.append("")

    stampable = [ln for ln in timed_lines if ln.get("time") is not None]
    stampable.sort(key=lambda ln: ln["time"])

    for line in stampable:
        text = str(line.get("text", "")).strip()
        stamp = format_timestamp(float(line["time"]))
        words = line.get("words") or []

        if not words:
            out.append(f"{stamp}{text}")
            continue

        # Enhanced LRC: every word preceded by its own timestamp.
        parts = [
            f"{format_word_timestamp(float(w['time']))}{str(w.get('text', '')).strip()}"
            for w in sorted(words, key=lambda w: float(w["time"]))
        ]

        # A bare trailing tag tells the player where the final word stops -
        # without it the last word would have to be guessed at.
        words_end = line.get("words_end")
        if words_end is not None:
            parts.append(format_word_timestamp(float(words_end)))

        out.append(stamp + " ".join(parts))

    # Trailing newline keeps the file well-formed for text tools.
    return "\n".join(out) + "\n"


def parse_lrc(text: str) -> dict:
    """
    Read .lrc text into {"metadata": {...}, "lines": [{"time", "text"}, ...]}.

    Handles the LRC feature where one line carries several timestamps, e.g.
    a repeated chorus line written as "[00:40.00][01:20.00]Same words" - each
    timestamp produces its own entry.
    """
    metadata = {}
    lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        meta_match = METADATA_RE.match(stripped)
        if meta_match:
            metadata[meta_match.group(1).lower()] = meta_match.group(2).strip()
            continue

        stamps = TIMESTAMP_RE.findall(stripped)
        if not stamps:
            continue

        # Strip the line timestamps, then read any word timings from the rest.
        body = TIMESTAMP_RE.sub("", stripped)
        lyric_text, words, words_end = parse_word_tags(body)

        for index, (minutes, seconds, fraction) in enumerate(stamps):
            # Word timings are ABSOLUTE times, so they can only describe the
            # first place this line appears. A chorus written once but stamped
            # for several playbacks gets real word timings on the first, and
            # the repeats fall back to estimation.
            is_first = index == 0

            lines.append({
                "time": parse_timestamp(minutes, seconds, fraction),
                "text": lyric_text,
                "words": words if is_first else [],
                "words_end": words_end if is_first else None,
            })

    lines.sort(key=lambda ln: ln["time"])
    return {"metadata": metadata, "lines": lines}


def is_valid_lrc(text: str) -> bool:
    """True if the text contains at least one timestamped line."""
    return bool(parse_lrc(text)["lines"])
