"""Surfing changes which cells you may occupy, not whether collision applies.

`Cell.passable` IS `collision == 0`, so the surf override in `step()` only ever
ran for cells the collision bits refuse -- and it let the planner swim into rock.

Measured on Route 122, sitting on water at (8,10): the planned 44-step route to
Mt Pyre's door opened with D into (8,11), which is water with collision=1. The
engine refused it every time -- `step_dir('D')` returned False without moving --
so `goto` logged "stalled 12x at (8, 10)" and the badge-7 chain declared the
door unreachable while sitting 44 steps away from it.

`GetCollisionAtCoords` checks collision for everyone, surfing or not.
"""

import pytest

from pokeagent.nav import Cell

pytestmark = pytest.mark.unit

WATER = 0x15          # a surfable behavior
LAND = 0x00


def _cell(collision=0, behavior=LAND, elevation=3):
    return Cell(metatile=0, collision=collision, elevation=elevation,
                behavior=behavior, kind="water" if behavior == WATER else "floor")


class _Nav:
    """A REAL MapData with a synthetic grid.

    Built with `__new__` on purpose: the class carries defaults for exactly
    this ("Test fakes ... skip __init__ entirely"), so every rule in `step`
    runs for real instead of against a hand-rolled imitation that can drift
    from it -- which is how the bug under test hid in the first place.
    """

    def __new__(cls, cells, surfing=True):
        from pokeagent.behaviors import Behaviors
        from pokeagent.nav import MapData

        nav = MapData.__new__(MapData)
        nav.beh = Behaviors()
        nav.surfing = surfing
        nav.waterfall = False
        nav.cell = lambda _map, x, y: cells.get((x, y))
        return nav


def test_blocked_water_is_refused_while_surfing():
    """The exact Route 122 cell: water, collision 1."""
    nav = _Nav({
        (8, 10): _cell(0, WATER, 1),
        (8, 11): _cell(1, WATER, 0),
    })
    assert nav.step("Route122", 8, 10, 1, "D") is None


def test_open_water_is_still_a_road():
    nav = _Nav({
        (8, 10): _cell(0, WATER, 1),
        (7, 10): _cell(0, WATER, 1),
    })
    assert nav.step("Route122", 8, 10, 1, "L") == (7, 10, 1)


def test_dismounting_onto_open_shore_still_works():
    """The move the whole Mt Pyre approach depends on."""
    nav = _Nav({
        (22, 32): _cell(0, WATER, 1),
        (22, 31): _cell(0, LAND, 3),
    })
    assert nav.step("Route122", 22, 32, 1, "U") == (22, 31, 3)


def test_blocked_land_is_refused_on_foot_too():
    nav = _Nav({(5, 5): _cell(0), (5, 4): _cell(1)}, surfing=False)
    assert nav.step("Route101", 5, 5, 3, "U") is None


def test_blocked_shore_is_refused_when_dismounting():
    """A surfer may not ride onto a wall just because it is land."""
    nav = _Nav({
        (22, 32): _cell(0, WATER, 1),
        (22, 31): _cell(1, LAND, 3),
    })
    assert nav.step("Route122", 22, 32, 1, "U") is None
