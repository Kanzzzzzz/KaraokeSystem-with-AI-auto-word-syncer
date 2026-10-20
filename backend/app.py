"""
Karaoke System - application entry point.

Run it with:

    python backend/app.py

then open http://localhost:5000 in a browser.

This file follows the "application factory" pattern: create_app() builds and
configures the app rather than creating it at import time. That keeps startup
in one readable place and makes the app easy to construct differently for
tests.
"""

import sys
from pathlib import Path

# Put backend/ on the import path so the modules below can import each other
# as `from config import ...`. This has to happen before those imports run,
# which is why it is not grouped with them at the top.
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from flask import Flask, jsonify, send_from_directory  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402
from routes.autosync import autosync_bp  # noqa: E402
from routes.library import library_bp  # noqa: E402
from routes.lyrics import lyrics_bp  # noqa: E402
from routes.media import media_bp  # noqa: E402
from routes.pages import local_ip_address, pages_bp  # noqa: E402
from routes.queue import queue_bp  # noqa: E402
from routes.scores import scores_bp  # noqa: E402
from routes.songs import songs_bp  # noqa: E402
from sockets.queue_events import init_socketio  # noqa: E402
from utils.autosync import service as autosync_service  # noqa: E402
from utils.importer import apply_sync_locks  # noqa: E402


def create_app() -> tuple[Flask, SocketIO]:
    """Build the Flask app and its SocketIO server."""
    app = Flask(__name__, static_folder=None)
    app.config["SECRET_KEY"] = config.SECRET_KEY

    # Songs can be uploaded through the sync editor. Flask would otherwise
    # accept a request of any size; this caps it (see utils/library_import.py).
    from utils.library_import import MAX_UPLOAD_MB
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    # Make sure the tables exist before the first request can touch them, so
    # a fresh clone works without remembering to run init_db.py first.
    database.init_db()
    database.init_app(app)

    # Put every "sync_locked" from song_overrides.json into force before any
    # request - or the auto-sync worker - can see a song as unlocked.
    with database.session() as conn:
        apply_sync_locks(conn)

    # Serve the frontend's CSS and JS. A custom route is used instead of
    # Flask's built-in static folder because the frontend lives outside the
    # backend package.
    @app.route("/css/<path:filename>")
    def css_files(filename):
        return send_from_directory(config.FRONTEND_DIR / "css", filename)

    @app.route("/js/<path:filename>")
    def js_files(filename):
        # ES modules must be served as JavaScript or the browser refuses to
        # run them, and Windows occasionally reports .js as text/plain.
        return send_from_directory(
            config.FRONTEND_DIR / "js", filename, mimetype="text/javascript"
        )

    # Placeholder media for the karaoke screen: the background video loop
    # and the score chime (see frontend/assets/README.md). Kept apart from
    # data/ because these files ARE committed - they are self-made, so they
    # are safe to share. send_from_directory answers HTTP Range requests,
    # which browsers use to stream and loop a <video>.
    @app.route("/assets/<path:filename>")
    def asset_files(filename):
        return send_from_directory(config.FRONTEND_DIR / "assets", filename)

    for blueprint in (pages_bp, songs_bp, queue_bp, scores_bp, lyrics_bp, media_bp, autosync_bp,
                      library_bp):
        app.register_blueprint(blueprint)

    # Return JSON for API errors instead of Flask's HTML error pages, so the
    # frontend's fetch() calls always get something they can parse.
    @app.errorhandler(404)
    def handle_404(error):
        return jsonify({"error": "Not found", "detail": str(error.description)}), 404

    @app.errorhandler(403)
    def handle_403(error):
        return jsonify({"error": "Forbidden", "detail": str(error.description)}), 403

    @app.errorhandler(413)
    def handle_413(error):
        return jsonify({"error": "That file is too big to upload."}), 413

    @app.errorhandler(500)
    def handle_500(error):
        return jsonify({"error": "Server error", "detail": str(error)}), 500

    # cors_allowed_origins="*" lets phones on the LAN connect. Fine for a
    # classroom demo on a private network; a public deployment would list
    # specific origins instead.
    #
    # allow_upgrades=False keeps the connection on HTTP long-polling instead
    # of upgrading it to a WebSocket. This is deliberate:
    #
    #   Werkzeug's development server cannot complete the WebSocket upgrade in
    #   this setup, and answers the upgrade request with HTTP 500. The app
    #   still works - the client just carries on polling - but every page load
    #   logs 500 errors from /socket.io/, which looks like a broken server and
    #   is very confusing to debug.
    #
    #   Long-polling delivers queue updates in well under a second, which is
    #   indistinguishable from a WebSocket for something like a song queue. So
    #   the upgrade is simply turned off rather than left to fail noisily.
    #
    #   A production deployment behind a real server (gunicorn with eventlet,
    #   or uvicorn) would set this back to True and get genuine WebSockets.
    socketio = SocketIO(
        app,
        async_mode=config.SOCKETIO_ASYNC_MODE,
        cors_allowed_origins="*",
        allow_upgrades=False,
        logger=False,
        engineio_logger=False,
    )
    init_socketio(socketio)

    return app, socketio


def port_already_serving(port: int) -> bool:
    """
    Is something already listening on this port?

    This check matters more than it looks. On Windows a second copy of this
    app can bind the same port WITHOUT any "address already in use" error,
    because of how socket reuse works there. Both processes then accept
    requests, and the browser's requests get split unpredictably between them.

    That breaks SocketIO specifically and confusingly: the connection is
    established with one process, but the follow-up polling requests may be
    answered by the other, which has never heard of that session and returns
    HTTP 500. The symptom is a flood of 500s from /socket.io/ while the rest
    of the site works perfectly - which is very hard to diagnose.

    So rather than let that happen, we check first and refuse to start.
    """
    import socket as socket_module

    probe = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    probe.settimeout(0.6)
    try:
        # connect_ex returns 0 only if something accepted the connection.
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


def main() -> None:
    if port_already_serving(config.PORT):
        print(
            f"\n  ERROR: something is already running on port {config.PORT}.\n\n"
            "  That is almost always an earlier copy of this app that did not\n"
            "  shut down - closing the terminal window does not always stop it.\n"
            "  Running two copies at once breaks the live queue with HTTP 500\n"
            "  errors from /socket.io/, so this one will not start.\n\n"
            "  Close the other one, or stop it with:\n"
            "      Windows :  taskkill /F /IM python.exe\n"
            "      Mac/Linux: pkill -f backend/app.py\n"
        )
        raise SystemExit(1)

    app, socketio = create_app()

    # The background thread that runs automatic word sync, one song at a
    # time. Started here rather than in create_app(), so building the app for
    # a test never starts minutes-long jobs.
    autosync_service.start()

    lan_url = f"http://{local_ip_address()}:{config.PORT}"
    print("\n" + "=" * 62)
    print("  KARAOKE SYSTEM")
    print("=" * 62)
    print(f"  Karaoke screen : http://localhost:{config.PORT}")
    print(f"  Sync editor    : http://localhost:{config.PORT}/sync")
    print(f"  Phones join at : {lan_url}/remote")
    print("=" * 62 + "\n")

    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
        # The reloader would start a second copy of the app in another
        # process, which breaks SocketIO's in-memory client list.
        use_reloader=False,
        allow_unsafe_werkzeug=True,  # Werkzeug's dev server is fine for a demo
    )


if __name__ == "__main__":
    main()
