"""A wandering NPC is not where maps/*.asm spawned it, and an A press
into empty ground is not a conversation.

Live cost: Chuck's wife (SPRITEMOVEDATA_WALK_LEFT_RIGHT at 10,46) had
stepped to (11,46); talk_to walked onto her spawn cell and returned
'talked' three times while HM02 FLY stayed in her pocket.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from trek import Driver  # noqa: E402

pytestmark = pytest.mark.unit


class Cells(Driver):
    def __init__(self, cells):
        self.cells = set(cells)

    def npc_cells(self):
        return self.cells


def test_spawn_cell_wins_when_the_npc_is_standing_on_it():
    assert Cells({(10, 46)})._live_target(10, 46) == (10, 46)


def test_a_lone_neighbour_is_the_wanderer():
    assert Cells({(11, 46), (14, 42)})._live_target(10, 46) == (11, 46)


def test_two_candidates_stay_ambiguous():
    d = Cells({(11, 46), (9, 46)})
    assert d._live_target(10, 46) == (10, 46)


def test_distant_sprites_are_not_the_target():
    assert Cells({(20, 46)})._live_target(10, 46) == (10, 46)


def test_no_sprite_table_degrades_to_the_listed_cell():
    class Broken(Driver):
        def npc_cells(self):
            raise RuntimeError("no struct table")
    assert Broken.__new__(Broken)._live_target(3, 4) == (3, 4)
