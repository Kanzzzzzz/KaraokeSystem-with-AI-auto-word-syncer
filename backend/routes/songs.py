"""
Song library API.

    GET /api/songs               list the library
    GET /api/songs?q=1001        search by code prefix or by title/artist text
    GET /api/songs/<id>          one song, including its lyrics if synced
    GET /api/songs/code/<code>   look up by karaoke number code
    PATCH /api/songs/<id>        edit title, artist, language, code
    DELETE /api/songs/<id>       remove a song (?files=1 also deletes its files)

New songs are added through POST /api/library/upload (routes/library.py).
"""

from flask import Blueprint, jsonify, request

from database import get_db
from models import song as song_model
from routes.media import safe_data_path
from sockets.queue_events import broadcast_queue
from utils.autosync import service as autosync_service
from utils.lrc import parse_lrc, read_plain_lines

songs_bp = Blueprint("songs", __name__, url_prefix="/api/songs")


def with_media_urls(song: dict) -> dict:
    """
    Add the URLs the browser should fetch, derived from the stored paths.

    Building these here (rather than storing them) means the database keeps
    plain filesystem paths and the URL scheme can change without a migration.
    """
    if song is None:
        return None

    song = dict(song)
    song["audio_url"] = f"/media/audio/{song['audio_path']}"
    song["lyrics_url"] = f"/media/lyrics/{song['lyrics_path']}" if song["lyrics_path"] else None
    song["source_text_url"] = (
        f"/media/lyrics/{song['source_text_path']}" if song["source_text_path"] else None
    )
    return song


@songs_bp.route("")
def list_songs():
    """Search, or list everything when no query is given."""
    query = request.args.get("q", "")

    # Cap the limit so a stray ?limit=999999 cannot be used to dump the
    # entire table in one request.
    limit = min(request.args.get("limit", default=50, type=int), 200)

    results = song_model.search(get_db(), query, limit=limit)
    return jsonify({"query": query, "count": len(results),
                    "songs": [with_media_urls(s) for s in results]})


@songs_bp.route("/<int:song_id>")
def get_song(song_id: int):
    song = song_model.get_by_id(get_db(), song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404
    return jsonify(with_media_urls(song))


@songs_bp.route("/code/<code>")
def get_song_by_code(code: str):
    """Look up by number code - what the on-screen keypad calls."""
    song = song_model.get_by_code(get_db(), code)
    if song is None:
        return jsonify({"error": f"No song with code {code}"}), 404
    return jsonify(with_media_urls(song))


@songs_bp.route("/<int:song_id>", methods=["PATCH"])
def update_song(song_id: int):
    """Edit title, artist, language and/or code. Send only the fields to change."""
    body = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        song = song_model.update(
            conn, song_id,
            **{field: body[field] for field in ("title", "artist", "language", "code")
               if field in body},
        )
    except song_model.SongEditError as error:
        return jsonify({"error": str(error)}), 400
    if song is None:
        return jsonify({"error": "Song not found"}), 404
    conn.commit()
    return jsonify(with_media_urls(song))


@songs_bp.route("/<int:song_id>", methods=["DELETE"])
def delete_song(song_id: int):
    """
    Remove a song. With ?files=1 its playback track and lyrics files are
    deleted too; the original recording in originals/ is always kept.
    """
    conn = get_db()
    song = song_model.get_by_id(conn, song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404
    if request.args.get("files") == "1" and autosync_service.is_busy_with(song_id):
        return jsonify({"error": "Auto sync is working on this song. Try again when it finishes."}), 409

    song_model.delete(conn, song_id)
    conn.commit()

    if request.args.get("files") == "1":
        for relative in (song["audio_path"], song["lyrics_path"], song["source_text_path"]):
            if relative:
                safe_data_path(relative).unlink(missing_ok=True)

    session = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    if session:
        broadcast_queue(conn, session["id"])
    return jsonify({"ok": True})


@songs_bp.route("/<int:song_id>/lyrics")
def get_song_lyrics(song_id: int):
    """
    The lyrics for a song, in whichever form exists.

    Returns "synced" with timestamped lines when an .lrc is present, or
    "plain" with untimed lines when only the original .txt is. The player uses
    the first; the sync editor uses the second as its list of lines to stamp.
    """
    song = song_model.get_by_id(get_db(), song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404

    if song["lyrics_path"]:
        text = safe_data_path(song["lyrics_path"]).read_text(encoding="utf-8", errors="replace")
        parsed = parse_lrc(text)
        return jsonify({
            "song_id": song_id,
            "format": "synced",
            "metadata": parsed["metadata"],
            "lines": parsed["lines"],
        })

    if song["source_text_path"]:
        text = safe_data_path(song["source_text_path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        return jsonify({
            "song_id": song_id,
            "format": "plain",
            "lines": read_plain_lines(text),
        })

    return jsonify({"song_id": song_id, "format": "none", "lines": []})
