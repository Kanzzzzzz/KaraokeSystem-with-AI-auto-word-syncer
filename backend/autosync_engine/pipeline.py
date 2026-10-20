"""
The whole auto-sync pipeline for one song, from a job description to a result.

INPUT (the job, a dict - built by backend/utils/autosync.py):

    playback_audio   the file the karaoke plays (often a minus-one track)
    original_audio   the same song WITH vocals, or None if the playback file
                     has vocals itself
    isolate_vocals   run Demucs first (strongly recommended)
    lines            [{"text", "time"}] - the lyric lines, with LRCLIB/.lrc
                     start times in the PLAYBACK file's time, or time None
                     when only plain lyrics exist. None = no lyrics at all.
    language         'tl', 'en' or None - only used by the Whisper fallback
    cache_dir        where Demucs stems are cached

OUTPUT (the result, a dict saved as JSON):

    mode        'anchored'    lines had LRCLIB times; the whole song is aligned
                              and each line checked against LRCLIB (_align_song)
                'unanchored'  plain lyrics; the whole song aligned in one go
                'transcribed' no lyrics; Whisper wrote them, then aligned
    lines       [{"text", "time", "anchor", "words": [...], "words_end",
                  "confidence"}], every time in PLAYBACK time
    offset      the minus-track check (offset.py), or None
    summary     word counts and the suggested status: 'done' / 'needs_review'
"""

import shutil
import time
from pathlib import Path

import numpy as np

from . import audio as audio_io
from . import ctc_align, offset, postprocess, separation, text

# Each line is aligned in a window from a little before its LRCLIB time to a
# little after the next line's. LRCLIB times are themselves often off by a
# few tenths of a second, so the window must be wider than the line.
#
# The margin AFTER is kept small on purpose. On test songs LRCLIB's line
# times were rarely late (the first word came 0.06 s after the LRCLIB time,
# typically), so the next line's singing starts right at its time; a wide
# margin there only let a line grab the next line's first words - for
# example "made for you / And you weren't", where both lines have "you".
WINDOW_BEFORE = 0.6
WINDOW_AFTER = 0.25

# The last line has no "next line"; its window runs this long at most.
LAST_LINE_SECONDS = 15.0

# A line whose audio-based start is more than AGREE_SECONDS from its LRCLIB
# time "disagrees" with LRCLIB. The audio is trusted anyway when the aligner
# was sure of the line (mean letter score at least TRUST_SCORE); otherwise
# LRCLIB's time is used. On the test song, lines where LRCLIB was wrong scored
# 0.42 and 0.58, and a humming line where the aligner was lost scored 0.01.
AGREE_SECONDS = 1.0
TRUST_SCORE = 0.2

# A line the model could barely hear even inside its LRCLIB window (heavily
# processed vocals, humming) is not aligned at all: an even-paced wipe across
# its LRCLIB line is a better guess than a forced alignment to noise.
UNHEARD_SCORE = 0.05

# A vocals stem this much quieter than the whole mix is treated as silence:
# the file is a minus-one track with nothing to align.
NO_VOCALS_DB = -30.0

# The share of words that must be high confidence for a song to be 'done'.
DONE_RATIO = 0.90

# The progress bar's share of each stage, as (start %, end %).
STAGES = {
    "separating": (0, 45),
    "matching": (45, 47),
    "transcribing": (47, 55),
    "listening": (55, 80),
    "aligning": (80, 82),
    "second opinion": (82, 99),
}

# A line rescued by Whisper needs at least this share of its words heard;
# with fewer, the matches are too likely to be chance.
RESCUE_MIN_SHARE = 0.4


class PipelineError(Exception):
    """A problem the user can act on, with a message saying what to do."""


def run(job: dict, report) -> dict:
    """
    Run every stage. `report(percent, stage, message)` is called as it goes.
    """
    timings = {}
    clock = time.time()

    def stage(name: str, message: str):
        """Returns a callback that maps 0..1 within a stage onto the whole bar."""
        low, high = STAGES[name]
        report(low, name, message)
        return lambda fraction: report(low + (high - low) * min(1.0, fraction), name, message)

    def lap(name: str):
        nonlocal clock
        timings[name] = round(time.time() - clock, 1)
        clock = time.time()

    playback_path = Path(job["playback_audio"])
    original_path = Path(job["original_audio"]) if job.get("original_audio") else None
    source_path = original_path or playback_path
    cache_dir = Path(job["cache_dir"])

    # --- 1. Vocals -----------------------------------------------------------
    stems = None
    if job.get("isolate_vocals", True):
        progress = stage("separating", "Separating the vocals (Demucs)...")
        stems = separation.separate(source_path, cache_dir, progress)
        if stems["vocal_level_db"] < NO_VOCALS_DB:
            if original_path:
                raise PipelineError(
                    f"The original recording ({original_path.name}) has almost no vocals "
                    f"({stems['vocal_level_db']:.0f} dB). Check it is the version with singing.")
            raise PipelineError(
                f"This track has no vocals to align ({stems['vocal_level_db']:.0f} dB) - "
                "it looks like a minus-one track. Put the original recording, with "
                "singing, in data/library/originals/ with the same name.")
        vocals, _ = audio_io.load(stems["vocals"])
    else:
        vocals, _ = audio_io.load(source_path, rate=audio_io.ALIGN_RATE)
    lap("separating")
    duration = len(vocals) / audio_io.ALIGN_RATE

    # --- 2. Minus track vs original -------------------------------------------
    # shift = how much later everything happens in the karaoke track.
    match = None
    shift = 0.0
    if original_path:
        stage("matching", "Checking the minus track matches the original...")
        playback, _ = audio_io.load(playback_path, rate=audio_io.ALIGN_RATE)
        if stems:
            reference, _ = audio_io.load(stems["accompaniment"])
        else:
            reference = vocals   # the full original mix: a rougher comparison
        # Was the karaoke track made from this very original (by
        # make_minus_one)? Then it is byte-for-byte the cached instrumental,
        # and its shift can only be a few hundredths of a second.
        generated = bool(stems) and _same_file(playback_path, stems["instrumental"])
        match = offset.measure(playback, reference,
                               max_offset=offset.GENERATED_MAX_OFFSET if generated
                               else offset.MAX_OFFSET_SECONDS)
        match["generated_from_original"] = generated
        # A generated minus-one IS the original's audio, so the true shift is
        # zero. The few hundredths measured are the MP3 encoder's padding at
        # the start of the file, which browsers skip when they play it.
        shift = 0.0 if generated else match["seconds"]
    lap("matching")

    # --- 3. The lyric lines, in the aligned audio's own time ------------------
    if job.get("lines") is None:
        stage("transcribing", "No lyrics - transcribing them with Whisper...")
        from . import transcribe
        lines = transcribe.transcribe(vocals, job.get("language"))
        if not lines:
            raise PipelineError("Whisper could not make out any lyrics in this song.")
        mode = "transcribed"
    else:
        lines = [
            {"text": line["text"],
             "time": None if line.get("time") is None else line["time"] - shift}
            for line in job["lines"]
        ]
        anchored = any(line["time"] is not None and line["text"].strip() for line in lines)
        mode = "anchored" if anchored else "unanchored"
    lap("transcribing")

    # --- 4. The model listens -------------------------------------------------
    progress = stage("listening", "Listening for each letter (MMS model)...")
    model = ctc_align.LetterModel()
    # The letter probabilities depend only on the vocals, not on the lyrics
    # or any setting, so they are cached next to the stems. A re-run - after
    # fixing a lyric, say - then skips this 2-3 minute step entirely.
    # Stored as float16: half the size, and far more precise than needed.
    cache = stems["vocals"].parent / "mms_logprobs.npy" if stems else None
    if cache is not None and cache.is_file():
        log_probs = np.load(cache).astype(np.float32)
        progress(1.0)
    else:
        log_probs = model.log_probs(vocals, progress)
        if cache is not None:
            np.save(cache, log_probs.astype(np.float16))
    lap("listening")

    # --- 5. Align and clean up ------------------------------------------------
    progress = stage("aligning", "Placing every word...")
    loudness = audio_io.loudness_db(vocals, audio_io.ALIGN_RATE)
    threshold = postprocess.voiced_threshold(loudness)

    results = _align_song(lines, log_probs, model, duration, loudness, threshold,
                          anchored=(mode != "unanchored"))
    progress(1.0)
    lap("aligning")

    # --- 5b. A second listener for lines the model could not hear ------------
    # Only whole LINES are handed to Whisper. Re-timing single weak words
    # with it was tried too, and measured against hand taps it made those
    # words worse (0.13 s -> 0.31 s typical error), so it is not done.
    if any(line.get("source") == "LRCLIB even pace" for line in results):
        progress = stage("second opinion", "Lines the model could not hear: asking Whisper...")
        from . import transcribe
        cache = stems["vocals"].parent / "whisper_words.json" if stems else None
        heard = transcribe.word_times(vocals, job.get("language"), cache)
        _rescue_unheard(results, heard, duration, loudness, threshold)
        progress(1.0)
    lap("second opinion")

    postprocess.fix_between_lines(results)

    # --- 6. Back to the karaoke track's time, and a verdict -------------------
    for line in results:
        for word in line["words"]:
            word["start"] = round(word["start"] + shift, 3)
            word["end"] = round(word["end"] + shift, 3)
            word["score"] = round(word["score"], 3)
        if line["words"]:
            line["time"] = line["words"][0]["start"]
            last = line["words"][-1]
            line["words_end"] = round(max(last["end"], last["start"] + 0.05), 3)
            line["confidence"] = (
                "high" if all(w["confidence"] == "high" for w in line["words"]) else "low")
        elif line["time"] is not None:
            line["time"] = round(line["time"] + shift, 3)   # a blank break marker
        if line.get("anchor") is not None:
            line["anchor"] = round(line["anchor"] + shift, 3)

    report(100, "finished", "Done.")
    return {
        "mode": mode,
        "lines": results,
        "offset": match,
        "vocal_level_db": None if stems is None else round(stems["vocal_level_db"], 1),
        "stems_cached": None if stems is None else stems["cached"],
        "isolate_vocals": bool(job.get("isolate_vocals", True)),
        "timings": timings,
        "summary": _summarise(results, mode, match),
    }


def _same_file(a: Path, b: Path) -> bool:
    """True if two files have identical contents (sizes first: that is quick)."""
    if not a.is_file() or not b.is_file() or a.stat().st_size != b.stat().st_size:
        return False
    return separation.fingerprint(a) == separation.fingerprint(b)


def make_minus_one(job: dict, report) -> dict:
    """
    Make a karaoke playback track (minus-one) from an original recording.

    Job: {"original_audio", "output_path", "cache_dir"}. Demucs separates the
    original, and its accompaniment - everything except the vocals - is
    copied to `output_path` as a 320 kbps stereo MP3.

    The separation is the same one auto sync uses (and is cached), so timing
    the words of the new song afterwards does not separate it a second time.
    And because the minus-one IS the original minus its vocals, word times
    measured on the original fit it exactly.
    """
    original = Path(job["original_audio"])
    output = Path(job["output_path"])

    report(0, "separating", "Separating the vocals to make the minus-one (Demucs)...")
    stems = separation.separate(
        original, Path(job["cache_dir"]),
        lambda fraction: report(99 * fraction, "separating",
                                "Separating the vocals to make the minus-one (Demucs)..."))

    if stems["vocal_level_db"] < NO_VOCALS_DB:
        raise PipelineError(
            f"{original.name} has almost no vocals ({stems['vocal_level_db']:.0f} dB) - "
            "it may already be a minus-one track. Put it in data/library/audio/ instead.")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".partial")
    shutil.copyfile(stems["instrumental"], temp)
    temp.replace(output)

    report(100, "finished", "Minus-one ready.")
    return {"task": "make_minus", "output": str(output), "stems_cached": stems["cached"],
            "seconds": round(stems["seconds"], 1),
            "vocal_level_db": round(stems["vocal_level_db"], 1)}


def _global_alignment(lines, log_probs, model):
    """
    PASS 1: align every word of the song in one go, using only the audio.

    All lyric lines are joined into one long letter sequence, with a wildcard
    allowed between lines to absorb ad-libs and instrumental passages. The
    Viterbi search then places the whole song at once. Because it must fit
    EVERY line in order, one badly timed LRCLIB line cannot pull a word to
    the wrong place - which is exactly what happened when lines were aligned
    only inside their LRCLIB windows: on a test song, three LRCLIB lines were
    1-3 seconds off, and the words were forced to follow them.

    Returns a list parallel to `lines`: per line, the aligner's per-word
    results (see ctc_align.align_words), or None for a line with no words.
    """
    letters, owners, line_starts = [], [], set()
    for line_index, line in enumerate(lines):
        tokens = text.split_line(line["text"])
        if tokens:
            line_starts.add(len(letters))
        for token in tokens:
            letters.append(text.letters_only(token, model.alphabet))
            owners.append(line_index)

    aligned = ctc_align.align_words(log_probs, 0, letters, model, wildcard_before=line_starts)

    per_line = [None] * len(lines)
    for line_index, line in enumerate(lines):
        if text.split_line(line["text"]):
            per_line[line_index] = [
                aligned[i] if aligned else None
                for i, owner in enumerate(owners) if owner == line_index
            ]
    return per_line


def _window_alignment(index, lines, log_probs, model, duration):
    """
    PASS 2 (only where needed): align ONE line inside its LRCLIB time window,
    from a little before its LRCLIB time to a little after the next line's.
    """
    line = lines[index]
    next_time = next((later["time"] for later in lines[index + 1:]
                      if later["time"] is not None), None)
    boundary = next_time if next_time is not None else min(duration, line["time"] + LAST_LINE_SECONDS)

    window_start = max(0.0, line["time"] - WINDOW_BEFORE)
    window_end = min(duration, boundary + WINDOW_AFTER)
    first = int(window_start / audio_io.FRAME_SECONDS)
    last = int(window_end / audio_io.FRAME_SECONDS)

    letters = [text.letters_only(token, model.alphabet) for token in text.split_line(line["text"])]
    return ctc_align.align_words(log_probs[first:last], first, letters, model)


def _first_start(aligned):
    """When the first placed word of a line starts, or None."""
    return next((w["start"] for w in aligned or [] if w is not None), None)


def _mean_score(aligned):
    scores = [w["score"] for w in aligned or [] if w is not None]
    return sum(scores) / len(scores) if scores else 0.0


def _align_song(lines, log_probs, model, duration, loudness, threshold, anchored):
    """
    Place every word, combining the audio and LRCLIB:

      1. The whole song is aligned from the audio alone (_global_alignment).
      2. Each line is compared with its LRCLIB time. Most agree, within a
         second, and end before the next line's LRCLIB time. A line that does
         NOT keeps the audio's answer only if
           - the aligner was confident about it (mean score at least
             TRUST_SCORE), AND
           - it still falls between the previous and the next line's LRCLIB
             times. LRCLIB's times can be seconds off, but its ORDER of lines
             is right; a short, repeated line such as "(Best friend)" can
             otherwise match the same words sung 13 seconds later.
         Otherwise LRCLIB wins, and the line is re-aligned inside its LRCLIB
         window (_window_alignment) - or, if the model cannot hear it even
         there (mean score below UNHEARD_SCORE), wiped at an even pace across
         its LRCLIB line and flagged "unaligned".
         Words falling outside LRCLIB's window are flagged "outside_line",
         so the editor asks you to check them.

    Without LRCLIB times (anchored=False) step 2 is skipped.
    """
    raw = _global_alignment(lines, log_probs, model)
    chosen = list(raw)
    source = [None] * len(lines)

    for index, line in enumerate(lines):
        if raw[index] is None:
            continue
        source[index] = "whole song"
        if not anchored or line["time"] is None:
            continue

        start = _first_start(raw[index])
        previous_time = next((l["time"] for l in reversed(lines[:index])
                              if l["time"] is not None and l["text"].strip()), 0.0)
        next_time = next((l["time"] for l in lines[index + 1:] if l["time"] is not None), duration)

        # The line must also END in time: its last word must start before
        # the next line's LRCLIB time (give or take AGREE_SECONDS). On
        # "AMAZING", a line began exactly on time but its words were
        # stretched over 10 seconds, swallowing the next two lines - a start
        # check alone let that through.
        last = next((w["start"] for w in reversed(raw[index]) if w is not None), None)
        fits = last is not None and last <= next_time + AGREE_SECONDS
        agrees = fits and start is not None and abs(start - line["time"]) <= AGREE_SECONDS
        in_order = fits and start is not None and previous_time < start < next_time
        if agrees or (in_order and _mean_score(raw[index]) >= TRUST_SCORE):
            continue

        window = _window_alignment(index, lines, log_probs, model, duration)
        if window is not None and _mean_score(window) >= UNHEARD_SCORE:
            chosen[index], source[index] = window, "LRCLIB window"
        else:
            chosen[index], source[index] = None, "LRCLIB even pace"

    results = []
    for index, line in enumerate(lines):
        entry = {"text": line["text"], "time": line["time"], "anchor": line["time"],
                 "source": source[index], "words": [], "words_end": None, "confidence": None}
        results.append(entry)
        tokens = text.split_line(line["text"])
        if not tokens:
            continue   # a blank line marks an instrumental break; kept as is

        # No word may run past the start of the next sung line.
        later = [_first_start(chosen[j]) for j in range(index + 1, len(lines)) if chosen[j]]
        later = [t for t in later if t is not None]
        mine = _first_start(chosen[index])
        limit = later[0] if later else min(duration, (mine or 0.0) + LAST_LINE_SECONDS)

        next_anchor = None
        if anchored and line["time"] is not None:
            next_anchor = next((l["time"] for l in lines[index + 1:] if l["time"] is not None),
                               min(duration, line["time"] + LAST_LINE_SECONDS))
            if chosen[index] is None:
                # An even-paced line fills exactly its LRCLIB slot - not up to
                # the next line the model DID hear, which may be lines later.
                limit = min(limit, next_anchor)

        entry["words"] = postprocess.clean_line(
            tokens, chosen[index],
            anchor=line["time"] if anchored else None, next_anchor=next_anchor,
            limit=limit, loudness=loudness, threshold=threshold)

    return results


def _similar(a: str, b: str) -> bool:
    """
    Do two words (letters only) sound like the same word? Whisper writes what
    it hears, so "waitin'" comes out as "waiting" and "Bowery" as "barry":
    exact spelling cannot be required.
    """
    import difflib
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a))):
        return True
    return difflib.SequenceMatcher(a=a, b=b).ratio() >= 0.75


def _match_in_order(tokens: list[str], heard: list[str]) -> list[int | None]:
    """
    Pair lyric words with heard words, keeping both in order: the longest
    common subsequence, where "common" means _similar(). Returns, per lyric
    word, the index of its heard word or None.
    """
    n, m = len(tokens), len(heard)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1],
                             best[i + 1][j + 1] + 1 if _similar(tokens[i], heard[j]) else 0)
    pairs, i, j = [None] * n, 0, 0
    while i < n and j < m:
        # Prefer pairing the EARLIEST heard word, so a line does not grab the
        # same words sung again in the next line.
        if _similar(tokens[i], heard[j]) and best[i][j] == best[i + 1][j + 1] + 1:
            pairs[i] = j
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _rescue_unheard(results, heard, duration, loudness, threshold):
    """
    Time the lines the alignment model could not hear, using Whisper's words.

    For each such line (source "LRCLIB even pace"), the heard words from
    a second before its LRCLIB time to just after the next line's are
    matched to the lyric's words, in order (_match_in_order). The window
    starts no earlier than the previous line's last word, so consecutive
    identical lines - "You're gonna wanna be my best friend" sung three
    times in a row - each take their own words. Matched words get Whisper's
    times; the rest are placed between them by postprocess.clean_line.
    """
    for index, line in enumerate(results):
        if line.get("source") != "LRCLIB even pace" or line["anchor"] is None:
            continue

        tokens = text.split_line(line["text"])
        keys = [text.letters_only(t, set("abcdefghijklmnopqrstuvwxyz")) for t in tokens]

        previous_end = next((l["words"][-1]["start"] for l in reversed(results[:index]) if l["words"]), 0.0)
        next_anchor = next((l["anchor"] for l in results[index + 1:] if l["anchor"] is not None),
                           min(duration, line["anchor"] + LAST_LINE_SECONDS))
        later = next((l["words"][0]["start"] for l in results[index + 1:]
                      if l["words"] and l.get("source") != "LRCLIB even pace"), duration)

        start = max(previous_end + 0.01, line["anchor"] - 1.0)
        stop = min(next_anchor + 0.5, later)
        window = [w for w in heard if start <= w["start"] < stop]
        pairs = _match_in_order(keys, [text.letters_only(w["word"], set("abcdefghijklmnopqrstuvwxyz"))
                                       for w in window])

        if sum(p is not None for p in pairs) < max(1, RESCUE_MIN_SHARE * len(tokens)):
            continue   # Whisper could not make it out either: keep the even pace

        aligned = [None if p is None else {"start": window[p]["start"], "end": window[p]["end"],
                                           "score": window[p]["probability"]}
                   for p in pairs]
        line["words"] = postprocess.clean_line(
            tokens, aligned, anchor=line["anchor"], next_anchor=next_anchor,
            limit=min(later, duration), loudness=loudness, threshold=threshold,
            lead=0.0, missing_flag="reestimated", placed_flag="second_opinion")
        line["source"] = "Whisper"


def _summarise(lines: list[dict], mode: str, match: dict | None) -> dict:
    """Count the words and suggest a status: 'done' or 'needs_review'."""
    words = [word for line in lines for word in line["words"]]
    high = sum(word["confidence"] == "high" for word in words)
    ratio = high / len(words) if words else 0.0

    reasons = []
    if mode == "unanchored":
        reasons.append("No line timing was available, so words were placed without anchors.")
    if mode == "transcribed":
        reasons.append("The lyrics were transcribed by Whisper - check the words themselves.")
    if match and not match["ok"]:
        reasons.append(match["reason"])
    if ratio < DONE_RATIO:
        reasons.append(f"{len(words) - high} of {len(words)} words have low confidence.")

    return {
        "words": len(words),
        "high_confidence": high,
        "low_confidence": len(words) - high,
        "high_ratio": round(ratio, 3),
        "lines": sum(1 for line in lines if line["words"]),
        "low_lines": sum(1 for line in lines if line["confidence"] == "low"),
        "status": "needs_review" if reasons else "done",
        "reasons": reasons,
    }
