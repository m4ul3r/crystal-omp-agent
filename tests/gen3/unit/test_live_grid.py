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

import pytest

from pokeagent import nav as navmod

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


def test_syncing_clears_the_reachability_memo():
    """`reachable` is cached, and an opened barrier changes exactly the answer
    it is holding. A stale cache here means the route stays 'impossible' after
    the door opens."""
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[navmod.Cell(1, 1, 3, 0, "wall")]]
    nav._live = {}
    nav._reach_cache = {("Gym", (0, 0), 3): {(0, 0)}}
    nav.set_live_cells("Gym", {(0, 0): navmod.Cell(2, 0, 3, 0, "floor")})
    assert nav._reach_cache == {}


def test_re_syncing_the_same_cells_changes_nothing():
    """Idempotent, so a sync every loop does not thrash the memo."""
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[navmod.Cell(1, 1, 3, 0, "wall")]]
    nav._live = {}
    nav._reach_cache = {}
    opened = navmod.Cell(2, 0, 3, 0, "floor")
    assert nav.set_live_cells("Gym", {(0, 0): opened}) == 1
    nav._reach_cache = {"keep": set()}
    assert nav.set_live_cells("Gym", {(0, 0): opened}) == 0
    assert nav._reach_cache == {"keep": set()}, "an unchanged sync cleared the memo"


def test_overrides_can_be_dropped_per_map_or_wholesale():
    nav = navmod.MapData.__new__(navmod.MapData)
    nav.grid = lambda m: [[navmod.Cell(1, 1, 3, 0, "wall")]]
    nav._live = {}
    nav._reach_cache = {}
    cell = navmod.Cell(2, 0, 3, 0, "floor")
    nav.set_live_cells("Gym", {(0, 0): cell})
    nav.set_live_cells("Cave", {(0, 0): cell})
    nav.clear_live_cells("Gym")
    assert "Gym" not in nav._live and "Cave" in nav._live
    nav.clear_live_cells()
    assert nav._live == {}


def test_the_puzzle_search_puts_the_save_target_back():
    """`Driver.load` repoints `state_path`.

    The barrier search reloads a scratch savestate on every trial, so without
    restoring it afterwards the run's save target ends up in a temp directory:
    autosaves keep succeeding, the log keeps saying the run advanced, and the
    working state never moves again. That is a silent, total loss of progress,
    and it is exactly what happened -- fifteen minutes of a solved gym puzzle
    written to /tmp.
    """
    import inspect

    from scripts.play import Session

    src = inspect.getsource(Session.press_floor_switches)
    assert "working = d.state_path" in src
    # Every exit path: the win, the raise, and the give-up.
    assert src.count("d.state_path = working") >= 3


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
