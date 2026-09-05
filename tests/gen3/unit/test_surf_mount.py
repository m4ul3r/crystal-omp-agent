"""You mount SURF from a shoreline, not off a cliff.

Route 119, standing at (21,46) on the elevation-4 plateau with the channel
three levels below: the planner offered a step north into the water as a
shortcut to the Weather Institute, and the walk answered "could not mount SURF
facing U" forty times without moving. The permissive seam rule that allowed it
was written to fix the opposite bug -- a surfer who could not step back onto
land -- so both directions are pinned here.

The wildcard elevations are the trap: Route 119's channel is elevation 0, and 0
matches anything, so a mount rule living inside the elevation branch is simply
skipped there. The guard has to stand on its own.
"""

import pytest

from pokeagent.nav import ELEVATION_ANY, MapData


class _Cell:
    def __init__(self, elevation, behavior=0x00, collision=0):
        self.elevation = elevation
        self.behavior = behavior
        self.collision = collision
        self.kind = "floor"

    @property
    def passable(self):
        return self.collision == 0


def _nav(surfing=True):
    nav = MapData.__new__(MapData)
    nav.surfing = surfing
    nav.waterfall = False
    return nav


WATER = _Cell(1)
WILDCARD_WATER = _Cell(0)
SHORE = _Cell(3)
CLIFF = _Cell(4)


def _seam(nav, here, there, z):
    """The mount/dismount decision on its own, without a grid."""
    nav._is_water = lambda c: c in (WATER, WILDCARD_WATER)
    return nav._surf_seam(here, there, z)


@pytest.mark.unit
def test_mounting_from_a_shore_is_allowed():
    nav = _nav()
    assert _seam(nav, SHORE, WATER, 3) is True


@pytest.mark.unit
def test_mounting_off_a_cliff_is_refused():
    """The Route 119 case: elevation 4 down to a channel."""
    nav = _nav()
    assert _seam(nav, CLIFF, WATER, 4) is False


@pytest.mark.unit
def test_dismounting_is_never_restricted():
    """A surfer takes the tile's own elevation, so stepping ashore is legal
    from any level -- refusing it stranded a probe on Route 117's pond with 46
    reachable cells in both modes."""
    nav = _nav()
    assert _seam(nav, WATER, SHORE, 1) is True
    assert _seam(nav, WATER, CLIFF, 1) is True


@pytest.mark.unit
def test_on_foot_no_seam_is_crossable():
    nav = _nav(surfing=False)
    assert _seam(nav, SHORE, WATER, 3) is False


@pytest.mark.unit
def test_zero_is_a_wildcard_elevation():
    """Which is exactly why the mount guard cannot live inside the elevation
    branch: Route 119's channel is elevation 0."""
    assert 0 in ELEVATION_ANY
    assert 15 in ELEVATION_ANY
