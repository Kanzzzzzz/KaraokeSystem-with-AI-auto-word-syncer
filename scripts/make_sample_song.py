"""
Generates the royalty-free demo song in data/samples/.

    python scripts/make_sample_song.py

WHY THIS EXISTS

The repository cannot ship commercial music, but a project that plays nothing
when you clone it is hard to evaluate. So instead of downloading audio from
somewhere, this script SYNTHESISES a short instrumental from pure maths. It is
therefore free of any copyright by construction - nobody recorded it and
nothing was sampled.

It produces three files:
    data/samples/audio/Karaoke System - Demo Song.wav
    data/samples/lyrics/Karaoke System - Demo Song.txt   (plain lyrics)
    data/samples/lyrics/Karaoke System - Demo Song.lrc   (already timed)

The .lrc means a fresh clone can see the synced-lyrics and scoring features
working immediately, without having to use the sync editor first.

Uses only the Python standard library - no audio dependencies.
"""

import math
import struct
import wave

import _bootstrap  # noqa: F401  - must run first; puts backend/ on sys.path

from config import SAMPLES_DIR

SAMPLE_RATE = 22050      # plenty for a simple tone-based backing track
BEATS_PER_MINUTE = 96
BEAT_SECONDS = 60 / BEATS_PER_MINUTE

# Note frequencies in Hz, one octave from A3. Built from equal temperament:
# each semitone multiplies frequency by 2^(1/12).
A3 = 220.0
NOTES = {name: A3 * (2 ** (semitone / 12)) for name, semitone in {
    "A3": 0, "B3": 2, "C4": 3, "D4": 5, "E4": 7, "F4": 8, "G4": 10,
    "A4": 12, "B4": 14, "C5": 15, "D5": 17, "E5": 19,
}.items()}

# A simple i-VI-III-VII progression in A minor - four chords, four beats each,
# repeated. Each chord is a list of note names played together.
PROGRESSION = [
    ["A3", "C4", "E4"],   # A minor
    ["F4", "A4", "C5"],   # F major
    ["C4", "E4", "G4"],   # C major
    ["G4", "B4", "D5"],   # G major
]

# The melody line, as (note, beats).
#
# The song is deliberately structured with an intro and a mid-song
# instrumental break, because both are needed to demonstrate the app properly:
#
#   beats  0-4   intro          - a quiet moment for microphone calibration
#   beats  4-20  verse          - four sung lines
#   beats 20-28  BREAK          - no lyrics; the singer should stay quiet here,
#                                 which is what the timing score checks
#   beats 28-44  chorus         - four more sung lines
#   beats 44-48  outro
MELODY = [
    # Intro (4 beats)
    ("A3", 2), ("C4", 2),

    # Verse (16 beats)
    ("E4", 2), ("C4", 1), ("D4", 1),
    ("E4", 2), ("A4", 2),
    ("G4", 2), ("E4", 1), ("C4", 1),
    ("D4", 4),

    # Instrumental break (8 beats)
    ("A4", 1), ("G4", 1), ("E4", 1), ("D4", 1),
    ("C4", 2), ("E4", 2),

    # Chorus (16 beats)
    ("E4", 2), ("C5", 1), ("B4", 1),
    ("C5", 2), ("E5", 2),
    ("D5", 2), ("B4", 1), ("G4", 1),
    ("A4", 4),

    # Outro (4 beats)
    ("E4", 2), ("A3", 2),
]

# Lyrics, with the beat each line starts on. Written to describe the app
# itself, so the demo doubles as a walkthrough.
#
# Note the jump from beat 16 to beat 28: that is the instrumental break, and
# singing through it will cost you points.
LYRICS = [
    # Verse
    (4,  "Welcome to the karaoke machine"),
    (8,  "Type a number, pick a song"),
    (12, "Watch the words begin to glow"),
    (16, "Sing along, you can't go wrong"),

    # Chorus, after the instrumental break
    (28, "Every line lights up in time"),
    (32, "Hit the notes and hold them strong"),
    (36, "When the music fades away"),
    (40, "Your final score will show"),
]


def envelope(position, total, attack=0.02, release=0.25):
    """
    Volume shape for one note, from 0 to 1.

    Without this, notes would start and stop instantly, producing an audible
    click at every edge (a sudden jump in the waveform). Fading in and out
    removes the click and sounds far more like an instrument.
    """
    fraction = position / total

    if fraction < attack:
        return fraction / attack
    if fraction > 1 - release:
        return (1 - fraction) / release
    return 1.0


def render():
    """Build the whole track as a list of float samples between -1 and 1."""
    total_beats = sum(beats for _, beats in MELODY)
    total_samples = int(total_beats * BEAT_SECONDS * SAMPLE_RATE)
    samples = [0.0] * total_samples

    # --- Chords (the backing pad) --------------------------------------
    beat = 0
    chord_index = 0
    while beat < total_beats:
        chord = PROGRESSION[chord_index % len(PROGRESSION)]
        start = int(beat * BEAT_SECONDS * SAMPLE_RATE)
        length = int(4 * BEAT_SECONDS * SAMPLE_RATE)

        for i in range(length):
            if start + i >= total_samples:
                break

            time = i / SAMPLE_RATE
            shape = envelope(i, length, attack=0.05, release=0.3)

            value = 0.0
            for note in chord:
                # Plain sine waves, kept quiet so the melody stays on top.
                value += math.sin(2 * math.pi * NOTES[note] * time)

            samples[start + i] += (value / len(chord)) * shape * 0.22

        beat += 4
        chord_index += 1

    # --- Melody ---------------------------------------------------------
    beat = 0
    for note_name, beats in MELODY:
        start = int(beat * BEAT_SECONDS * SAMPLE_RATE)
        length = int(beats * BEAT_SECONDS * SAMPLE_RATE)
        frequency = NOTES[note_name]

        for i in range(length):
            if start + i >= total_samples:
                break

            time = i / SAMPLE_RATE
            shape = envelope(i, length)

            # A second sine an octave up, at a third of the volume, gives the
            # tone a little brightness - a single sine sounds very dull.
            value = (
                math.sin(2 * math.pi * frequency * time)
                + 0.33 * math.sin(2 * math.pi * frequency * 2 * time)
            )

            samples[start + i] += value * shape * 0.3

        beat += beats

    return samples


def write_wav(samples, path):
    """Write float samples out as a 16-bit mono WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Normalise so the loudest point sits just under full scale. Without this,
    # overlapping chords and melody could exceed 1.0 and clip harshly.
    peak = max(abs(s) for s in samples) or 1.0
    scale = 0.89 / peak

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)        # 2 bytes = 16-bit
        wav.setframerate(SAMPLE_RATE)

        # 32767 is the largest value a signed 16-bit sample can hold.
        frames = b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s * scale)) * 32767))
            for s in samples
        )
        wav.writeframes(frames)


def write_lyrics(path_txt, path_lrc):
    """Write both the plain lyric sheet and the already-timed .lrc."""
    path_txt.parent.mkdir(parents=True, exist_ok=True)

    plain = ["Lyrics of Karaoke System - Demo Song", "", "[Verse]"]
    for index, (_, text) in enumerate(LYRICS):
        # A section header before the chorus, so the demo also shows how
        # section markers are handled by the sync editor.
        if index == 4:
            plain.extend(["", "[Chorus]"])
        plain.append(text)
    path_txt.write_text("\n".join(plain) + "\n", encoding="utf-8")

    lrc = [
        "[ti:Demo Song]",
        "[ar:Karaoke System]",
        "[re:Generated by scripts/make_sample_song.py]",
        "",
    ]
    for beat, text in LYRICS:
        seconds = beat * BEAT_SECONDS
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        lrc.append(f"[{minutes:02d}:{remainder:05.2f}]{text}")

    path_lrc.write_text("\n".join(lrc) + "\n", encoding="utf-8")


def main() -> None:
    audio_path = SAMPLES_DIR / "audio" / "Karaoke System - Demo Song.wav"
    txt_path = SAMPLES_DIR / "lyrics" / "Karaoke System - Demo Song.txt"
    lrc_path = SAMPLES_DIR / "lyrics" / "Karaoke System - Demo Song.lrc"

    print("Synthesising the demo song...")
    samples = render()
    write_wav(samples, audio_path)
    write_lyrics(txt_path, lrc_path)

    duration = len(samples) / SAMPLE_RATE
    size_mb = audio_path.stat().st_size / (1024 * 1024)

    print(f"  {audio_path.name}  ({duration:.1f}s, {size_mb:.2f} MB)")
    print(f"  {txt_path.name}")
    print(f"  {lrc_path.name}  ({len(LYRICS)} timed lines)")
    print("\nRun 'python scripts/import_songs.py' to add it to the library.")


if __name__ == "__main__":
    main()
