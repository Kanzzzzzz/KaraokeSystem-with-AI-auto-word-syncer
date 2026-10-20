"""
Queue API.

    GET    /api/queue              current queue + what is playing
    POST   /api/queue              add {song_id | code, singer_name}
    DELETE /api/queue/<entry_id>   host removes an entry
    PATCH  /api/queue/reorder      host reorders: {"order": [id, id, ...]}
    POST   /api/queue/advance      finish current song, start the next
    POST   /api/queue/clear        empty the queue

Every route that changes the queue calls broadcast_queue() afterwards, so all
connected screens and phones update immediately over SocketIO.
"""

from flask import Blueprint, jsonify, request

from database import get_db
from models import queue as queue_model
from models import session as session_model
from models import song as song_model
from sockets.queue_events import broadcast_now_playing, broadcast_queue

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")


def current_session_id(conn) -> int:
    return session_model.get_or_create_active(conn)["id"]


def queue_payload(conn, session_id: int) -> dict:
    """The shape every queue response uses, so clients only parse one format."""
    return {
        "session_id": session_id,
        "queue": queue_model.list_queue(conn, session_id),
        "now_playing": queue_model.get_now_playing(conn, session_id),
    }


@queue_bp.route("", methods=["GET"])
def get_queue():
    conn = get_db()
    return jsonify(queue_payload(conn, current_session_id(conn)))


@queue_bp.route("", methods=["POST"])
def add_to_queue():
    conn = get_db()
    payload = request.get_json(silent=True) or {}

    singer_name = str(payload.get("singer_name", "")).strip()
    if not singer_name:
        return jsonify({"error": "A singer name is required"}), 400

    # Cap the length so one person cannot break the queue layout for everyone
    # with a 5,000-character name.
    singer_name = singer_name[:40]

    # Accept either a database id or a karaoke code, because the phone remote
    # sends an id from a search result while the keypad sends a code.
    song = None
    if payload.get("song_id") is not None:
        song = song_model.get_by_id(conn, int(payload["song_id"]))
    elif payload.get("code"):
        song = song_model.get_by_code(conn, str(payload["code"]))

    if song is None:
        return jsonify({"error": "Song not found"}), 404

    session_id = current_session_id(conn)
    entry = queue_model.add(conn, session_id, song["id"], singer_name)

    # If nothing is currently playing, start immediately.
    #
    # Without this, a queue that goes from empty to non-empty leaves every
    # entry stuck in "waiting" with no way to start it: the transport's Play
    # button only resumes audio that is already loaded, and Skip stays
    # disabled until something is "now playing" - so the very first song
    # added would just sit there forever with no button able to start it.
    #
    # advance() safely promotes the oldest waiting entry to "playing" only
    # when nothing already has that status, so this never interrupts a song
    # that is already going.
    now_playing = queue_model.get_now_playing(conn, session_id)
    if now_playing is None:
        now_playing = queue_model.advance(conn, session_id)
        # advance() just promoted this exact row from "waiting" to "playing" -
        # refetch it so the response is internally consistent (entry.status
        # would otherwise still read "waiting" while now_playing shows the
        # same id as playing).
        if now_playing is not None and now_playing["id"] == entry["id"]:
            entry = now_playing

    # The model layer does not commit - the request teardown normally would,
    # but the broadcast below has to see the finished write, so commit now.
    conn.commit()
    broadcast_queue(conn, session_id)
    if now_playing is not None and now_playing["id"] == entry["id"]:
        broadcast_now_playing(now_playing)

    return jsonify({"ok": True, "entry": entry, **queue_payload(conn, session_id)}), 201


@queue_bp.route("/<int:entry_id>", methods=["DELETE"])
def remove_from_queue(entry_id: int):
    conn = get_db()
    session_id = current_session_id(conn)

    if not queue_model.remove(conn, entry_id):
        return jsonify({"error": "Entry not found, or it has already been sung"}), 404

    conn.commit()
    broadcast_queue(conn, session_id)
    return jsonify({"ok": True, **queue_payload(conn, session_id)})


@queue_bp.route("/reorder", methods=["PATCH"])
def reorder_queue():
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")

    if not isinstance(order, list):
        return jsonify({"error": "Expected an 'order' array of entry ids"}), 400

    session_id = current_session_id(conn)
    queue_model.reorder(conn, session_id, [int(i) for i in order])

    conn.commit()
    broadcast_queue(conn, session_id)
    return jsonify({"ok": True, **queue_payload(conn, session_id)})


@queue_bp.route("/advance", methods=["POST"])
def advance_queue():
    """
    Move to the next song.

    Called automatically when a track ends, and manually by the host's skip
    button. `finished_status` distinguishes the two so the history shows which
    songs were actually sung through.
    """
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    finished_status = "skipped" if payload.get("skipped") else "done"

    session_id = current_session_id(conn)
    now_playing = queue_model.advance(conn, session_id, finished_status=finished_status)

    conn.commit()
    broadcast_queue(conn, session_id)
    broadcast_now_playing(now_playing)

    return jsonify({"ok": True, **queue_payload(conn, session_id)})


@queue_bp.route("/clear", methods=["POST"])
def clear_queue():
    conn = get_db()
    session_id = current_session_id(conn)
    queue_model.clear(conn, session_id)

    conn.commit()
    broadcast_queue(conn, session_id)
    return jsonify({"ok": True, **queue_payload(conn, session_id)})
