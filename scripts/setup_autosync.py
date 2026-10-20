"""
Install everything automatic word sync needs, in one step.

    python scripts/setup_autosync.py

  1. creates the .venv-align virtual environment (if it does not exist)
  2. installs PyTorch (CPU build) and requirements-autosync.txt into it
  3. downloads the three models, so the first auto sync does not stall:
       MMS forced aligner   ~1.2 GB   the alignment model
       htdemucs            ~80 MB    vocal isolation
       Whisper small       ~460 MB   only for songs with no lyrics
  4. checks that everything loads

Safe to run again: anything already installed or downloaded is skipped.
Everything goes into .venv-align and your user cache folder; the karaoke
app's own environment is not touched.
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV = PROJECT_ROOT / ".venv-align"
VENV_PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
REQUIREMENTS = PROJECT_ROOT / "requirements-autosync.txt"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

DOWNLOAD_MODELS = """
from huggingface_hub import snapshot_download
print('  MMS forced aligner...', flush=True)
# Only the safetensors weights: the repository also has the same weights in
# the older .bin format, which would be another 1.2 GB for nothing.
snapshot_download('MahmoudAshraf/mms-300m-1130-forced-aligner',
                  ignore_patterns=['*.bin'])
print('  htdemucs...', flush=True)
from demucs.pretrained import get_model
get_model('htdemucs')
print('  Whisper small...', flush=True)
import whisper
whisper.load_model('small')
"""

SELF_CHECK = """
import sys
sys.path.insert(0, r'{backend}')
import torch, numpy
from autosync_engine import ctc_align
model = ctc_align.LetterModel()
silence = numpy.zeros(16000, dtype=numpy.float32)
frames = model.log_probs(silence)
print(f'  torch {{torch.__version__}}, alignment model OK ({{frames.shape[0]}} frames per second)')
"""


def run(command: list[str]) -> None:
    print("  $ " + " ".join(str(part) for part in command))
    subprocess.run([str(part) for part in command], check=True)


def find_python_for_venv() -> list[str]:
    """A Python between 3.10 and 3.13 - PyTorch does not support every new version at once."""
    if (3, 10) <= sys.version_info[:2] <= (3, 13):
        return [sys.executable]
    if shutil.which("py"):   # the Windows Python launcher
        for version in ("3.12", "3.13", "3.11", "3.10"):
            if subprocess.run(["py", f"-{version}", "--version"], capture_output=True).returncode == 0:
                return ["py", f"-{version}"]
    sys.exit("Please install Python 3.12 (python.org) and run this again.")


def main() -> None:
    print("1. Virtual environment")
    if VENV_PYTHON.is_file():
        print(f"  {VENV} already exists")
    else:
        run(find_python_for_venv() + ["-m", "venv", VENV])

    print("\n2. Libraries (a few minutes; PyTorch alone is ~200 MB)")
    run([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "pip"])
    run([VENV_PYTHON, "-m", "pip", "install", "torch==2.14.0", "--index-url", TORCH_CPU_INDEX])
    run([VENV_PYTHON, "-m", "pip", "install", "-r", REQUIREMENTS])

    print("\n3. Models (about 1.8 GB the first time)")
    run([VENV_PYTHON, "-c", DOWNLOAD_MODELS])

    print("\n4. Self-check")
    run([VENV_PYTHON, "-c", SELF_CHECK.format(backend=PROJECT_ROOT / "backend")])

    print("\nAuto sync is ready. Try:  python scripts/auto_sync.py --song <code>")


if __name__ == "__main__":
    main()
