"""Emulator-in-the-loop fixtures.

Every scenario forks its checkpoint into a temp directory and drives the
fork, so a test can never mutate a milestone. The predecessor project made
that mistake's inverse -- its integration lane was believed unrunnable for a
whole session because the milestone directory was hardcoded -- so the search
path here is explicit and the skip message names what is missing.
"""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pokeagent import paths  # noqa: E402

#: Checkpoints produced by scripts/newgame.py and scripts/to_starter.py.
MILESTONES = ("littleroot", "route101", "starter", "first-battle", "lab")


def _state(name):
    return paths.SAVES_DIR / f"{name}.state"


@pytest.fixture(scope="session")
def milestones():
    absent = [n for n in MILESTONES if not _state(n).exists()]
    if absent:
        pytest.skip(
            "missing checkpoints: "
            + ", ".join(absent)
            + " -- run scripts/newgame.py then scripts/to_starter.py"
        )
    return {n: _state(n) for n in MILESTONES}


@pytest.fixture
def optional_fork(tmp_path):
    """Fork a checkpoint that MAY not exist; skip the test when it does not.

    Late-game states are not in a fresh checkout, and a lane that ERRORS on a
    missing file gets read as "cannot run here" -- which is exactly how this
    project's integration lane was believed unrunnable for sixteen sessions
    (journal #53). Absent means skip, loudly and by name.
    """
    from pokeagent.trek import Driver

    def _fork(name):
        src = _state(name)
        if not src.exists():
            pytest.skip(f"optional checkpoint {name} is not in this checkout")
        dst = tmp_path / src.name
        shutil.copy2(src, dst)
        meta = Path(str(src) + ".meta")
        if meta.exists():
            shutil.copy2(meta, str(dst) + ".meta")
        return Driver(dst)

    return _fork


@pytest.fixture
def fork(milestones, tmp_path):
    """Copy a milestone (and its .meta) into tmp and open a Driver on it."""
    from pokeagent.trek import Driver

    def _fork(name):
        src = milestones[name]
        dst = tmp_path / src.name
        shutil.copy2(src, dst)
        meta = Path(str(src) + ".meta")
        if meta.exists():
            shutil.copy2(meta, str(dst) + ".meta")
        return Driver(dst)

    return _fork


@pytest.fixture(scope="session", autouse=True)
def _milestones_unchanged(milestones):
    """Fail the session if a test wrote to a checkpoint."""
    import hashlib

    def digest():
        return {
            n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in milestones.items()
        }

    before = digest()
    yield
    after = digest()
    changed = [n for n in before if before[n] != after[n]]
    assert not changed, f"integration tests mutated milestones: {changed}"
