import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crystalagent.emu as emu_module
from crystalagent.driver import Driver
from crystalagent.emu import Crystal

pytestmark = pytest.mark.unit


class FakePyBoy:
    instances = []

    def __init__(self, *args, **kwargs):
        self.frame_count = 7
        self.loaded = []
        self.released = []
        type(self).instances.append(self)

    def load_state(self, stream):
        payload = stream.read()
        self.loaded.append(payload)
        if payload.startswith(b"frame:"):
            self.frame_count = int(payload.split(b":", 1)[1])

    def button_release(self, button):
        self.released.append(button)

    def tick(self, frames, render):
        self.frame_count += frames


@pytest.fixture
def crystal():
    result = Crystal.__new__(Crystal)
    result.py = FakePyBoy()
    result._pyboy_version = "2.4.0"
    result._rom_sha256 = "rom-hash"
    result._base_frames = 0
    result._start_count = result.py.frame_count
    result._observer = None
    return result


def state_file(tmp_path, name="checkpoint.state", frame=23, meta=None):
    path = tmp_path / name
    path.write_bytes(f"frame:{frame}".encode())
    if meta is not None:
        Path(f"{path}.meta").write_text(json.dumps(meta))
    return path


def test_load_validates_matching_provenance_and_rebases_frames(crystal, tmp_path):
    path = state_file(tmp_path, meta={
        "frames": 900,
        "pyboy": "2.4.0",
        "rom_sha256": "rom-hash",
    })

    assert crystal.load(path) == path
    assert crystal.py.loaded == [b"frame:23"]
    assert crystal.frame == 900
    crystal.tick(4)
    assert crystal.frame == 904


def test_load_missing_stamps_warns_but_loads(crystal, tmp_path, capsys):
    path = state_file(tmp_path, meta={"frames": 40})

    crystal.load(path)

    assert crystal.py.loaded == [b"frame:23"]
    assert "lacks provenance stamps (pyboy, rom_sha256)" in capsys.readouterr().err


def test_load_without_sidecar_warns_and_starts_at_zero(crystal, tmp_path, capsys):
    path = state_file(tmp_path)

    crystal.load(path)

    assert crystal.frame == 0
    assert f"{path.name}.meta lacks provenance stamps" in capsys.readouterr().err


def test_malformed_sidecar_fails_before_pyboy_load(crystal, tmp_path):
    path = state_file(tmp_path)
    Path(f"{path}.meta").write_text("not json")

    with pytest.raises(json.JSONDecodeError):
        crystal.load(path)

    assert crystal.py.loaded == []


@pytest.mark.parametrize("field,value", [
    ("pyboy", "wrong-version"),
    ("rom_sha256", "wrong-rom"),
])
def test_provenance_mismatch_fails_before_pyboy_load(
        crystal, tmp_path, field, value):
    stamps = {"frames": 100, "pyboy": "2.4.0", "rom_sha256": "rom-hash"}
    stamps[field] = value
    path = state_file(tmp_path, meta=stamps)

    with pytest.raises(RuntimeError, match="refusing to load"):
        crystal.load(path)

    assert crystal.py.loaded == []
    assert crystal._base_frames == 0


def test_constructor_delegates_to_load(monkeypatch, tmp_path):
    FakePyBoy.instances.clear()
    monkeypatch.setitem(sys.modules, "pyboy", SimpleNamespace(PyBoy=FakePyBoy))
    monkeypatch.setattr(emu_module, "_pyboy_version", lambda: "2.4.0")
    monkeypatch.setattr(emu_module, "_rom_digest", lambda path: "rom-hash")
    path = state_file(tmp_path, meta={
        "frames": 120,
        "pyboy": "2.4.0",
        "rom_sha256": "rom-hash",
    })

    loaded = Crystal(tmp_path / "rom.gbc", object(), object(), path)

    assert loaded.py.loaded == [b"frame:23"]
    assert loaded.frame == 120


def test_release_buttons_releases_each_button_once_then_settles(crystal):
    crystal.release_buttons(settle_frames=10)

    assert crystal.py.released == [
        "up", "down", "left", "right", "a", "b", "start", "select"
    ]
    assert crystal.frame == 10


def test_repeated_loads_keep_cumulative_frame_semantics(crystal, tmp_path):
    first = state_file(tmp_path, "first.state", frame=30, meta={
        "frames": 100, "pyboy": "2.4.0", "rom_sha256": "rom-hash"
    })
    second = state_file(tmp_path, "second.state", frame=80, meta={
        "frames": 500, "pyboy": "2.4.0", "rom_sha256": "rom-hash"
    })

    crystal.load(first)
    crystal.tick(5)
    assert crystal.frame == 105
    crystal.load(second)
    crystal.tick(9)
    assert crystal.frame == 509


def test_driver_load_updates_path_only_after_success(tmp_path):
    driver = Driver.__new__(Driver)
    old = tmp_path / "old.state"
    new = tmp_path / "new.state"
    driver.state_path = old
    releases = []

    class FakeEmu:
        def load(self, path):
            raise RuntimeError("bad provenance")

        def release_buttons(self, settle_frames=0):
            releases.append(settle_frames)

    driver.emu = FakeEmu()
    with pytest.raises(RuntimeError, match="bad provenance"):
        driver._load_state(new)
    assert driver.state_path == old
    assert releases == []

    driver.emu.load = lambda path: Path(path)
    assert driver._load_state(new) == new
    assert driver.state_path == new
    assert releases == [10]
