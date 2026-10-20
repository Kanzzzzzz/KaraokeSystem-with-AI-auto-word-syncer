"""
The automatic word-sync engine: from audio + lyrics to a time for every word.

IMPORTANT: this package needs torch, Demucs and transformers, which live in
the separate .venv-align environment. The karaoke server never imports it.
It runs only inside scripts/autosync_worker.py, which the server starts as a
separate process (see backend/utils/autosync.py for why).

-------------------------------------------------------------------------------
THE PIPELINE (pipeline.py runs these in order)
-------------------------------------------------------------------------------

  1. separation.py  Demucs splits the recording into a vocals stem and an
                    accompaniment stem. Alignment on isolated vocals is far
                    more accurate than on the full mix. Cached by file content.

  2. offset.py      When the words are timed on an ORIGINAL recording but the
                    karaoke plays a MINUS-ONE track, this measures how far
                    apart the two are in time, by matching the original's
                    accompaniment against the minus track. A poor match means
                    they are not the same recording, and the song is flagged.

  3. transcribe.py  Only when the song has no lyrics at all: Whisper writes
                    them down from the vocals, so there is something to align.

  4. ctc_align.py   The core. A speech model (MMS, trained on 1,130 languages
                    including Tagalog) says, every 20 ms, how likely each
                    letter is. A Viterbi search then finds the most likely
                    moment for every letter of the known lyrics, in order -
                    this is "forced alignment". The whole song is aligned at
                    once; pipeline.py then checks each line against LRCLIB.

  5. postprocess.py Cleans the raw result up for singing (held notes, gaps,
                    outliers, words outside their line's time window) and
                    scores each word's confidence.

Every time in this package is in SECONDS. Times are measured on the audio
the words were aligned on; pipeline.py converts them to the karaoke track's
time at the very end.
"""
