"""
Vocal isolation with Demucs, with a cache so it never runs twice on one file.

Demucs (Meta's music source separation model) splits a song into four stems:
drums, bass, other instruments, and vocals. We keep two:

  vocals         what the aligner listens to
  accompaniment  drums + bass + other: a MINUS-ONE version of the song. Saved
                 twice - at full quality as instrumental.mp3, which becomes
                 the karaoke's playback track when you supply only the
                 original, and at 16 kHz for offset.py to compare against

Separation is by far the slowest step (minutes on a laptop CPU), so the
stems are cached in data/library/autosync_cache/stems/, in a folder named
after a fingerprint of the audio file's CONTENTS. Renaming the file keeps
the cache; replacing the audio with a different recording makes a new entry.

The analysis stems are stored as 16 kHz mono WAV: the rate the aligner needs,
and about a sixth of the size of CD-quality stereo. The instrumental is kept
as 320 kbps stereo MP3, because people listen to that one.
"""

import hashlib
import json
import time
import types
from pathlib import Path

import numpy as np

from . import audio as audio_io

# htdemucs is Demucs v4's standard model. The fine-tuned "htdemucs_ft" is
# slightly cleaner but runs four models instead of one - four times slower on
# a CPU, for a difference the aligner barely notices.
MODEL_NAME = "htdemucs"


def fingerprint(path: Path) -> str:
    """SHA-1 of the file's bytes: identical audio -> identical cache folder."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def cache_folder(path: Path, cache_dir: Path) -> Path:
    return cache_dir / "stems" / f"{fingerprint(path)}-{MODEL_NAME}"


def _progress_tqdm(report):
    """
    A stand-in for the tqdm module that Demucs uses to draw its progress bar.

    Demucs processes the song in short segments and wraps that loop in
    tqdm.tqdm(...). Handing it this object instead means each finished segment
    calls `report(fraction_done)`, which becomes the percentage in the sync
    editor's progress bar.
    """
    def tqdm(iterable, **_kwargs):
        items = list(iterable)
        for index, item in enumerate(items):
            yield item
            report((index + 1) / max(1, len(items)))

    return types.SimpleNamespace(tqdm=tqdm)


def separate(path: Path, cache_dir: Path, report=lambda fraction: None) -> dict:
    """
    Split `path` into vocals and accompaniment.

    Returns {"vocals": Path, "accompaniment": Path, "instrumental": Path,
             "cached": bool, "seconds": float, "vocal_level_db": float}

    `vocal_level_db` is how loud the vocals stem is compared with the whole
    mix (0 dB = as loud as the song, -40 dB = practically silent). A
    minus-one track gives a very low number - there is nothing to align.
    """
    folder = cache_folder(path, cache_dir)
    vocals_path = folder / "vocals.wav"
    accompaniment_path = folder / "accompaniment.wav"
    instrumental_path = folder / "instrumental.mp3"
    info_path = folder / "info.json"

    if all(p.is_file() for p in (vocals_path, accompaniment_path, instrumental_path, info_path)):
        info = json.loads(info_path.read_text(encoding="utf-8"))
        report(1.0)
        return {"vocals": vocals_path, "accompaniment": accompaniment_path,
                "instrumental": instrumental_path, "cached": True, "seconds": 0.0,
                "vocal_level_db": info["vocal_level_db"]}

    import torch
    import demucs.apply
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    started = time.time()
    model = get_model(MODEL_NAME)
    model.eval()

    # Demucs wants stereo at its own sample rate (44.1 kHz).
    mix, rate = audio_io.load(path, rate=model.samplerate, mono=False)
    if mix.shape[0] == 1:
        mix = np.repeat(mix, 2, axis=0)
    mix = mix[:2]
    wav = torch.from_numpy(np.ascontiguousarray(mix))

    # The same normalisation Demucs's own command-line tool applies: the model
    # was trained on songs at a standard level, so scale to that and back.
    reference = wav.mean(0)
    mean, std = reference.mean(), reference.std() + 1e-8
    wav = (wav - mean) / std

    demucs.apply.tqdm = _progress_tqdm(report)   # see _progress_tqdm above
    with torch.inference_mode():
        # split=True processes the song in overlapping segments, which is
        # what keeps memory use within a laptop's reach.
        sources = apply_model(model, wav[None], device="cpu", split=True,
                              overlap=0.25, progress=True)[0]
    sources = sources * std + mean

    names = list(model.sources)                       # drums, bass, other, vocals
    vocals = sources[names.index("vocals")]
    accompaniment = sum(sources[i] for i, name in enumerate(names) if name != "vocals")

    # Down to what the aligner uses: mono, 16 kHz.
    def to_align_rate(stereo):
        mono = stereo.mean(0).numpy()
        return audio_io.resample(mono[None], rate, audio_io.ALIGN_RATE)[0]

    vocals_16k = to_align_rate(vocals)
    accompaniment_16k = to_align_rate(accompaniment)
    mix_16k = audio_io.resample(mix.mean(0, keepdims=True), rate, audio_io.ALIGN_RATE)[0]

    def rms(x):
        return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-10)

    vocal_level_db = 20 * np.log10(rms(vocals_16k) / rms(mix_16k))

    folder.mkdir(parents=True, exist_ok=True)
    audio_io.save_wav(vocals_path, vocals_16k, audio_io.ALIGN_RATE)
    audio_io.save_wav(accompaniment_path, accompaniment_16k, audio_io.ALIGN_RATE)
    audio_io.save_mp3(instrumental_path, accompaniment.numpy(), rate)
    elapsed = time.time() - started
    # Written last: its presence is what marks the cache entry as complete.
    info_path.write_text(json.dumps({
        "source_file": path.name,
        "model": MODEL_NAME,
        "vocal_level_db": round(vocal_level_db, 2),
        "separation_seconds": round(elapsed, 1),
    }, indent=2), encoding="utf-8")

    return {"vocals": vocals_path, "accompaniment": accompaniment_path,
            "instrumental": instrumental_path, "cached": False, "seconds": elapsed,
            "vocal_level_db": vocal_level_db}
