"""Cells the engine refuses must be remembered across runs.

Victory Road refuses a whole class of ordinary steps the decoded grid calls
open -- (7,25), (8,25), (9,25), (9,26), (16,34), (16,35) on 1F alone, none of
them holding an object. In-memory only, each crossing spent its budget
rediscovering the same walls and never got far enough to use the knowledge.
"""

import json

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import boulder_solver as bs  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_WALLS_PATH", tmp_path / "learned_walls.json")
    monkeypatch.setattr(bs, "_LEARNED_WALLS", {})
    monkeypatch.setattr(bs, "_WALL_HITS", {})
    yield


def _confirm(map_name, cell):
    """Refuse a cell enough times for the solver to believe it."""
    for _ in range(bs.WALL_CONFIRMATIONS):
        bs.note_wall(map_name, cell)


@pytest.mark.unit
def test_a_refusal_is_remembered_for_that_map_only():
    _confirm("VictoryRoad_1F", (7, 25))
    assert (7, 25) in bs.learned_walls("VictoryRoad_1F")
    assert (7, 25) not in bs.learned_walls("VictoryRoad_B1F")


@pytest.mark.unit
def test_walls_survive_a_fresh_process(monkeypatch):
    _confirm("VictoryRoad_1F", (16, 35))
    _confirm("VictoryRoad_1F", (16, 34))
    # A new run starts with an empty cache and must reload from disk.
    monkeypatch.setattr(bs, "_LEARNED_WALLS", {})
    reloaded = bs.learned_walls("VictoryRoad_1F")
    assert (16, 35) in reloaded and (16, 34) in reloaded


@pytest.mark.unit
def test_the_file_is_valid_json_and_grows(monkeypatch):
    _confirm("Map", (1, 2))
    first = json.loads(bs._WALLS_PATH.read_text())
    assert first["walls"]["Map"] == [[1, 2]]
    _confirm("Map", (3, 4))
    second = json.loads(bs._WALLS_PATH.read_text())
    assert sorted(second["walls"]["Map"]) == [[1, 2], [3, 4]]


@pytest.mark.unit
def test_missing_file_is_not_an_error():
    assert bs.learned_walls("NeverSeen") == frozenset()


@pytest.mark.unit
def test_a_refused_push_is_not_a_wall(monkeypatch):
    """"Cannot push a boulder onto (4,8)" and "cannot walk on (4,8)" are
    different facts. Conflating them made Victory Road B1F's alcove at (4,6)
    unsolvable for WALKING too, which killed an otherwise-fine 83-move plan."""
    monkeypatch.setattr(bs, "_NO_PUSH", {})
    bs.note_no_push("VictoryRoad_B1F", (4, 8))
    assert (4, 8) in bs.learned_no_push("VictoryRoad_B1F")
    assert (4, 8) not in bs.learned_walls("VictoryRoad_B1F")


@pytest.mark.unit
def test_one_refusal_is_not_believed():
    """Three transient causes have masqueraded as terrain -- a body on the
    tile, an unmounted water tile, a scene owning input -- and each killed a
    crossing that had already been proven. One sighting is not evidence."""
    bs.note_wall("VictoryRoad_B1F", (25, 10))
    assert (25, 10) not in bs.learned_walls("VictoryRoad_B1F")
    assert not bs._WALLS_PATH.exists(), "an unconfirmed guess must not persist"

    for _ in range(bs.WALL_CONFIRMATIONS - 1):
        bs.note_wall("VictoryRoad_B1F", (25, 10))
    assert (25, 10) in bs.learned_walls("VictoryRoad_B1F")
