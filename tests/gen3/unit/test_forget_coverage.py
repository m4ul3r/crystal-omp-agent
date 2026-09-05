"""Which move gets spent when a level-up brings a fifth.

Ranking on raw power alone made LOTTAD forget ABSORB (20) for THIEF (40) --
throwing away the party's only Grass move on the way into a Water gym, while
FAKE OUT sat there duplicating STRENGTH's type.
"""
import pytest

from pokeagent.teaching import Teacher

pytestmark = pytest.mark.unit


class _Move:
    def __init__(self, power, type_):
        self.power, self.type = power, type_


class _Names:
    def __init__(self, table):
        self.table = table

    def move_data(self, mid):
        return self.table[mid]

    def move(self, mid):
        return f"move{mid}"


class _Mon:
    def __init__(self, moves):
        self.moves = moves


def teacher(table):
    t = object.__new__(Teacher)
    t.names = _Names(table)
    t.emu = None
    t.consts = None
    return t


#: STRENGTH(Normal 80), FAKE OUT(Normal 40), ABSORB(Grass 20), DIVE(Water 60)
LOTTAD = {1: _Move(80, "NORMAL"), 2: _Move(40, "NORMAL"),
          3: _Move(20, "GRASS"), 4: _Move(60, "WATER")}


def test_a_duplicated_type_is_spent_before_a_weaker_sole_type():
    t = teacher(LOTTAD)
    # Slot 1 is FAKE OUT: same type as STRENGTH, so it goes -- not ABSORB,
    # even though ABSORB has the lower base power.
    assert t.slot_to_forget(_Mon([1, 2, 3, 4]), 99) == 1


def test_status_moves_still_go_first():
    table = dict(LOTTAD)
    table[5] = _Move(0, "NORMAL")
    t = teacher(table)
    assert t.slot_to_forget(_Mon([1, 5, 3, 4]), 99) == 1


def test_all_types_unique_falls_back_to_weakest():
    table = {1: _Move(80, "NORMAL"), 2: _Move(60, "WATER"),
             3: _Move(20, "GRASS"), 4: _Move(70, "FIRE")}
    t = teacher(table)
    assert t.slot_to_forget(_Mon([1, 2, 3, 4]), 99) == 2
