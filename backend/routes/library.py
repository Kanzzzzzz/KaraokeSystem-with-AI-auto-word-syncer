"""
Adding songs to the library from the website.

    POST /api/library/upload        (multipart/form-data)

Fields: original (file, with vocals), minus (file, optional), artist, title,
language ("English" | "Filipino" | ""), lyrics (optional text), auto_sync
("1" or "0"). At least one of the two files is needed.

The files are saved into the library folders straight away; the slow part -
making the minus-one, importing, LRCLIB - is queued as a background job
(see utils/library_import.py). Answers 202 with {"job_id"}, or 400/409 with
{"error"} saying what is wrong.

    GET /api/library/jobs/<job_id>

How that job is going: {"status": "queued" | "running" | "done" | "failed",
"percent", "stage", "message", "songs", "notes", "error"}.
"""

from flask import Blueprint, jsonify, request

from database import get_db
from models import song as song_model
from utils import library_import
from utils.autosync import service

library_bp = Blueprint("library", __name__, url_prefix="/api/library")


@library_bp.route("/upload", methods=["POST"])
def upload_song():
    form = request.form
    try:
        job = library_import.save_upload(
            get_db(),
            original=request.files.get("original") or None,
            minus=request.files.get("minus") or None,
            artist=form.get("artist", ""),
            title=form.get("title", ""),
            language=form.get("language", ""),
            lyrics=form.get("lyrics", ""),
            auto_sync=form.get("auto_sync", "1") == "1",
        )
    except library_import.UploadError as error:
        status = 409 if "already" in str(error) else 400
        return jsonify({"error": str(error)}), status

    job_id = service.submit_import(job)
    return jsonify({"ok": True, "job_id": job_id, "title": job["title"]}), 202


@library_bp.route("/activity")
def activity():
    """What the background thread is busy with, for the 'still working' badge."""
    state = service.activity()
    conn = get_db()
    jobs = [{"kind": "import", "title": job["title"], "percent": job["percent"],
             "message": job["message"], "queued": job["status"] == "queued"}
            for job in state["imports"]]
    for sync in state["syncs"]:
        song = song_model.get_by_id(conn, sync["song_id"])
        jobs.append({"kind": "sync", "title": song["title"] if song else "a song",
                     "percent": sync.get("percent"), "message": sync.get("message"),
                     "queued": sync.get("stage") == "queued"})
    return jsonify({"busy": bool(jobs), "jobs": jobs})


@library_bp.route("/jobs/<int:job_id>")
def import_job(job_id: int):
    state = service.import_status(job_id)
    if state is None:
        return jsonify({"error": "No such import job (the server may have restarted)."}), 404
    return jsonify(state)
