"""
Loading audio, changing its sample rate, and measuring its loudness.

Files are decoded with soundfile (libsndfile), which reads MP3, WAV, OGG and
FLAC by itself - no ffmpeg installation needed. The one format it cannot read
is M4A (AAC); such a file has to be converted to MP3 first.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

# The alignment model and Whisper both expect 16,000 samples per second.
ALIGN_RATE = 16_000

# The alignment model produces one prediction per 320 samples at 16 kHz,
# i.e. every 20 ms. Loudness is measured on the same grid so the two line up.
FRAME_SECONDS = 0.02


class AudioLoadError(Exception):
    """The file could not be decoded."""


def load(path: Path, rate: int | None = None, mono: bool = True) -> tuple[np.ndarray, int]:
    """
    Read an audio file as float32 samples between -1 and 1.

    Returns (audio, sample_rate). `audio` has shape (samples,) when mono,
    otherwise (channels, samples). With `rate`, it is resampled to that rate.
    """
    try:
        data, file_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as error:  # libsndfile raises several different types
        hint = " M4A files need converting to MP3 first." if path.suffix.lower() == ".m4a" else ""
        raise AudioLoadError(f"Could not read {path.name}: {error}.{hint}") from error

    audio = data.T  # soundfile gives (samples, channels); we want channels first

    if mono:
        audio = audio.mean(axis=0, keepdims=True)

    if rate is not None and rate != file_rate:
        audio = resample(audio, file_rate, rate)
        file_rate = rate

    return (audio[0] if mono else audio), file_rate


def resample(audio: np.ndarray, old_rate: int, new_rate: int) -> np.ndarray:
    """
    Change the sample rate with a proper low-pass filter (julius, which comes
    with Demucs), so high frequencies do not fold back as noise.
    """
    import julius
    import torch

    tensor = torch.from_numpy(np.ascontiguousarray(audio))
    return julius.resample_frac(tensor, old_rate, new_rate).numpy()


def save_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    """
    Write 16-bit WAV, via a temporary name so that an interrupted run can
    never leave a half-written file that looks like a finished cache entry.
    """
    temp = path.with_name(path.name + ".partial")
    sf.write(str(temp), np.clip(audio, -1, 1), rate, subtype="PCM_16", format="WAV")
    temp.replace(path)


def save_mp3(path: Path, stereo: np.ndarray, rate: int, kbps: int = 320) -> None:
    """
    Write (2, samples) float audio as an MP3, for listening.

    Used for the generated minus-one track, which the karaoke plays, so it is
    kept at full quality: stereo, the original sample rate, 320 kbps. The
    encoder (lameenc) is the one Demucs itself uses - no ffmpeg needed.
    """
    import lameenc

    # Separation can push a few peaks just past full scale. Turning the whole
    # track down slightly avoids the crackle that cutting them off would cause.
    peak = float(np.abs(stereo).max()) or 1.0
    if peak > 0.99:
        stereo = stereo * (0.99 / peak)
    pcm = (stereo.T * 32767).astype(np.int16)   # interleaved L R L R ...

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(kbps)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(stereo.shape[0])
    encoder.set_quality(2)                      # 2 = best, 7 = fastest
    encoder.silence()
    data = encoder.encode(pcm.tobytes()) + encoder.flush()

    temp = path.with_name(path.name + ".partial")
    temp.write_bytes(data)
    temp.replace(path)


def loudness_db(audio: np.ndarray, rate: int) -> np.ndarray:
    """
    Loudness of every 20 ms frame, in decibels.

    RMS (root mean square) rather than the peak, so a single click does not
    count as singing. Used by postprocess.py to see how long a note is held.
    """
    hop = int(round(rate * FRAME_SECONDS))
    frames = len(audio) // hop
    if frames == 0:
        return np.zeros(0, dtype=np.float32)

    blocks = audio[: frames * hop].reshape(frames, hop)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    # 1e-10 avoids log(0) for digital silence.
    return (20 * np.log10(rms + 1e-10)).astype(np.float32)
