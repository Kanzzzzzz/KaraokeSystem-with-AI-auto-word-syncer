"""
Scores and the session leaderboard.

    POST /api/scores        save a finished performance
    GET  /api/leaderboard   tonight's ranking
    GET  /api/scores/<id>   one score, with its energy timeline for the recap
"""

from flask import Blueprint, jsonify, request

from database import get_db
from models import queue as queue_model
from models import score as score_model
from models import session as session_model
from sockets.queue_events import broadcast_score

scores_bp = Blueprint("scores", __name__, url_prefix="/api")


@scores_bp.route("/scores", methods=["POST"])
def save_score():
    conn = get_db()
    payload = request.get_json(silent=True) or {}

    if payload.get("song_id") is None:
        return jsonify({"error": "song_id is required"}), 400
    if payload.get("final_score") is None:
        return jsonify({"error": "final_score is required"}), 400

    session_id = session_model.get_or_create_active(conn)["id"]

    # Prefer the singer name recorded on the queue entry over one sent by the
    # client, so the leaderboard always matches who actually queued the song.
    singer_name = str(payload.get("singer_name", "")).strip()
    entry_id = payload.get("queue_entry_id")
    if entry_id is not None:
        entry = queue_model.get(conn, int(entry_id))
        if entry is not None:
            singer_name = entry["singer_name"]

    if not singer_name:
        singer_name = "Anonymous"

    score = score_model.save(
        conn,
        session_id=session_id,
        song_id=int(payload["song_id"]),
        queue_entry_id=int(entry_id) if entry_id is not None else None,
        singer_name=singer_name,
        final_score=payload["final_score"],
        timing_score=payload.get("timing_score"),
        energy_score=payload.get("energy_score"),
        pitch_score=payload.get("pitch_score"),
        energy_timeline=payload.get("energy_timeline"),
    )

    conn.commit()
    broadcast_score(score)

    return jsonify({"ok": True, "score": score}), 201


@scores_bp.route("/scores/<int:score_id>")
def get_score(score_id: int):
    """One score including its energy timeline - what the recap screen loads."""
    score = score_model.get(get_db(), score_id)
    if score is None:
        return jsonify({"error": "Score not found"}), 404
    return jsonify(score)


@scores_bp.route("/leaderboard")
def get_leaderboard():
    conn = get_db()
    session_id = session_model.get_or_create_active(conn)["id"]
    limit = min(request.args.get("limit", default=20, type=int), 100)

    return jsonify({
        "session_id": session_id,
        "leaderboard": score_model.leaderboard(conn, session_id, limit=limit),
        "recent": score_model.recent(conn, session_id),
    })
