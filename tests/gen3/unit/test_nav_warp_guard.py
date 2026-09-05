"""A warp tile is a destination, never a corridor.

Stepping onto a warp fires it. That is the game's rule, and a path that crosses
one does not go where it claims -- it ends at the warp's far side, on a
different map, mid-route.

Granite Cave is the worked example and the reason this file exists. The player
stood on 1F at (17,12) with a warp down to B1F at (17,11) directly above, and
the plain BFS opened its route to the Route 106 exit with `U`. That step fired
the warp, the run landed on B1F, planned its way back up, and oscillated
between two floors indefinitely -- burning encounters and frames, MOVING the
whole time, so a watchdog looking for a stopped run could not see it.

The BFS is shared, so this bug was one map away from being every map.
"""

import pytest

from pokeagent import nav as navmod

pytestmark = pytest.mark.unit


class Cell:
    def __init__(self, passable=True, elevation=3):
        self.passable = passable
        self.elevation = elevation
        self.collision = 0 if passable else 1


class Warp:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Info:
    def __init__(self, warps):
        self.warps = [Warp(*w) for w in warps]


class FakeNav(navmod.MapData):
    """A 5x5 open room with warps where the test puts them."""

    def __init__(self, warps=(), walls=()):
        self._info = Info(warps)
        self._walls = set(walls)

    def info(self, map_name):
        return self._info

    def cell(self, map_name, x, y):
        if not (0 <= x < 5 and 0 <= y < 5) or (x, y) in self._walls:
            return None
        return Cell()

    def elevation_at(self, map_name, x, y):
        return 3

    def step(self, map_name, x, y, z, d):
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[d]
        nx, ny = x + dx, y + dy
        return None if self.cell(map_name, nx, ny) is None else (nx, ny, 3)


def walk(start, path):
    x, y = start
    cells = []
    for move in path:
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[move]
        x, y = x + dx, y + dy
        cells.append((x, y))
    return cells


def test_a_path_never_crosses_a_warp():
    """The Granite Cave shape: a warp sits between the player and the goal."""
    nav = FakeNav(warps=[(2, 1)])
    path = nav.find_path("M", (2, 2), (2, 0))
    assert path, "a route around the warp exists and must be found"
    assert (2, 1) not in walk((2, 2), path), f"path crossed the warp: {path}"
    assert walk((2, 2), path)[-1] == (2, 0)


def test_the_shortest_route_is_still_taken_when_it_is_clear():
    """The guard must not tax ordinary paths."""
    nav = FakeNav(warps=[(0, 0)])
    path = nav.find_path("M", (2, 2), (2, 0))
    assert path == ["U", "U"]


def test_a_warp_can_still_be_the_goal():
    """Routing TO a door is the single most common thing the driver does. If
    the guard blocked the goal as well, nothing could ever be entered."""
    nav = FakeNav(warps=[(2, 0)])
    path = nav.find_path("M", (2, 2), (2, 0))
    assert path == ["U", "U"]


def test_standing_on_a_warp_does_not_trap_the_player():
    """Every door arrival leaves the player standing on a warp -- gotcha 15 --
    so the start cell must be exempt or the run would be stuck the moment it
    walked through any door."""
    nav = FakeNav(warps=[(2, 2)])
    path = nav.find_path("M", (2, 2), (0, 0))
    assert path, "a player standing on a warp must still be able to leave"
    assert walk((2, 2), path)[-1] == (0, 0)


def test_no_route_is_honestly_no_route():
    """A goal walled off behind warps returns None rather than a path that
    silently teleports. A wrong answer here costs a run; None costs a replan."""
    nav = FakeNav(warps=[(1, 0), (0, 1)])
    assert nav.find_path("M", (2, 2), (0, 0)) is None


def test_warp_cells_are_collected_once():
    nav = FakeNav(warps=[(1, 1), (3, 3)])
    assert nav.warp_cells("M") == {(1, 1), (3, 3)}
    assert nav.warp_cells("M") is nav.warp_cells("M"), "should be memoised"
