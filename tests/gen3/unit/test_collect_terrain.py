"""Water encounters need water cells, not grass ones.

The collector's pacer only ever looked for grass. Sent to `Underwater2` to
close CHINCHOU, CLAMPERL and RELICANTH it logged "no reachable grass on
Underwater2" and left -- and there is no grass anywhere underwater, so every
surf and dive species in the game was uncatchable by this collector.

Measured on the live save at Underwater2 (32,9): 0 reachable grass cells,
**569** reachable water cells.
"""

import pytest

from scripts.collect import Collector


class FakeNav:
    def __init__(self, tiles):
        self.tiles = tiles
        self.surfing = False
        self.asked = []

    def find_tiles(self, map_name, kind):
        self.asked.append(kind)
        return list(self.tiles.get(kind, ()))

    def reachable(self, map_name, pos, elevation):
        # Everything except (99,99), which stands in for a walled-off cell.
        return [c for group in self.tiles.values() for c in group
                if c != (99, 99)]


class FakeDriver:
    def __init__(self, tiles):
        self.nav = FakeNav(tiles)

    def map_name(self):
        return "Underwater2"

    def pos(self):
        return (0, 0)

    def elevation(self):
        return 3


def _collector(tiles):
    c = Collector.__new__(Collector)
    c.d = FakeDriver(tiles)
    return c


@pytest.mark.unit
def test_water_terrain_finds_water_cells():
    c = _collector({"water": [(2, 0), (1, 0)], "grass": []})
    assert c.terrain_cells("water") == [(1, 0), (2, 0)]
    assert c.d.nav.asked == ["water"]


@pytest.mark.unit
def test_grass_is_still_the_default():
    """The land path must be untouched by the water change."""
    c = _collector({"grass": [(3, 0)], "water": [(1, 0)]})
    assert c.terrain_cells() == [(3, 0)]
    assert c.grass_cells() == [(3, 0)]


@pytest.mark.unit
def test_cells_are_nearest_first():
    """Each leg should cross terrain rather than shuffle, so ordering matters."""
    c = _collector({"water": [(5, 5), (1, 1), (3, 3)]})
    assert c.terrain_cells("water") == [(1, 1), (3, 3), (5, 5)]


@pytest.mark.unit
def test_unreachable_cells_are_excluded():
    """A tile of the right kind that cannot be walked to is not a target."""
    c = _collector({"water": [(1, 0), (99, 99)]})
    assert c.terrain_cells("water") == [(1, 0)]


@pytest.mark.unit
def test_a_map_with_no_such_terrain_is_empty_not_an_error():
    """Underwater2 has no grass at all -- the honest answer is [] so the
    caller can move on, rather than an exception that kills the sweep."""
    c = _collector({"water": [(1, 0)]})
    assert c.terrain_cells("grass") == []


@pytest.mark.unit
def test_a_broken_nav_degrades_to_no_cells():
    c = _collector({})

    def boom(*a, **k):
        raise RuntimeError("no decoded grid")

    c.d.nav.find_tiles = boom
    assert c.terrain_cells("water") == []


@pytest.mark.unit
def test_dive_counts_as_water():
    """The dex renames a water table on an Underwater* map to 'dive'
    (dex.py:603). Checking for 'water' alone sent the collector to
    Underwater1/2 four times, each reporting 'no reachable grass' -- and
    CHINCHOU, CLAMPERL and RELICANTH only live there.
    """
    from scripts.collect import WATER_KINDS

    assert {"dive"} & WATER_KINDS
    assert {"water"} & WATER_KINDS
    assert not ({"land"} & WATER_KINDS)
