"""CUT is a first-class field move, and nav learns the tree is gone.

`COLL_CUT_TREE` ($12) reads as a WALL, so any route that needs one is
simply "no path" -- live: Ilex Forest's north exit refused to route with
HM01 in the bag and CUT on the lead, and the savestate search could not
find a way either, because cutting is not a move it can make.
"""
import pytest

from crystalagent.driver import Driver
from crystalagent.nav import _tile_kind

pytestmark = pytest.mark.unit


def test_the_collision_byte_reads_as_a_cut_tree():
    """$12/$1a are trees, $15 is a headbutt tree -- all were 'blocked'."""
    assert _tile_kind(0x12) == "cut-tree"
    assert _tile_kind(0x1A) == "cut-tree"
    assert _tile_kind(0x15) == "headbutt-tree"
    assert _tile_kind(0x00) == "floor"


def test_cut_is_registered_with_the_badge_the_engine_checks():
    assert Driver.OW_FIELD_MOVES["CUT"] == ("cut-tree", "HIVE")


class Cutter(Driver):
    """cut() only: the field-move press and nav are faked."""

    def __init__(self, tiles, here=(8, 26), used=True):
        self.tiles = dict(tiles)
        self.here = here
        self.used = used
        self.calls = []
        self.patched = []
        self.last_field_reason = None
        self.last_goto_reason = None
        self.nav = type("N", (), {
            "set_cell": lambda _s, m, x, y, c: self.patched.append((m, x, y, c))
        })()

    # -- fakes -------------------------------------------------------------
    def pos(self):
        return (0, 0) + self.here

    def map_name(self):
        return "ILEX_FOREST"

    def _map_const(self):
        return "ILEX_FOREST"

    def tile_at(self, x, y, map_name=None):
        return self.tiles.get((x, y), "blocked")

    def facing(self):
        return "U"

    def close_menus(self):
        pass

    def _standable(self, name, cell):
        return self.tiles.get(cell) == "floor"

    def goto(self, x, y, label=""):
        self.here = (x, y)
        return True

    def use_field_move(self, move, facing=None):
        self.calls.append((move, facing))
        if self.used:
            self.tiles[(8, 25)] = "floor"
        return self.used

    def sync_grid(self):
        """The real one patches nav from the LIVE block map; a cut tree
        REGROWS on map re-entry, so cut() must never hand-write the cell."""
        if self.tiles.get((8, 25)) == "floor":
            self.patched.append(("ILEX_FOREST", 8, 25, 0x00))
        return list(self.patched)


TREE = {(8, 25): "cut-tree", (8, 26): "floor", (8, 27): "floor"}


def test_cutting_a_named_tree_faces_it_and_patches_nav():
    d = Cutter(TREE)
    assert d.cut(8, 25) is True
    assert d.calls == [("CUT", "U")]
    assert d.patched == [("ILEX_FOREST", 8, 25, 0x00)]   # pathing sees it


def test_a_cell_that_is_not_a_tree_is_refused_before_pressing():
    d = Cutter(TREE)
    assert d.cut(8, 27) is False
    assert "wrong-tile" in d.last_field_reason
    assert d.calls == [] and d.patched == []


def test_no_adjacent_tree_is_refused_with_a_reason():
    d = Cutter({(8, 26): "floor"}, here=(8, 26))
    assert d.cut() is False
    assert "no-tree" in d.last_field_reason


def test_a_failed_cut_does_not_patch_the_grid():
    d = Cutter(TREE, used=False)
    assert d.cut(8, 25) is False
    assert d.calls == [("CUT", "U")] and d.patched == []
