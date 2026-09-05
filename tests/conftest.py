"""Make repo-root imports work under plain `pytest` (the ./crystal launcher
normally injects PYTHONPATH; tests mirror that here)."""

import warnings

import pytest

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from crystalagent import paths as crystal_paths


def _missing_crystal_artifacts():
    return [
        path for path in (
            crystal_paths.ROM,
            crystal_paths.SYM,
            crystal_paths.CHARMAP,
            crystal_paths.MAP_CONSTANTS,
            crystal_paths.REPO_ROOT / "constants" / "battle_constants.asm",
        ) if not path.exists()
    ]


def pytest_configure(config):
    missing = _missing_crystal_artifacts()
    if missing:
        warnings.warn(
            pytest.PytestConfigWarning(
                "Crystal tests not collected: missing artifacts: "
                + ", ".join(map(str, missing))
            ),
            stacklevel=2,
        )


def pytest_ignore_collect(collection_path, config):
    # Crystal's state module parses the disassembly while importing; an absent
    # lane must be excluded before importing its tests, not by a later fixture.
    if any(collection_path.is_relative_to(REPO / "tests" / lane)
           for lane in ("unit", "integration")):
        return bool(_missing_crystal_artifacts())
