"""The map the game is walking on, not the map that shipped.

`.blk` files describe a map as authored. Switch puzzles rewrite it at runtime:
Mauville's gym raises and lowers electric barriers, and against the static grid
alone the pathfinder sees Wattson in a component it cannot enter and reports --
correctly -- that there is no route. Nothing is broken; the map has simply
changed and nobody read it back.

Crystal solved this by reading the live block map out of WRAM. These cover the
Gen-3 half: decoding `gBackupMapLayout` entries and letting them override the
shipped grid.
"""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from pokeagent import nav as navmod
from pokeagent.trek import Driver

pytestmark = pytest.mark.unit


def test_a_grid_entry_splits_into_metatile_collision_and_elevation():
    """Bit layout from global.fieldmap.h:7-9. Read from the header, but every
    field is given a distinct value so a shifted mask cannot pass."""
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[]]
    nav._behaviour_of = lambda m, mid: 0x11
    nav.beh = type("B", (), {"kind": staticmethod(lambda b, c, e: "floor")})()

    # metatile 0x123, collision 2, elevation 5
    entry = 0x123 | (2 << 10) | (5 << 12)
    cell = nav.cell_from_entry("Gym", entry)
    assert cell.metatile == 0x123
    assert cell.collision == 2
    assert cell.elevation == 5
    assert cell.behavior == 0x11


def test_an_override_wins_over_the_shipped_grid():
    """The whole point: a barrier that just opened has to beat the .blk."""
    nav = navmod.MapData.__new__(navmod.MapData)
    shipped = navmod.Cell(1, 1, 3, 0, "wall")
    opened = navmod.Cell(2, 0, 3, 0, "floor")
    nav.grid = lambda m: [[shipped]]
    nav._live = {}
    nav._reach_cache = {}

    assert nav.cell("Gym", 0, 0) is shipped
    assert nav.set_live_cells("Gym", {(0, 0): opened}) == 1
    assert nav.cell("Gym", 0, 0) is opened


def test_reachability_tracks_opening_and_closing_observed_barriers():
    d, _shipped = _live_driver()
    assert d.nav.reachable("Gym", (0, 0), 0) == {(0, 0)}
    d.emu.set_tile(1, 2 | (3 << 12))
    assert d.sync_grid() == 1
    assert d.nav.reachable("Gym", (0, 0), 0) == {(0, 0), (1, 0)}
    d.emu.set_tile(1, 1 | (1 << 10) | (3 << 12))
    assert d.sync_grid() == 1
    assert d.nav.reachable("Gym", (0, 0), 0) == {(0, 0)}


def test_re_syncing_the_same_cells_changes_nothing():
    """Repeated observations report no additional effective cell changes."""
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[navmod.Cell(1, 1, 3, 0, "wall")]]
    nav._live = {}
    nav._reach_cache = {}
    opened = navmod.Cell(2, 0, 3, 0, "floor")
    assert nav.set_live_cells("Gym", {(0, 0): opened}) == 1
    assert nav.set_live_cells("Gym", {(0, 0): opened}) == 0
    assert nav.cell("Gym", 0, 0) == opened


def test_overrides_can_be_dropped_per_map_or_wholesale():
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[navmod.Cell(1, 1, 3, 0, "wall")]]
    nav._live = {}
    nav._reach_cache = {}
    cell = navmod.Cell(2, 0, 3, 0, "floor")
    nav.set_live_cells("Gym", {(0, 0): cell})
    nav.set_live_cells("Cave", {(0, 0): cell})
    nav.clear_live_cells("Gym")
    assert nav.cell("Gym", 0, 0) == nav.grid("Gym")[0][0]
    assert nav.cell("Cave", 0, 0) == cell
    nav.clear_live_cells()
    assert nav.cell("Cave", 0, 0) == nav.grid("Cave")[0][0]


def test_stepping_onto_an_elevation_zero_tile_makes_you_elevation_zero():
    """How the game changes level at all, and it is not symmetric with 15.

    `ObjectEventUpdateZCoord` (src/event_object_movement.c:7586-7598):

        if (z == 0xF || z2 == 0xF) return;   // bridge: keep what you had
        objEvent->currentElevation = z;      // otherwise TAKE the tile's

    and `IsZCoordMismatchAt` (:7528) returns FALSE when the walker's own z is
    0. So an elevation-0 tile is a transition: you become 0 standing on it and
    may then step onto any level. 15 is a bridge and keeps your level.

    Treating them alike meant the walker could never leave the level it
    started on. Route 114's halves are elevation 3 and 4, joined by two
    elevation-0 cells, and the run concluded SURF was needed for a road it
    could already walk.
    """
    from pokeagent.nav import MapData

    # A 0-tile hands you elevation 0 whatever you arrived with.
    assert MapData._next_z(3, 0) == 0
    assert MapData._next_z(4, 0) == 0
    # A 15-tile is a bridge: it does NOT change you.
    assert MapData._next_z(3, 15) == 3
    assert MapData._next_z(4, 15) == 4
    # Anything else is simply the tile's own level.
    assert MapData._next_z(3, 4) == 4
    assert MapData._next_z(0, 4) == 4


class _LiveMapEmulator:
    """Two live tiles behind the engine's seven-cell padded border."""

    def __init__(self):
        self.width = 9
        self.raw = bytearray(9 * 8 * 2)
        self.set_tile(0, 1 | (1 << 10) | (3 << 12))
        self.set_tile(1, 1 | (1 << 10) | (3 << 12))

    def resolve(self, name):
        assert name == "gBackupMapLayout"
        return 100

    def u32(self, address):
        return {100: self.width, 104: 8, 108: 200}[address]

    def read(self, address, size):
        assert address == 200
        return self.raw[:size]

    def set_tile(self, x, entry):
        offset = ((7 * 9) + x + 7) * 2
        self.raw[offset:offset + 2] = entry.to_bytes(2, "little")


def _live_driver():
    d = object.__new__(Driver)
    d.emu = _LiveMapEmulator()
    d.map_name = lambda: "Gym"
    nav = navmod.MapData.__new__(navmod.MapData)
    shipped = navmod.Cell(1, 1, 3, 0, "wall")
    nav.grid = lambda name: [[shipped, shipped]]
    nav.info = lambda name: SimpleNamespace(width=2, height=1)
    nav._behaviour_of = lambda name, mid: 0
    nav.beh = SimpleNamespace(
        kind=lambda beh, coll, elev: "wall" if coll else "floor",
        blocked_sets=defaultdict(set),
        jump_sets=defaultdict(set),
    )
    nav.blocked = {}
    nav.surfing = False
    nav.waterfall = False
    nav._live = {}
    nav._reach_cache = {}
    d.nav = nav
    return d, shipped


def test_live_tiles_close_again_after_opening_or_loading_an_older_state():
    d, shipped = _live_driver()
    assert d.sync_grid() == 0
    d.emu.set_tile(0, 2 | (5 << 12))
    assert d.sync_grid() == 1
    opened = d.nav.cell("Gym", 0, 0)
    assert (opened.collision, opened.elevation) == (0, 5)
    d.emu.set_tile(0, 1 | (1 << 10) | (3 << 12))
    assert d.sync_grid() == 1
    assert d.nav.cell("Gym", 0, 0) == shipped
    assert d.sync_grid() == 0


def test_partial_sync_closes_only_observed_tiles_and_preserves_other_maps():
    d, shipped = _live_driver()
    opened = navmod.Cell(2, 0, 5, 0, "floor")
    # Explicit overrides still merge, rather than replacing the entire map.
    d.nav.set_live_cells("Gym", {(0, 0): opened})
    d.nav.set_live_cells("Gym", {(1, 0): opened})
    d.nav.set_live_cells("Cave", {(0, 0): opened})
    assert d.nav.cell("Gym", 0, 0) == opened
    assert d.sync_grid(rect=(0, 0, 0, 0)) == 1
    assert d.nav.cell("Gym", 0, 0) == shipped
    assert d.nav.cell("Gym", 1, 0) == opened
    assert d.nav.cell("Cave", 0, 0) == opened
    assert d.sync_grid(rect=(1, 0, 1, 0)) == 1
    assert d.nav.cell("Gym", 1, 0) == shipped


def test_unavailable_live_grid_does_not_discard_last_known_tiles():
    d, _shipped = _live_driver()
    d.emu.set_tile(0, 2 | (5 << 12))
    assert d.sync_grid() == 1
    opened = d.nav.cell("Gym", 0, 0)
    d.emu.width = 0
    assert d.sync_grid() == 0
    assert d.nav.cell("Gym", 0, 0) == opened


def test_outside_live_grid_is_unknown_even_during_full_sync():
    d, shipped = _live_driver()
    opened = navmod.Cell(2, 0, 5, 0, "floor")
    d.nav.set_live_cells("Gym", {(0, 0): opened, (1, 0): opened})
    # A layout exposing only x=0 cannot tell us whether x=1 has closed.
    d.emu.width = 8
    d.emu.raw = bytearray(8 * 8 * 2)
    d.emu.raw[-2:] = (1 | (1 << 10) | (3 << 12)).to_bytes(2, "little")
    assert d.sync_grid() == 1
    assert d.nav.cell("Gym", 0, 0) == shipped
    assert d.nav.cell("Gym", 1, 0) == opened
