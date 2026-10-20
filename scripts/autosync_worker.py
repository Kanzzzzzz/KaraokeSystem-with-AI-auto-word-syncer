"""
The auto-sync worker process. Not meant to be run by hand - use
scripts/auto_sync.py, or the Auto Sync button in the sync editor.

    .venv-align/Scripts/python.exe scripts/autosync_worker.py <job.json>

It runs with the .venv-align Python (which has torch, Demucs and the
alignment model), reads one job file, and talks back to whoever started it
by printing one JSON object per line on stdout:

    {"type": "progress", "percent": 42.0, "stage": "separating", "message": "..."}
    {"type": "result", "path": "<where the result JSON was written>"}
    {"type": "error", "message": "..."}

A job normally times a song's words. A job with "task": "make_minus" instead
turns an original recording into a minus-one track (pipeline.make_minus_one).

It never touches the database or any lyric file. It only reads audio and
writes cached stems, its result file, and (for make_minus) the new minus-one.
Deciding what to do with the result - including respecting a song's lock -
is the karaoke app's job.
"""

import json
import sys
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    # Our messages go to the REAL stdout. Everything else - library warnings,
    # progress bars, stray prints - is redirected to stderr, so nothing can
    # break the one-JSON-object-per-line protocol.
    channel = sys.stdout
    sys.stdout = sys.stderr

    def send(message: dict) -> None:
        channel.write(json.dumps(message) + "\n")
        channel.flush()

    last_percent = [-1.0]

    def report(percent: float, stage: str, message: str) -> None:
        # At most one message per whole percent, so the pipe is not flooded.
        if int(percent) != int(last_percent[0]) or stage == "finished":
            last_percent[0] = percent
            send({"type": "progress", "percent": round(percent, 1),
                  "stage": stage, "message": message})

    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

        from autosync_engine import pipeline

        if job.get("task") == "make_minus":
            result = pipeline.make_minus_one(job, report)
        else:
            result = pipeline.run(job, report)
        result_path = Path(job["result_path"])
        result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        send({"type": "result", "path": str(result_path)})
        return 0

    except Exception as error:
        from autosync_engine.pipeline import PipelineError

        # A PipelineError's message is written for the user. Anything else is
        # a bug or a crash: send the short message, log the full traceback.
        traceback.print_exc()
        message = str(error) if isinstance(error, PipelineError) else (
            f"The auto-sync worker crashed: {type(error).__name__}: {error}")
        send({"type": "error", "message": message})
        return 1


if __name__ == "__main__":
    sys.exit(main())
