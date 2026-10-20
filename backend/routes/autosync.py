"""
Automatic word sync API.

    POST /api/songs/<id>/auto-sync

Body (optional): {"isolate_vocals": true}

Queues the song and returns straight away with 202 Accepted - the work takes
minutes and happens in the background (see utils/autosync.py). Answers:
    202  queued
    404  no such song
    409  already queued or running
    423  the song is locked against auto sync (423 is HTTP's "Locked")

    GET /api/songs/<id>/auto-sync

Where the song stands: {"sync_status", "sync_message", "sync_locked",
"progress": {"percent", "stage", "message"} | null, "result_available"}.
The sync editor asks this about once a second while a job runs.

    GET /api/songs/<id>/auto-sync/result

The last result in full - every word's time, confidence and flags - from the
song's .autosync.json file. This is what the editor loads to highlight
low-confidence words, or to offer a draft.
"""

import json

from flask import Blueprint, jsonify, request

from database import get_db
from models import song as song_model
from models.song import SongLockedError
from utils import lyrics_store
from utils.autosync import service

autosync_bp = Blueprint("autosync", __name__, url_prefix="/api/songs")


def _state(song: dict) -> dict:
    sidecar = lyrics_store.autosync_sidecar_path(song)
    return {
        "song_id": song["id"],
        "sync_status": song["sync_status"],
        "sync_message": song["sync_message"],
        "sync_locked": song["sync_locked"],
        "progress": service.progress(song["id"]),
        "result_available": sidecar.is_file(),
    }


@autosync_bp.route("/<int:song_id>/auto-sync", methods=["POST"])
def start_auto_sync(song_id: int):
    payload = request.get_json(silent=True) or {}
    options = {"isolate_vocals": bool(payload.get("isolate_vocals", True))}

    try:
        song = service.enqueue(song_id, options)
    except LookupError:
        return jsonify({"error": "Song not found"}), 404
    except SongLockedError:
        return jsonify({"error": "This song is locked against auto sync. "
                                 "Remove \"sync_locked\" from song_overrides.json to allow it."}), 423
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409

    return jsonify({"ok": True, **_state(song)}), 202


@autosync_bp.route("/<int:song_id>/auto-sync", methods=["GET"])
def auto_sync_status(song_id: int):
    song = song_model.get_by_id(get_db(), song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404
    return jsonify(_state(song))


@autosync_bp.route("/<int:song_id>/auto-sync/result", methods=["GET"])
def auto_sync_result(song_id: int):
    song = song_model.get_by_id(get_db(), song_id)
    if song is None:
        return jsonify({"error": "Song not found"}), 404

    sidecar = lyrics_store.autosync_sidecar_path(song)
    if not sidecar.is_file():
        return jsonify({"error": "This song has no auto-sync result yet."}), 404
    return jsonify(json.loads(sidecar.read_text(encoding="utf-8")))
