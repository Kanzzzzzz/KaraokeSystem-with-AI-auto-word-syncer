"""
Serves audio and lyric files out of the data/ folder.

Files are not in Flask's static folder because data/library/ is gitignored and
may sit anywhere on disk, so they are served through this blueprint instead.
That also gives us one place to enforce the path safety check below.
"""

from pathlib import Path

from flask import Blueprint, abort, send_file

from config import DATA_DIR

media_bp = Blueprint("media", __name__, url_prefix="/media")


def safe_data_path(relative_path: str) -> Path:
    """
    Turn a stored relative path into a real file path, refusing anything that
    points outside data/.

    This blocks directory traversal: without it, a request for
    "../../../../Windows/System32/config/SAM" would be happily served.
    resolve() collapses any ".." segments first, then is_relative_to() checks
    where the result actually landed.
    """
    candidate = (DATA_DIR / relative_path).resolve()

    if not candidate.is_relative_to(DATA_DIR.resolve()):
        abort(403, description="Path is outside the data folder")

    if not candidate.is_file():
        abort(404, description="File not found")

    return candidate


@media_bp.route("/audio/<path:relative_path>")
def serve_audio(relative_path: str):
    """
    Stream an audio file.

    conditional=True is what makes seeking work: it tells Flask to honour the
    browser's HTTP Range requests, so dragging the progress bar fetches just
    that part of the file instead of re-downloading the whole track. The
    sync editor depends on this heavily, since it seeks constantly.
    """
    return send_file(safe_data_path(relative_path), conditional=True)


@media_bp.route("/lyrics/<path:relative_path>")
def serve_lyrics(relative_path: str):
    """
    Serve a .lrc or .txt lyric file as plain text.

    Sent as text/plain so the browser hands it to fetch() as a string rather
    than offering it as a download.
    """
    path = safe_data_path(relative_path)

    if path.suffix.lower() not in {".lrc", ".txt"}:
        abort(400, description="Not a lyric file")

    return send_file(path, mimetype="text/plain; charset=utf-8")
