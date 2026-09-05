"""The boulder solver's wall set must honour elevation, not just collision.

Collision-only walls let it plan across seams both `nav.step` and the engine
refuse, and that single omission is what made Victory Road look impossible.
On 1F the row y=25 is elevation 15 (a bridge, carrying whatever level you
arrive with) flanked by elevation-4 cells, while y=24 above is elevation 3.
Arriving from the 4 side you cross the bridge still carrying 4, and 4 -> 3 is
illegal -- the engine refused (7,24), (8,24) and (9,24) every time.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import boulder_solver as bs  # noqa: E402


class _Cell:
    def __init__(self, elevation, collision=0, behavior=0x8):
        self.elevation = elevation
        self.collision = collision
        self.behavior = behavior
        self.kind = "floor"


class _Nav:
    def __init__(self, grid):
        self._grid = grid

    def grid(self, _name):
        return self._grid

    def info(self, _name):
        return type("I", (), {"objects": []})()


class _Driver:
    """The three things `snapshot` asks for."""

    def __init__(self, grid, z):
        self.nav = _Nav(grid)
        self._z = z

    def map_name(self):
        return "VictoryRoad_1F"

    def elevation(self):
        return self._z

    def live_npcs(self):
        return []


def _victory_road_slice():
    """A 3x3 lift of the real barrier: e3 row, e15 bridge, e4 approach."""
    return [
        [_Cell(3), _Cell(3), _Cell(3)],      # y=0  (the y=24 row)
        [_Cell(4), _Cell(15), _Cell(4)],     # y=1  (the y=25 bridge)
        [_Cell(4), _Cell(4), _Cell(4)],      # y=2
    ]


@pytest.mark.unit
def test_carrying_level_4_walls_off_the_level_3_row():
    walls, _b, _o, _r, _e = bs.snapshot(_Driver(_victory_road_slice(), 4), elevation_filter=True)
    assert (0, 0) in walls and (1, 0) in walls and (2, 0) in walls, \
        "an e3 row is unreachable while carrying e4"
    assert (1, 1) not in walls, "the e15 bridge is always passable"
    assert (0, 2) not in walls, "our own level stays passable"


@pytest.mark.unit
def test_carrying_level_3_opens_that_row():
    walls, _b, _o, _r, _e = bs.snapshot(_Driver(_victory_road_slice(), 3), elevation_filter=True)
    assert (0, 0) not in walls and (1, 0) not in walls
    assert (0, 1) in walls, "the e4 cells are now the unreachable ones"
    assert (1, 1) not in walls, "the bridge is still passable"


@pytest.mark.unit
def test_collision_still_walls_regardless_of_elevation():
    grid = [[_Cell(3, collision=1), _Cell(3)]]
    walls, _b, _o, _r, _e = bs.snapshot(_Driver(grid, 3), elevation_filter=True)
    assert (0, 0) in walls and (1, 0) not in walls


@pytest.mark.unit
def test_unknown_elevation_does_not_wall_the_world():
    """z == 0 is the wildcard; it must not filter anything out."""
    walls, _b, _o, _r, _e = bs.snapshot(_Driver(_victory_road_slice(), 0), elevation_filter=True)
    assert walls == set()


@pytest.mark.unit
def test_the_filter_is_off_by_default():
    """B1F's verified route to (30,25) changes level through a wildcard tile,
    and a static filter keyed on the CURRENT level forbids that -- switching
    this on by default traded a working crossing for a correct 1F model."""
    walls, _b, _o, _r, _e = bs.snapshot(_Driver(_victory_road_slice(), 4))
    assert (0, 0) not in walls, "no elevation filtering unless asked"
