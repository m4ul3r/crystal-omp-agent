"""An aligned held slide may not cross ANOTHER warp tile.

`take_warp` enters an aligned warp up to three cells away with one held
step instead of routing adjacent. That slide stops on the first warp it
touches, so a door whose PAIR sits between the player and the target can
never be reached that way: at Sprout Tower's exit, sliding L from (11,15)
toward (9,15) stopped on (10,15), fired nothing (a south-wall door answers
only to DOWN, gotcha 15), and `travel` retried the identical slide four
times before declaring an open door impassable.
"""
import pytest

from crystalagent.driver import Driver
from crystalagent.nav import STEP

pytestmark = pytest.mark.unit


class Slider(Driver):
    """Only _slide_is_clear runs; the map is a dict of tile kinds."""

    def __init__(self, tiles, missing=False):
        self.tiles = tiles
        self.missing = missing

    def tile_at(self, x, y, map_name=None):
        if self.missing:
            raise KeyError("no decoded grid here")
        return self.tiles.get((x, y), "floor")


# Sprout Tower 1F: the exit is the warp PAIR (9,15) and (10,15)
TOWER = {(9, 15): "warp", (10, 15): "warp"}


def test_slide_across_the_paired_door_tile_is_refused():
    d = Slider(TOWER)
    assert d._slide_is_clear((11, 15), (9, 15)) is False


def test_slide_over_plain_floor_is_allowed():
    d = Slider(TOWER)
    assert d._slide_is_clear((9, 12), (9, 15)) is True


def test_adjacent_slide_has_nothing_in_between():
    d = Slider(TOWER)
    assert d._slide_is_clear((10, 14), (10, 15)) is True


def test_unknown_map_data_does_not_block_the_slide():
    """A duck-typed driver with no grid must degrade to the old
    behaviour, never raise -- take_warp's fallback is a real approach."""
    d = Slider(TOWER, missing=True)
    assert d._slide_is_clear((11, 15), (9, 15)) is True


class Lander(Driver):
    """step_off_warp only: tiles and stepping are faked."""

    def __init__(self, tiles, here=(4, 16), moves_ok=True):
        self.tiles = dict(tiles)
        self.here = here
        self.moves_ok = moves_ok
        self.steps = []

    def pos(self):
        return (0, 0) + self.here

    def map_name(self):
        return "ECRUTEAK_GYM"

    def tile_at(self, x, y, map_name=None):
        return self.tiles.get((x, y), "blocked")

    def npc_cells(self):
        return set()

    def _step(self, mv, **kw):
        self.steps.append(mv)
        if not self.moves_ok:
            return "blocked"
        dx, dy = STEP[mv]
        self.here = (self.here[0] + dx, self.here[1] + dy)
        return "moved"


DOOR = {(4, 16): "warp", (5, 16): "warp", (4, 15): "floor"}


def test_a_door_arrival_steps_off_the_warp():
    """Ecruteak's gym door: arriving lands ON the exit warp and the next
    goto's first step re-entered it -- city<->gym three times, then the
    ping-pong guard bailed the leg."""
    d = Lander(DOOR)
    assert d.step_off_warp() == "U"
    assert d.here == (4, 15)


def test_the_paired_warp_tile_is_not_a_step_off():
    d = Lander({(4, 16): "warp", (5, 16): "warp"})
    assert d.step_off_warp() is None
    assert d.steps == []


def test_off_a_warp_it_does_nothing():
    d = Lander({(4, 16): "floor", (4, 15): "floor"})
    assert d.step_off_warp() is None
    assert d.steps == []
