"""Driver.save target resolution: bare milestone names vs path-like names."""
from pathlib import Path

import pytest

import crystalagent.paths as paths
from crystalagent.driver import Driver

pytestmark = pytest.mark.unit


def test_bare_name_lands_in_saves(monkeypatch):
    monkeypatch.setattr(paths, "SAVES_DIR", "saves")
    assert Driver._save_target(
        Path("w.state"), "pre-rival.state") == Path("saves/pre-rival.state")


def test_pathlike_name_honored_verbatim():
    assert Driver._save_target(
        Path("w.state"), "omp_saves/x.state") == Path("omp_saves/x.state")
    assert Driver._save_target(
        Path("w.state"), "/tmp/abs/x.state") == Path("/tmp/abs/x.state")


def test_no_name_returns_working_path():
    assert Driver._save_target(Path("w.state"), None) == Path("w.state")
