"""
How accurate is auto sync? Compare its word times with ones tapped by hand.

    python scripts/measure_drift.py --song 1004
    python scripts/measure_drift.py --reference tapped.lrc --auto result.json

REFERENCE: an .lrc in which you word-tapped SOME lines in the sync editor
(Words mode). Only lines with word timing are used, so a handful of carefully
tapped lines is enough - you do not need the whole song.

AUTO: the auto-sync result - a .autosync.json file, a --dry-run result from
scripts/auto_sync.py, or an auto-synced .lrc.

With --song, the reference is the song's .lrc and the auto result is its
.autosync.json. The easy way to get both:
  1. in the sync editor, word-tap a few lines and save
  2. run auto sync - because the song now has hand-tapped words, the result is
     kept as a draft in the .autosync.json and your .lrc is left alone
  3. run this script

Lines are matched by their words (ignoring case and punctuation) and the
nearest time, so a chorus sung three times is compared with the right repeat.

-------------------------------------------------------------------------------
WHAT THE NUMBERS MEAN
-------------------------------------------------------------------------------

For every word: error = auto time - your time (negative = auto is early).

  bias       the average error. Far from 0 = everything is shifted the same
             way - a constant offset, easy to fix - rather than random.
  median     half of the words are closer than this
  p90        90% of the words are closer than this
  <=100ms... share of words within 0.1 / 0.2 / 0.3 s of your tap

Your own taps are not perfect either (typically 50-100 ms), so errors of
about 0.1 s are within the noise of the measurement itself.

The BASELINE row is what the karaoke would show WITHOUT word timing: the
line wiped at an even pace, letter by letter, from its LRCLIB start to the
next line's. Auto sync is only worth having if it clearly beats it.
"""

import argparse
import csv
import difflib
import json
import re
import statistics
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  - must run first; puts backend/ on sys.path

from config import DATA_DIR
from database import session
from models import song as song_model
from utils.lrc import parse_lrc
from utils.lyrics_store import autosync_sidecar_path

AUTO_SOURCE = "Karaoke System auto-sync"


def key(text: str) -> str:
    """'Puwede bang...?' -> 'puwedebang...' - for matching, not display."""
    return re.sub(r"[^a-z]", "", text.lower())


# ==========================================================================
# Reading the two sides
# ==========================================================================

def load_reference(path: Path) -> list[dict]:
    """The hand-tapped lines: [{"text", "time", "words": [{"text", "time"}]}]."""
    parsed = parse_lrc(path.read_text(encoding="utf-8", errors="replace"))
    if parsed["metadata"].get("re", "").startswith(AUTO_SOURCE):
        sys.exit(f"{path.name} was written by auto sync, so it cannot be the hand-tapped reference.")
    lines = [line for line in parsed["lines"] if line["words"]]
    if not lines:
        sys.exit(f"{path.name} has no word-timed lines. Tap some in the sync editor's Words mode first.")
    return lines


def load_auto(path: Path) -> list[dict]:
    """
    The auto result as [{"text", "time", "anchor", "next_anchor", "words":
    [{"text", "time", "confidence"}]}], from JSON or from an .lrc.
    """
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["lines"]
        anchors = [line.get("anchor") for line in raw]
        lines = []
        for index, line in enumerate(raw):
            if not line["words"]:
                continue
            later = next((a for a in anchors[index + 1:] if a is not None), None)
            lines.append({
                "text": line["text"], "time": line["time"],
                "anchor": line.get("anchor"), "next_anchor": later,
                "words": [{"text": w["text"], "time": w["start"],
                           "confidence": w.get("confidence", "?"), "flags": w.get("flags", [])}
                          for w in line["words"]],
            })
        return lines

    parsed = parse_lrc(path.read_text(encoding="utf-8", errors="replace"))
    times = [line["time"] for line in parsed["lines"]]
    lines = []
    for index, line in enumerate(parsed["lines"]):
        if line["words"]:
            lines.append({
                "text": line["text"], "time": line["time"], "anchor": line["time"],
                "next_anchor": times[index + 1] if index + 1 < len(times) else None,
                "words": [{"text": w["text"], "time": w["time"], "confidence": "?", "flags": []}
                          for w in line["words"]],
            })
    return lines


# ==========================================================================
# Pairing words
# ==========================================================================

def even_pace(line: dict) -> list[float] | None:
    """
    The BASELINE: where each word would start if the line were wiped at an
    even pace, letter by letter, from its anchor to the next - which is what
    the player does for a line with no word timing.
    """
    start, stop = line["anchor"], line["next_anchor"]
    if start is None or stop is None or stop <= start:
        return None
    lengths = [len(w["text"]) for w in line["words"]]
    total = sum(lengths) or 1
    times, done = [], 0
    for length in lengths:
        times.append(start + (stop - start) * done / total)
        done += length
    return times


def pair_words(reference: list[dict], auto: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Match each reference line to an auto line, then word to word.
    Returns (pairs, notes). A pair is one word measured both ways.
    """
    pool = list(auto)
    pairs, notes = [], []

    for ref_line in reference:
        candidates = [a for a in pool if key(a["text"]) == key(ref_line["text"])]
        if not candidates:
            notes.append(f"no auto line matches: \"{ref_line['text']}\"")
            continue
        auto_line = min(candidates, key=lambda a: abs(a["time"] - ref_line["time"]))
        if abs(auto_line["time"] - ref_line["time"]) > 10:
            notes.append(f"matching auto line is over 10 s away: \"{ref_line['text']}\"")
            continue
        pool.remove(auto_line)

        # Skip a line whose times are all identical to auto's: it was loaded
        # from the auto result and saved, not tapped - comparing it with
        # itself would make auto sync look perfect.
        if (len(ref_line["words"]) == len(auto_line["words"])
                and all(abs(r["time"] - a["time"]) < 0.011
                        for r, a in zip(ref_line["words"], auto_line["words"]))):
            notes.append(f"skipped, identical to auto (not hand-tapped): \"{ref_line['text']}\"")
            continue

        baseline = even_pace(auto_line)
        ref_keys = [key(w["text"]) for w in ref_line["words"]]
        auto_keys = [key(w["text"]) for w in auto_line["words"]]
        matcher = difflib.SequenceMatcher(a=ref_keys, b=auto_keys, autojunk=False)
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                r = ref_line["words"][block.a + k]
                a = auto_line["words"][block.b + k]
                pairs.append({
                    "line": ref_line["text"], "word": r["text"],
                    "reference": r["time"], "auto": a["time"],
                    "error": a["time"] - r["time"],
                    "baseline_error": (baseline[block.b + k] - r["time"]) if baseline else None,
                    "confidence": a["confidence"], "flags": ",".join(a["flags"]),
                })
    return pairs, notes


# ==========================================================================
# Statistics
# ==========================================================================

def describe(errors: list[float]) -> dict:
    absolute = sorted(abs(e) for e in errors)
    n = len(absolute)
    return {
        "n": n,
        "bias": statistics.mean(errors),
        "median": statistics.median(absolute),
        "p90": absolute[min(n - 1, int(0.9 * n))],
        "max": absolute[-1],
        "within": [sum(e <= t for e in absolute) / n for t in (0.1, 0.2, 0.3)],
    }


def row(label: str, s: dict) -> str:
    w1, w2, w3 = (f"{x:>6.0%}" for x in s["within"])
    return (f"  {label:<22}{s['n']:>5}  {s['bias']:>+7.3f}s {s['median']:>7.3f}s "
            f"{s['p90']:>7.3f}s {s['max']:>7.3f}s {w1} {w2} {w3}")


def report(pairs: list[dict], notes: list[str], show_words: bool) -> None:
    if not pairs:
        sys.exit("No words could be compared.\n" + "\n".join(f"  - {n}" for n in notes))

    header = (f"  {'':<22}{'words':>5}  {'bias':>8} {'median':>8} {'p90':>8} {'max':>8} "
              f"{'<=100ms':>6} {'<=200ms':>6} {'<=300ms':>6}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    print(row("auto sync", describe([p["error"] for p in pairs])))

    baseline = [p["baseline_error"] for p in pairs if p["baseline_error"] is not None]
    if baseline:
        print(row("baseline (even pace)", describe(baseline)))

    # Does the confidence score mean anything? High-confidence words should
    # be clearly more accurate than low ones.
    for level in ("high", "low"):
        errors = [p["error"] for p in pairs if p["confidence"] == level]
        if errors:
            print(row(f"  {level}-confidence words", describe(errors)))

    by_line: dict[str, list[float]] = {}
    for p in pairs:
        by_line.setdefault(p["line"], []).append(abs(p["error"]))
    print("\n  Per line (mean |error|, worst word):")
    for line, errors in by_line.items():
        worst = max((p for p in pairs if p["line"] == line), key=lambda p: abs(p["error"]))
        print(f"    {statistics.mean(errors):6.3f}s  worst \"{worst['word']}\" {worst['error']:+.2f}s   "
              f"{line[:55]}")

    if show_words:
        print("\n  Every word (auto - yours):")
        for p in pairs:
            print(f"    {p['error']:+7.3f}s  {p['confidence']:<4} {p['word']:<18} {p['flags']}")

    if notes:
        print("\n  Notes:")
        for note in notes:
            print(f"    - {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure auto-sync accuracy against hand taps.")
    parser.add_argument("--song", help="song code: its .lrc vs its .autosync.json")
    parser.add_argument("--reference", help="hand-tapped .lrc")
    parser.add_argument("--auto", help="auto result: .json or .lrc")
    parser.add_argument("--words", action="store_true", help="list every word's error")
    parser.add_argument("--csv", help="also write every word's numbers to this CSV file")
    args = parser.parse_args()

    if args.song:
        with session() as conn:
            song = song_model.get_by_code(conn, args.song)
        if song is None or not song["lyrics_path"]:
            sys.exit(f"Song {args.song} does not exist or has no .lrc.")
        reference_path = Path(args.reference) if args.reference else DATA_DIR / song["lyrics_path"]
        auto_path = Path(args.auto) if args.auto else autosync_sidecar_path(song)
    elif args.reference and args.auto:
        reference_path, auto_path = Path(args.reference), Path(args.auto)
    else:
        parser.error("give --song, or both --reference and --auto")

    if not auto_path.is_file():
        sys.exit(f"No auto-sync result at {auto_path}. Run auto sync first.")

    print(f"Reference (hand-tapped): {reference_path.name}")
    print(f"Auto sync result:        {auto_path.name}\n")
    pairs, notes = pair_words(load_reference(reference_path), load_auto(auto_path))
    report(pairs, notes, args.words)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(pairs[0].keys()))
            writer.writeheader()
            writer.writerows(pairs)
        print(f"\nWrote {len(pairs)} rows to {args.csv}")


if __name__ == "__main__":
    main()
