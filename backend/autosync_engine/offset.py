"""
Is the minus-one track the same recording as the original - and how far
apart in time are they?

The words are timed on the ORIGINAL (it has the singing), but the karaoke
plays the MINUS-ONE track. Those times only transfer if the two are the same
recording with the vocals removed. If the minus track has an extra second of
silence at the start, every word must move by one second; if it is a
re-recorded karaoke version at a different tempo, no single shift fits.

-------------------------------------------------------------------------------
HOW IT IS MEASURED
-------------------------------------------------------------------------------

Demucs has already split the original into vocals and ACCOMPANIMENT. The
accompaniment is, in effect, our own minus-one version of the original, so
it should line up with the real minus track beat for beat.

Comparing raw waveforms would be fragile (a slightly different mix or
encoder changes every sample), so each signal is reduced to an ONSET
ENVELOPE: 100 numbers per second saying how much new sound starts at that
moment - drum hits, chord changes. Two copies of the same arrangement have
the same onsets even when their mixes differ.

  1. Cross-correlation of the two envelopes finds the time shift that makes
     them agree best: that is the offset. The correlation at that shift
     (0 = unrelated, 1 = identical) says how well they match.
  2. The same is repeated on each quarter of the song separately. The same
     recording gives the same offset in every quarter; a different tempo
     gives offsets that drift apart as the song goes on.

A TRAP: LOOPED MUSIC. Many songs are built from a loop - a bar or two that
repeats exactly. Shift such a track by one loop and it matches itself almost
perfectly. On the test song "AMAZING" the loop is 1.91 s long, and the shift
of one loop scored 0.87 while the true shift (0.03 s) scored 0.83 - so every
word was moved 1.9 s. Two defences:
  - when several shifts score nearly as well as the best (within
    NEAR_BEST), the one closest to zero is taken: a minus-one track is far
    more likely to be almost aligned than exactly one loop out;
  - when the minus-one was MADE from this original (see pipeline.py), the
    true shift is known to be tiny, so only +-GENERATED_MAX_OFFSET is searched.
"""

import numpy as np

from .audio import ALIGN_RATE

HOP = 160           # 10 ms between envelope values at 16 kHz
FFT_SIZE = 512      # 32 ms analysis window
MAX_OFFSET_SECONDS = 20.0
GENERATED_MAX_OFFSET = 0.25   # a minus-one made from the original is off by an encoder delay at most
NEAR_BEST = 0.90              # shifts scoring at least 90% of the best count as equally good

# Thresholds for trusting the match. Chosen on test songs: the same
# recording scores well above MIN_MATCH, unrelated audio scores near zero.
MIN_MATCH = 0.35
MAX_DRIFT_SECONDS = 0.08
SEGMENTS = 4


def onset_envelope(audio: np.ndarray) -> np.ndarray:
    """
    Spectral flux: how much the spectrum GROWS from one 10 ms frame to the
    next, summed over all frequencies. Peaks where notes and beats begin.
    """
    if len(audio) < FFT_SIZE:
        return np.zeros(0, dtype=np.float32)

    windows = np.lib.stride_tricks.sliding_window_view(audio, FFT_SIZE)[::HOP]
    envelope = np.empty(len(windows), dtype=np.float32)
    hann = np.hanning(FFT_SIZE).astype(np.float32)

    previous = None
    # Done in blocks so a whole song never needs all its spectra in memory.
    for start in range(0, len(windows), 2000):
        block = windows[start:start + 2000] * hann
        # log compresses loudness, so quiet and loud passages count alike.
        spectrum = np.log1p(100 * np.abs(np.fft.rfft(block, axis=1)))
        before = np.vstack([spectrum[:1] if previous is None else previous, spectrum[:-1]])
        envelope[start:start + len(block)] = np.maximum(0, spectrum - before).sum(axis=1)
        previous = spectrum[-1:]

    # Standardise (mean 0, spread 1) so the correlation below is a true
    # correlation coefficient between -1 and 1.
    return (envelope - envelope.mean()) / (envelope.std() + 1e-8)


def _correlation_at(a: np.ndarray, b: np.ndarray, lag: int) -> float:
    """
    Correlation between a[n] and b[n - lag] over the part where both exist.
    A positive lag means `a` runs LATER than `b`.
    """
    if lag >= 0:
        x, y = a[lag:], b[: len(a) - lag]
    else:
        x, y = a[: len(a) + lag], b[-lag:]
    n = min(len(x), len(y))
    if n < 100:
        return 0.0
    return float(np.dot(x[:n], y[:n]) / n)


def best_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[int, float]:
    """
    The shift that best lines `a` up with `b`, and the correlation there.

    Uses the FFT to compute the correlation at every shift at once (the
    textbook "correlation theorem"), then divides by how many values overlap
    at each shift so short overlaps are not unfairly penalised.
    """
    size = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    raw = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)

    lags = np.arange(-max_lag, max_lag + 1)
    overlap = np.minimum(len(a), len(b) + lags) - np.maximum(0, lags)
    values = raw[lags % size] / np.maximum(overlap, 1)
    # Ignore shifts where less than half of the shorter signal overlaps.
    values[overlap < min(len(a), len(b)) // 2] = -np.inf

    # Of the shifts that score nearly as well as the best, the smallest -
    # see "A TRAP: LOOPED MUSIC" at the top of this file.
    best = float(values.max())
    good = np.flatnonzero(values >= best * NEAR_BEST)
    index = int(good[np.argmin(np.abs(lags[good]))])
    return int(lags[index]), float(values[index])


def measure(playback: np.ndarray, reference: np.ndarray,
            max_offset: float = MAX_OFFSET_SECONDS) -> dict:
    """
    How far the karaoke track (`playback`) is shifted from the original's
    accompaniment (`reference`), both 16 kHz mono.

    Returns {"seconds", "match", "segment_offsets", "drift", "ok", "reason"}.
    `seconds` is added to a time in the original to get the time in the
    karaoke track.
    """
    a = onset_envelope(playback)
    b = onset_envelope(reference)
    frames_per_second = ALIGN_RATE / HOP
    max_lag = int(max_offset * frames_per_second)

    lag, match = best_lag(a, b, max_lag)

    # Check each quarter on its own, allowing it to settle within half a
    # second of the overall offset.
    segment_offsets = []
    length = len(a) // SEGMENTS
    for k in range(SEGMENTS):
        start = k * length
        segment = np.zeros_like(a)
        segment[start:start + length] = a[start:start + length]
        search = np.arange(lag - 50, lag + 51)
        scores = np.array([_correlation_at(segment, b, candidate) for candidate in search])
        # Nearly-best shifts count as equal, and the one closest to the
        # overall shift wins - the same loop trap as above, one beat wide.
        good = np.flatnonzero(scores >= scores.max() * NEAR_BEST)
        closest = good[np.argmin(np.abs(search[good] - lag))]
        segment_offsets.append(int(search[closest]) / frames_per_second)

    drift = max(segment_offsets) - min(segment_offsets)
    ok = match >= MIN_MATCH and drift <= MAX_DRIFT_SECONDS

    if match < MIN_MATCH:
        reason = (f"The minus track matches the original poorly (match {match:.2f}, "
                  f"needs {MIN_MATCH}). It may be a different recording.")
    elif drift > MAX_DRIFT_SECONDS:
        reason = (f"The offset between the minus track and the original changes by "
                  f"{drift:.2f}s over the song - probably a different tempo or edit.")
    else:
        reason = None

    return {
        "seconds": round(lag / frames_per_second, 3),
        # Can come out a hair over 1 from rounding at the edges; 1 = identical.
        "match": round(min(match, 1.0), 3),
        "segment_offsets": [round(s, 3) for s in segment_offsets],
        "drift": round(drift, 3),
        "ok": ok,
        "reason": reason,
    }
