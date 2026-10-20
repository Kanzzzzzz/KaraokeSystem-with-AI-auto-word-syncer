"""
The fallback for a song with no lyrics at all: let Whisper write them down.

Whisper (OpenAI's open speech recognition model) is run on the isolated
vocals. Each of its segments - roughly a sung phrase - becomes one lyric line,
and its start and end become that line's time window. The words are then
timed by the same forced alignment as any other song.

The result is always marked needs_review: Whisper mishears sung words far
more often than spoken ones, so the TEXT itself needs checking, not just the
timing.

Language: 'tl' (Tagalog) or 'en' comes from the song's language setting in
song_overrides.json. For songs mixing Tagalog and English, 'tl' works better -
Whisper in Tagalog mode still writes English words it hears, while English
mode tends to force Tagalog words into English-sounding ones. With no
language set, Whisper guesses from the first 30 seconds.
"""

import json
from pathlib import Path

import numpy as np

# "small" is the largest Whisper that runs at a bearable speed on a laptop
# CPU (a few minutes per song). "medium" is noticeably better at Tagalog but
# three times slower; change it here if accuracy matters more than time.
WHISPER_MODEL = "small"

# Whisper sometimes "hears" words in pure instrumental passages. A segment
# it was unsure contained speech AND unsure of its words is dropped.
MAX_NO_SPEECH = 0.6
MIN_AVG_LOGPROB = -1.0


def transcribe(vocals: np.ndarray, language: str | None) -> list[dict]:
    """Return lyric lines as [{"text", "time", "end"}] from 16 kHz vocals."""
    import whisper

    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(
        vocals.astype(np.float32),
        language=language,
        fp16=False,                          # half precision needs a GPU
        # Without this, one misheard line leads Whisper to "continue" it and
        # invent more in the same vein - a known cause of repeated nonsense.
        condition_on_previous_text=False,
    )

    lines = []
    for segment in result["segments"]:
        text = segment["text"].strip()
        doubtful = (segment["no_speech_prob"] > MAX_NO_SPEECH
                    and segment["avg_logprob"] < MIN_AVG_LOGPROB)
        if text and not doubtful:
            lines.append({"text": text, "time": float(segment["start"]), "end": float(segment["end"])})
    return lines


def word_times(vocals: np.ndarray, language: str | None, cache: Path | None = None) -> list[dict]:
    """
    Every word Whisper hears in the vocals, with its time:
    [{"word", "start", "end", "probability"}].

    Used as a SECOND LISTENER for lines the alignment model cannot hear (see
    pipeline._rescue_unheard). Whisper is slower and less precise than the
    aligner, but it was trained on far more audio, and on test songs it
    still made out lines that the aligner could not - heavily processed
    vocals, and fast repeated choruses.

    Cached next to the stems (`cache`), so it runs at most once per song.
    """
    if cache is not None and cache.is_file():
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if stored.get("model") == WHISPER_MODEL and stored.get("language") == language:
            return stored["words"]

    import whisper

    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(vocals.astype(np.float32), language=language, fp16=False,
                              condition_on_previous_text=False, word_timestamps=True)
    words = [{"word": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"]),
              "probability": float(w["probability"])}
             for segment in result["segments"] for w in segment.get("words", [])]

    if cache is not None:
        cache.write_text(json.dumps({"model": WHISPER_MODEL, "language": language, "words": words}),
                         encoding="utf-8")
    return words
