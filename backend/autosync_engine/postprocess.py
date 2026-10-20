"""
Cleaning up raw alignment results, and deciding how far to trust each word.

The aligner was trained on SPEECH, and singing differs in ways that show up
in its output:

  - Held notes. CTC models mark a letter's START sharply, then fall back to
    "blank" while a vowel is held, so a sung word's END comes out too early.
    We extend each word for as long as the vocals stay loud, up to the next
    word.
  - Legato. Sung words often run into each other with no real gap. A gap
    shorter than JOIN_GAP is closed, so the wipe does not flicker off
    between words.
  - Outliers. A word the model barely recognised (a low score) AND that sits
    oddly far from its neighbours - a big jump in the middle of a phrase - is
    re-placed between them, sharing the time by number of letters, and
    flagged. A low score ALONE is not enough: short words like "I" or "and"
    score low even when placed well, and the aligner's placement of them is
    still better than a guess. They are only flagged as "weak".

  - Lateness. This kind of model notices a new letter slightly AFTER it is
    sung, most of all when words run into each other. So every start is
    moved START_LEAD seconds earlier. Measured on a test song against hand
    taps AND, separately, against Whisper's own word times, the raw starts
    were a median 0.14-0.25 s late; after the correction the median error
    against the hand taps fell from 0.19 s to 0.12 s. (The sync editor makes
    the same kind of correction, 0.15 s, for human reaction time.)

Then each word is checked against its line's time window from LRCLIB.

Every word leaves here with a list of FLAGS saying what happened to it:

  no_letters    nothing to align ("-", "2") - given its neighbour's time
  second_opinion  timed by Whisper, for a line the alignment model could not
                hear (see pipeline._rescue_unheard). Not a low-confidence flag
                by itself: on test songs these lines went from 0.5 s late to
                about as accurate as the rest.
  weak          the model barely recognised it (score below WEAK_SCORE)
  reestimated   weak AND far out of step with its neighbours, so re-placed
  unaligned     the whole line could not be aligned; spread evenly instead
  outside_line  starts well outside the line's LRCLIB time window
  order_fixed   had to be moved to keep the words in singing order

A word is HIGH confidence when its score is at least HIGH_SCORE and it has
none of these flags. The sync editor highlights the rest for checking.
"""

import numpy as np

from .audio import FRAME_SECONDS

MIN_WORD_SECONDS = 0.08     # shortest a word may be
JOIN_GAP = 0.35             # gaps shorter than this are closed
WEAK_SCORE = 0.05           # below this the model barely heard the word
OUTLIER_GAP = 1.5           # a weak word this far (s) from both neighbours' trend is re-placed
WINDOW_TOLERANCE = 0.3      # how far outside its LRCLIB window a word may start unflagged
VOICED_BELOW_PEAK_DB = 25   # "still singing" = within 25 dB of the song's loud level
# Score needed for high confidence. Calibrated on 615 words of three songs
# against Whisper's word times: words scoring 0.05-0.30 were as accurate as
# those above (median error 0.08-0.10 s), while words below 0.05 were a
# median 0.50 s off. A higher cutoff only highlighted good words - at 0.30,
# a third of every song - and buried the few that really need checking.
HIGH_SCORE = 0.05
START_LEAD = 0.15           # every word start is moved this much earlier (see above)

# Every flag here makes a word low confidence.
LOW_CONFIDENCE_FLAGS = {"no_letters", "weak", "reestimated", "unaligned", "outside_line",
                        "order_fixed"}


def voiced_threshold(loudness: np.ndarray) -> float:
    """
    The level above which the vocal stem counts as singing.

    Relative to the song's own loud level (its 95th percentile), so it works
    the same for a quiet ballad and a loud rock song.
    """
    if len(loudness) == 0:
        return 0.0
    return float(np.percentile(loudness, 95)) - VOICED_BELOW_PEAK_DB


def _extend_while_voiced(end: float, limit: float, loudness: np.ndarray, threshold: float) -> float:
    """Move `end` forward while the vocals stay loud, but never past `limit`."""
    frame = int(end / FRAME_SECONDS)
    last = min(len(loudness), int(limit / FRAME_SECONDS))
    while frame < last and loudness[frame] > threshold:
        frame += 1
    return max(end, min(limit, frame * FRAME_SECONDS))


def _voiced_span(start: float, stop: float, loudness: np.ndarray,
                 threshold: float) -> tuple[float, float] | None:
    """
    From the first to the last moment the vocals are sounding between
    `start` and `stop`, or None if they never are (or only for a blip).
    """
    first = max(0, int(start / FRAME_SECONDS))
    last = min(len(loudness), int(stop / FRAME_SECONDS))
    if last <= first:
        return None
    voiced = np.flatnonzero(loudness[first:last] > threshold)
    if len(voiced) < 10:            # under 0.2 s of singing: not trustworthy
        return None
    return ((first + voiced[0]) * FRAME_SECONDS, (first + voiced[-1] + 1) * FRAME_SECONDS)


def _spread(words: list[dict], indexes: list[int], start: float, stop: float) -> None:
    """
    Share the time between `start` and `stop` among the given words, in
    proportion to their number of letters - the way the player estimates a
    line with no word timing. Used for words that could not be placed.
    """
    weights = [max(1, sum(ch.isalpha() for ch in words[i]["text"])) for i in indexes]
    total = sum(weights)
    stop = max(stop, start)
    position = start
    for i, weight in zip(indexes, weights):
        words[i]["start"] = position
        position += (stop - start) * weight / total


def clean_line(
    tokens: list[str],
    aligned: list[dict | None] | None,
    anchor: float | None,
    next_anchor: float | None,
    limit: float,
    loudness: np.ndarray,
    threshold: float,
    lead: float = START_LEAD,
    missing_flag: str = "no_letters",
    placed_flag: str | None = None,
) -> list[dict]:
    """
    Turn one line's raw alignment into clean, flagged word timings.

    tokens       the line's words, exactly as written
    aligned      the aligner's result per word (None = no letters), or None
                 if the whole line failed
    anchor       the line's start time from LRCLIB (None when unanchored)
    next_anchor  the next line's start time from LRCLIB (None when unanchored)
    limit        no word of this line may run past this time
    lead         how much earlier to move each placed start (START_LEAD is
                 for the alignment model; Whisper's times need none)
    missing_flag the flag for a word `aligned` has no time for
    placed_flag  a flag added to every word that WAS placed (e.g. who placed it)
    """
    words = [{"text": token, "start": None, "end": None, "score": 0.0, "flags": []}
             for token in tokens]

    if aligned is None:
        # Nothing could place this line's words. Wipe it at an even pace - but
        # only across the part of its slot where singing is actually heard,
        # not the whole slot up to the next line. Spreading over the whole
        # slot is what made such lines lag behind the singing (0.5 s on test
        # songs): the singing usually ends well before the next line starts.
        start = anchor if anchor is not None else 0.0
        stop = limit
        voiced = _voiced_span(start - 0.5, limit, loudness, threshold)
        if voiced is not None:
            start, stop = voiced
        _spread(words, list(range(len(words))), start, stop)
        for word in words:
            word["flags"].append("unaligned")
    else:
        for word, result in zip(words, aligned):
            if result is None:
                word["flags"].append(missing_flag)
            else:
                word.update(start=max(0.0, result["start"] - lead),
                            end=result["end"], score=result["score"])
                if placed_flag:
                    word["flags"].append(placed_flag)
                if result["score"] < WEAK_SCORE:
                    word["flags"].append("weak")
        _drop_outliers(words)

    _fill_missing(words, anchor, limit)
    _keep_in_order(words)
    _set_ends(words, limit, loudness, threshold)

    if anchor is not None and next_anchor is not None:
        for word in words:
            if (word["start"] < anchor - WINDOW_TOLERANCE
                    or word["start"] > next_anchor + WINDOW_TOLERANCE):
                word["flags"].append("outside_line")

    for word in words:
        word["confidence"] = confidence_of(word)
    return words


def _drop_outliers(words: list[dict]) -> None:
    """
    Un-time a weak word that sits far from where its neighbours put it.

    "Where its neighbours put it" is the midpoint between the previous and
    the next timed word. A weak word more than OUTLIER_GAP seconds from that
    midpoint, while its neighbours are close together, is an obvious
    outlier: _fill_missing() then re-places it between them.
    """
    for index, word in enumerate(words):
        if "weak" not in word["flags"] or word["start"] is None:
            continue
        before = next((w for w in reversed(words[:index]) if w["start"] is not None), None)
        after = next((w for w in words[index + 1:] if w["start"] is not None), None)
        if before is None or after is None:
            continue
        expected = (before["start"] + after["start"]) / 2
        if abs(word["start"] - expected) > OUTLIER_GAP and after["start"] - before["start"] < 2 * OUTLIER_GAP:
            word.update(start=None, end=None)
            word["flags"].append("reestimated")


def _fill_missing(words: list[dict], anchor: float | None, limit: float) -> None:
    """Give every untimed word a time between its nearest timed neighbours."""
    i = 0
    while i < len(words):
        if words[i]["start"] is not None:
            i += 1
            continue

        # A run of untimed words from i to j-1.
        j = i
        while j < len(words) and words[j]["start"] is None:
            j += 1

        before = words[i - 1]["end"] if i > 0 else None
        after = words[j]["start"] if j < len(words) else None

        if before is None:
            before = anchor if anchor is not None else (after - 0.3 * (j - i) if after else 0.0)
            if after is not None:
                before = min(before, after)
        if after is None:
            after = max(before, min(limit, before + 0.3 * (j - i)))

        _spread(words, list(range(i, j)), before, after)
        i = j


def _keep_in_order(words: list[dict]) -> None:
    """A word may never start before the one sung before it."""
    for before, word in zip(words, words[1:]):
        if word["start"] < before["start"]:
            word["start"] = before["start"]
            word["flags"].append("order_fixed")


def _set_ends(words: list[dict], limit: float, loudness: np.ndarray, threshold: float) -> None:
    """
    Decide where each word stops: held for as long as it is sung, gaps under
    JOIN_GAP closed, at least MIN_WORD_SECONDS long, never overlapping the
    next word or passing `limit`.
    """
    for index, word in enumerate(words):
        next_start = words[index + 1]["start"] if index + 1 < len(words) else limit
        end = word["end"] if word["end"] is not None else word["start"]

        end = _extend_while_voiced(max(end, word["start"]), next_start, loudness, threshold)
        if index + 1 < len(words) and next_start - end < JOIN_GAP:
            end = next_start
        end = max(end, word["start"] + MIN_WORD_SECONDS)
        word["end"] = min(end, max(next_start, word["start"]))


def confidence_of(word: dict) -> str:
    """'high' or 'low' - see the top of this file."""
    if word["score"] >= HIGH_SCORE and not LOW_CONFIDENCE_FLAGS.intersection(word["flags"]):
        return "high"
    return "low"


def fix_between_lines(lines: list[dict]) -> None:
    """
    Keep lines in order and stop each line's singing before the next begins.

    Neighbouring windows overlap slightly, so in a fast song the first word of
    a line can land before the last word of the previous one. It is moved
    just after it and flagged.
    """
    sung = [line for line in lines if line["words"]]
    for previous, line in zip(sung, sung[1:]):
        last_start = previous["words"][-1]["start"]
        first = line["words"][0]
        if first["start"] <= last_start:
            first["start"] = last_start + 0.01
            first["flags"].append("order_fixed")
            first["confidence"] = confidence_of(first)
            for before, word in zip(line["words"], line["words"][1:]):
                if word["start"] < before["start"]:
                    word["start"] = before["start"]
        # The previous line's last word must not run into this line.
        previous["words"][-1]["end"] = min(previous["words"][-1]["end"], line["words"][0]["start"])
