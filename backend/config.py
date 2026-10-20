"""
Central configuration for the karaoke app.

Everything that is a path or a tunable number lives here, so no other file has
to guess where things are. All paths are absolute and built from this file's
own location, which means the app works no matter what folder you launch it
from.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------
# __file__ is backend/config.py, so .parent is backend/ and .parent.parent is
# the project root. Deriving it this way (instead of hardcoding a path) keeps
# the project portable between computers.
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

FRONTEND_DIR = PROJECT_ROOT / "frontend"

# KARAOKE_DATA_DIR points the whole app at a different data folder. Normal use
# never sets it; the tests do, so they can run against a throwaway COPY of the
# library instead of your real songs.
DATA_DIR = Path(os.environ.get("KARAOKE_DATA_DIR") or PROJECT_ROOT / "data").resolve()
SCHEMA_PATH = BACKEND_DIR / "schema.sql"
DATABASE_PATH = DATA_DIR / "karaoke.db"

# The two media roots. They behave identically at runtime; the only difference
# is that samples/ is committed to git and library/ is ignored (see .gitignore).
SAMPLES_DIR = DATA_DIR / "samples"
LIBRARY_DIR = DATA_DIR / "library"

# Where scripts/import_songs.py looks for new material. Each entry is
# (audio folder, lyrics folder) and they are scanned in order.
MEDIA_SOURCES = [
    (SAMPLES_DIR / "audio", SAMPLES_DIR / "lyrics"),
    (LIBRARY_DIR / "audio", LIBRARY_DIR / "lyrics"),
]

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".wav"}

# Optional hand-written corrections for songs whose filenames do not follow the
# "Artist - Title" convention. The importer cannot tell "Artist - Title" from
# "Title - Artist" by itself, so this file lets you state the answer once and
# have it survive a database reset. See data/song_overrides.example.json.
OVERRIDES_PATH = DATA_DIR / "song_overrides.json"


# ---------------------------------------------------------------------------
# Automatic word sync (backend/utils/autosync.py)
# ---------------------------------------------------------------------------
# Original recordings WITH vocals, used only to time the words of a minus-one
# track - never played. Matched to a song by filename, the same way lyrics
# are. Under library/, so they are gitignored like the rest of your audio.
ORIGINALS_DIRS = [SAMPLES_DIR / "originals", LIBRARY_DIR / "originals"]

# Separated vocal stems (cached so Demucs never runs twice on the same file)
# and the job files passed to the worker. Also under library/: stems are
# made from your copyrighted audio, so they must never be committed either.
AUTOSYNC_CACHE_DIR = LIBRARY_DIR / "autosync_cache"

# The heavy machine-learning libraries (torch, Demucs, the alignment model)
# live in their own virtual environment, .venv-align, and the alignment runs
# as a separate process using that environment's Python. The karaoke app
# itself never imports them, so it keeps its small requirements.txt and still
# starts on a computer without them. KARAOKE_AUTOSYNC_PYTHON overrides it.
_VENV_PYTHON = (
    PROJECT_ROOT / ".venv-align" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)
AUTOSYNC_PYTHON = Path(os.environ.get("KARAOKE_AUTOSYNC_PYTHON") or _VENV_PYTHON)
AUTOSYNC_WORKER_SCRIPT = PROJECT_ROOT / "scripts" / "autosync_worker.py"

# How often the server's background worker looks for songs that
# scripts/import_songs.py queued while the server was busy or not running.
AUTOSYNC_POLL_SECONDS = 30


# ---------------------------------------------------------------------------
# Song codes
# ---------------------------------------------------------------------------
# Songs are numbered like a real karaoke machine. Starting at 1001 keeps every
# code the same width (4 digits), which makes the on-screen keypad predictable.
FIRST_SONG_CODE = 1001
CODE_WIDTH = 4


# ---------------------------------------------------------------------------
# Flask / SocketIO
# ---------------------------------------------------------------------------
# This key only signs session cookies for a local classroom demo. A real
# deployed app would load it from an environment variable instead of the
# source code - noted here because it is the kind of thing worth mentioning
# in the project writeup.
SECRET_KEY = "karaoke-school-project-dev-key"

# "threading" uses Python's own threads instead of eventlet or gevent. Those
# libraries are faster under heavy load but lag behind new Python releases and
# need C build tools on Windows. Threading mode has neither problem and is
# more than enough for a roomful of phones.
SOCKETIO_ASYNC_MODE = "threading"

HOST = "0.0.0.0"   # listen on every network interface so phones on the same
                   # Wi-Fi can reach the server, not just this computer
PORT = 5000
DEBUG = True
