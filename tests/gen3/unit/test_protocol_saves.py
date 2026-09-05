"""ROM-free startup guards and on-disk autosave preservation."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from pokeagent import autopilot, paths, serve
from pokeagent.objective import Autosave

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("entrypoint", [serve.main, autopilot.main])
@pytest.mark.parametrize("requested,configured", [
    ("relative", "absolute"),
    ("parent", "relative"),
    ("file_symlink", "relative"),
    ("directory_symlink", "absolute"),
    ("absolute", "file_symlink"),
])
def test_default_guard_normalizes_both_paths_and_requires_opt_in(
    tmp_path, monkeypatch, entrypoint, requested, configured,
):
    saves = tmp_path / "saves"
    saves.mkdir()
    default = saves / "default.state"
    default.write_bytes(b"shared milestone")
    (saves / "nested").mkdir()
    alias = tmp_path / "alias.state"
    alias.symlink_to(default)
    directory_alias = tmp_path / "alias_saves"
    directory_alias.symlink_to(saves, target_is_directory=True)
    spellings = {
        "absolute": default,
        "relative": Path("saves/default.state"),
        "parent": Path("saves/nested/../default.state"),
        "file_symlink": alias,
        "directory_symlink": directory_alias / "default.state",
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "DEFAULT_STATE", spellings[configured])
    trek = ModuleType("pokeagent.trek")

    def driver_boundary(state):
        raise RuntimeError("driver boundary reached")

    trek.Driver = driver_boundary
    monkeypatch.setitem(sys.modules, "pokeagent.trek", trek)
    argv = ["--state", str(spellings[requested])]
    with pytest.raises(SystemExit):
        entrypoint(argv)
    with pytest.raises(RuntimeError, match="driver boundary reached"):
        entrypoint([*argv, "--allow-default"])
    assert default.read_bytes() == b"shared milestone"


@pytest.mark.parametrize("entrypoint", [serve.main, autopilot.main])
def test_independent_fork_does_not_require_default_opt_in(tmp_path, monkeypatch, entrypoint):
    monkeypatch.setattr(paths, "DEFAULT_STATE", tmp_path / "default.state")
    trek = ModuleType("pokeagent.trek")

    def driver_boundary(state):
        raise RuntimeError("driver boundary reached")

    trek.Driver = driver_boundary
    monkeypatch.setitem(sys.modules, "pokeagent.trek", trek)
    with pytest.raises(RuntimeError, match="driver boundary reached"):
        entrypoint(["--state", str(tmp_path / "mine.state")])


class SaveBoundary:
    def __init__(self):
        self.frame = 0
        self.fail = None

    def save_state(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.fail == "before":
            raise OSError("disk unavailable")
        path.write_bytes(f"state at {self.frame}".encode())
        if self.fail == "sidecar":
            raise OSError("sidecar unavailable")
        Path(f"{path}.meta").write_text(f"frame {self.frame}")
        return path


def autosave_boundary(tmp_path, emu=None):
    emu = emu or SaveBoundary()
    driver = SimpleNamespace(emu=emu, state_path=None)
    return Autosave(driver, session="run", saves_dir=tmp_path, every_frames=10, ring=2), emu


def test_permanent_autosave_survives_session_restart_with_sidecar(tmp_path):
    first, emu = autosave_boundary(tmp_path)
    path = Path(first.checkpoint("badge"))
    original = path.read_bytes(), Path(f"{path}.meta").read_bytes()
    # An older naming scheme is also history, not a scratch slot.
    legacy = tmp_path / "run-badge1.state"
    legacy.write_bytes(b"legacy milestone")
    restarted, _ = autosave_boundary(tmp_path, emu)
    emu.frame = 20
    next_path = Path(restarted.checkpoint("badge"))
    assert next_path != path
    assert next_path.read_bytes() == b"state at 20"
    assert (path.read_bytes(), Path(f"{path}.meta").read_bytes()) == original
    assert legacy.read_bytes() == b"legacy milestone"


@pytest.mark.parametrize("failure", ["before", "sidecar"])
def test_failed_checkpoint_is_not_claimed_and_can_be_retried(tmp_path, failure):
    autosave, emu = autosave_boundary(tmp_path)
    emu.fail = failure
    assert autosave.checkpoint("caught") is None
    assert autosave.stats()["saves"] == 0
    assert autosave.stats()["milestones"] == 0
    assert autosave.milestones == []
    emu.fail = None
    emu.frame = 30
    saved = Path(autosave.checkpoint("caught"))
    assert saved.read_bytes() == b"state at 30"
    assert Path(f"{saved}.meta").read_text() == "frame 30"
    assert autosave.stats()["saves"] == 1
    assert autosave.stats()["milestones"] == 1
    if failure == "sidecar":
        assert (tmp_path / "run-caught-1.state").read_bytes() == b"state at 0"
        assert saved != tmp_path / "run-caught-1.state"


def test_periodic_ring_still_overwrites_only_periodic_slots(tmp_path):
    autosave, emu = autosave_boundary(tmp_path)
    milestone = Path(autosave.checkpoint("badge"))
    written = []
    for frame in (10, 20, 30):
        emu.frame = frame
        written.extend(autosave._periodic())
    assert written[0] == written[2]
    assert written[0] != written[1]
    assert Path(written[0]).read_bytes() == b"state at 30"
    assert Path(written[1]).read_bytes() == b"state at 20"
    assert milestone.read_bytes() == b"state at 0"


def test_failed_periodic_save_remains_due(tmp_path):
    autosave, emu = autosave_boundary(tmp_path)
    emu.frame = 10
    emu.fail = "before"
    assert autosave._periodic() == []
    assert autosave.stats()["saves"] == 0
    emu.fail = None
    saved, = autosave._periodic()
    assert Path(saved).read_bytes() == b"state at 10"
    assert autosave._periodic() == []
