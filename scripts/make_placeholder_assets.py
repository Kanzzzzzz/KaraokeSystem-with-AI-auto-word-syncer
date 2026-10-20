"""
Generate the karaoke screen's placeholder media from maths alone.

    python scripts/make_placeholder_assets.py            # both files
    python scripts/make_placeholder_assets.py --chime    # just the chime
    python scripts/make_placeholder_assets.py --video    # just the video

Writes:
    frontend/assets/sounds/score-chime.wav          the score-reveal "ding"
    frontend/assets/background/videoke-loop.mp4     the background mood loop

Like the demo song (make_sample_song.py), nothing here was recorded or
downloaded, so the files carry no copyright and are safe to commit and share.
They are placeholders: swap in your own by changing the URLs in
frontend/js/videoke/assets.js (see frontend/assets/README.md).

DEPENDENCIES
The chime needs only the standard library. The video needs numpy and
imageio-ffmpeg (which bundles an ffmpeg binary, so nothing has to be
installed system-wide):

    pip install numpy imageio-ffmpeg

Neither is in requirements.txt - the app itself never needs them, only this
one-off generator does.
"""

import argparse
import math
import struct
import subprocess
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT_ROOT / "frontend" / "assets"
CHIME_PATH = ASSETS / "sounds" / "score-chime.wav"
VIDEO_PATH = ASSETS / "background" / "videoke-loop.mp4"


# ---------------------------------------------------------------------------
# The chime: a quick rising C-major arpeggio of bell tones
# ---------------------------------------------------------------------------

CHIME_RATE = 32000
CHIME_NOTES = [1046.50, 1318.51, 1567.98, 2093.00]    # C6 E6 G6 C7
CHIME_STAGGER = 0.085                                  # seconds between notes
CHIME_LENGTH = 1.7

# Bell partials: (ratio to the note, loudness, how fast it dies away). The
# non-whole-number 2.76 is what makes it ring like a bell rather than beep.
BELL_PARTIALS = [(1.0, 1.0, 3.2), (2.0, 0.35, 5.0), (2.76, 0.22, 7.0), (5.4, 0.08, 11.0)]


def make_chime(path: Path) -> None:
    samples = [0.0] * int(CHIME_RATE * CHIME_LENGTH)

    for n, freq in enumerate(CHIME_NOTES):
        start = int(n * CHIME_STAGGER * CHIME_RATE)
        last = n == len(CHIME_NOTES) - 1
        # The top note rings longer and a little louder - the "ta-da".
        boost, slow = (1.25, 0.6) if last else (1.0, 1.0)

        for i in range(start, len(samples)):
            t = (i - start) / CHIME_RATE
            attack = min(1.0, t / 0.004)      # a 4ms fade-in avoids a click
            value = sum(level * math.exp(-decay * slow * t) * math.sin(2 * math.pi * freq * ratio * t)
                        for ratio, level, decay in BELL_PARTIALS)
            samples[i] += boost * attack * value

    # Fade the tail to exact silence, then scale the peak to -3 dB.
    fade = int(0.15 * CHIME_RATE)
    for k in range(fade):
        samples[-1 - k] *= k / fade
    gain = (10 ** (-3 / 20)) / max(abs(s) for s in samples)

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)                   # 16-bit
        out.setframerate(CHIME_RATE)
        out.writeframes(b"".join(struct.pack("<h", int(s * gain * 32767)) for s in samples))


# ---------------------------------------------------------------------------
# The video: a slowly shifting "plasma" in videoke colours
# ---------------------------------------------------------------------------

# Rendered small and scaled up: it sits behind a dark scrim and is blurred,
# so detail would be wasted - and a small file decodes easily on any laptop.
VIDEO_W, VIDEO_H, VIDEO_SECONDS, VIDEO_FPS = 320, 180, 24, 24

# Colours placed along the plasma's value range (-1 to 1). Navy sits at 0,
# where the summed waves spend most of their time, so the picture is mostly
# dark with bands of colour drifting through it.
PALETTE = [
    (-0.62, (20, 150, 200)),    # cyan
    (-0.30, (170, 18, 120)),    # magenta
    (0.00, (14, 16, 64)),       # deep navy
    (0.28, (90, 10, 50)),       # maroon
    (0.55, (230, 160, 30)),     # gold
]
BAND_WIDTH = 0.16


def make_video(path: Path) -> None:
    import numpy as np
    import imageio_ffmpeg

    y, x = np.mgrid[0:VIDEO_H, 0:VIDEO_W].astype(np.float32)
    r = np.hypot(x - VIDEO_W / 2, y - VIDEO_H / 2)
    centres = np.array([c for c, _ in PALETTE], dtype=np.float32)
    colours = np.array([rgb for _, rgb in PALETTE], dtype=np.float32)

    def frame(t: float):
        # Four waves. Their periods - 24, 12, 8 and 6 seconds - all divide
        # the clip length exactly, so the last frame flows straight back into
        # the first: the loop has no visible seam.
        tau = 2 * np.pi * t
        value = (np.sin(x / 41 + tau / 24) + np.sin(y / 29 - tau / 12)
                 + np.sin((x + y) / 60 + tau / 8) + np.sin(r / 35 - tau / 6)) / 4
        # Blend the palette colours by how close the value is to each one.
        weights = np.exp(-((value[..., None] - centres) / BAND_WIDTH) ** 2)
        rgb = (weights @ colours) / (weights.sum(axis=-1, keepdims=True) + 1e-4)
        return np.clip(rgb, 0, 255).astype(np.uint8)

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = subprocess.Popen(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{VIDEO_W}x{VIDEO_H}",
         "-r", str(VIDEO_FPS), "-i", "-",
         "-vf", "scale=640:360:flags=bicubic,gblur=sigma=3,format=yuv420p",
         # H.264 plays in every browser; +faststart lets it begin playing
         # before the whole file has arrived. No audio track.
         "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-profile:v", "high",
         "-movflags", "+faststart", "-an", str(path)],
        stdin=subprocess.PIPE,
    )
    for n in range(VIDEO_SECONDS * VIDEO_FPS):
        ffmpeg.stdin.write(frame(n / VIDEO_FPS).tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise SystemExit("ffmpeg failed to encode the video")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chime", action="store_true", help="only make the chime")
    parser.add_argument("--video", action="store_true", help="only make the video")
    args = parser.parse_args()
    both = not args.chime and not args.video

    if args.chime or both:
        make_chime(CHIME_PATH)
        print(f"  wrote {CHIME_PATH.relative_to(PROJECT_ROOT)}")
    if args.video or both:
        make_video(VIDEO_PATH)
        print(f"  wrote {VIDEO_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
