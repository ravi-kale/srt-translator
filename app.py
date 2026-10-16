"""Vercel entrypoint: serves the FastAPI app defined in backend/main.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from main import app  # noqa: E402,F401
