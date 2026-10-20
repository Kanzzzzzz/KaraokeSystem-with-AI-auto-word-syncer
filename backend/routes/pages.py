"""
Serves the HTML pages and the join-by-QR helper.

Three pages:
    /        the karaoke machine - player, lyrics, scoring, host controls
    /sync    the LRC sync editor
    /remote  the phone view for searching and joining the queue
"""

import io
import socket

from flask import Blueprint, jsonify, request, send_file, send_from_directory

from config import FRONTEND_DIR, PORT

pages_bp = Blueprint("pages", __name__)


def local_ip_address() -> str:
    """
    Find this computer's address on the local network, e.g. "192.168.1.14".

    The trick: open a UDP socket toward an outside address and ask which of
    our own interfaces it would use. Nothing is actually sent (UDP is
    connectionless), but the OS has to pick a route, which tells us the LAN
    address phones should connect to. socket.gethostname() is unreliable here
    because it often resolves to 127.0.0.1.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        # No network at all - the QR code will point at localhost, which still
        # works for a single-computer demo.
        return "127.0.0.1"
    finally:
        probe.close()


@pages_bp.route("/")
def karaoke_screen():
    return send_from_directory(FRONTEND_DIR, "index.html")


@pages_bp.route("/sync")
def sync_editor():
    return send_from_directory(FRONTEND_DIR, "sync.html")


@pages_bp.route("/remote")
def phone_remote():
    return send_from_directory(FRONTEND_DIR, "remote.html")


@pages_bp.route("/api/join-info")
def join_info():
    """The URL phones should open, so the host screen can display it."""
    return jsonify({"url": f"http://{local_ip_address()}:{PORT}/remote"})


@pages_bp.route("/api/join-qr.png")
def join_qr():
    """
    A QR code image of the join URL.

    Generated on the fly rather than saved to disk, because the LAN address
    changes whenever the computer joins a different network.
    """
    try:
        import qrcode
    except ImportError:
        return jsonify({"error": "qrcode package is not installed"}), 501

    target = request.args.get("url") or f"http://{local_ip_address()}:{PORT}/remote"

    image = qrcode.make(target)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")
