"""Make repo-root imports work under plain `pytest` (the ./crystal launcher
normally injects PYTHONPATH; tests mirror that here)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)
