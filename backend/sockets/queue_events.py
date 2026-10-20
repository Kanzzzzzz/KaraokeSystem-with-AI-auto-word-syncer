"""
Real-time queue updates over SocketIO.

The pattern here: HTTP routes change the database, then call one of the
broadcast_* functions below to push the new state to every connected client.
Clients therefore never have to poll - they update the moment anything
changes, on the host screen and on every phone at once.

Events sent to clients:
    queue:updated       the whole queue changed
    player:now_playing  a different song started

Events received from clients:
    connect             a new screen or phone joined - send it current state
"""

from flask import request
from flask_socketio import SocketIO

from models import queue as queue_model
from models import session as session_model

# Filled in by init_socketio() during app startup. Keeping it module-level
# lets the HTTP routes broadcast without having to pass the SocketIO object
# around through every function call.
socketio: SocketIO | None = None


def init_socketio(sio: SocketIO) -> None:
    """Store the SocketIO instance and register the client event handlers."""
    global socketio
    socketio = sio

    @sio.on("connect")
    def handle_connect(auth=None):
        """
        Send a newly connected client the current state immediately.

        Without this, a phone that joins mid-session would see an empty queue
        until the next time somebody changed something.
        """
        # Imported here rather than at module level to avoid a circular import:
        # database imports config, and app imports this module.
        from database import connect

        conn = connect()
        try:
            session = session_model.get_or_create_active(conn)
            conn.commit()
            # to=request.sid sends this to the client that just connected
            # ONLY. (It used to be to=None, which broadcasts: every phone
            # that joined made every screen redraw its queue for nothing.)
            sio.emit("queue:updated", {
                "session_id": session["id"],
                "queue": queue_model.list_queue(conn, session["id"]),
                "now_playing": queue_model.get_now_playing(conn, session["id"]),
            }, to=request.sid)
        finally:
            conn.close()


def broadcast_queue(conn, session_id: int) -> None:
    """Push the current queue to every connected client."""
    if socketio is None:
        return  # SocketIO not initialised (e.g. running under a unit test)

    socketio.emit("queue:updated", {
        "session_id": session_id,
        "queue": queue_model.list_queue(conn, session_id),
        "now_playing": queue_model.get_now_playing(conn, session_id),
    })


def broadcast_now_playing(entry: dict | None) -> None:
    """
    Announce the song that just started, or that the queue has run out.

    Sent separately from queue:updated because clients react differently: the
    player loads new audio, while a phone only refreshes its list.
    """
    if socketio is None:
        return

    socketio.emit("player:now_playing", {"entry": entry})


def broadcast_score(score: dict) -> None:
    """Announce a finished performance so leaderboards refresh everywhere."""
    if socketio is None:
        return

    socketio.emit("score:recorded", {"score": score})
