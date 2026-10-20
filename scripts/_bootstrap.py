"""
Shared setup for the command-line scripts in this folder.

The backend modules import each other as `from config import ...` rather than
`from backend.config import ...`, which means backend/ has to be on Python's
import path. Scripts live outside that folder, so they import this module
first to put it there.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
