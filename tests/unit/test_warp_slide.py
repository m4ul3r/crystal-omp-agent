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

import trek

pytestmark = pytest.mark.unit


class Slider(trek.Driver):
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
