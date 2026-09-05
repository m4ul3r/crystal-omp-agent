"""A move that cannot deliver its listed power is the cheapest slot, not the dearest.

SPIT UP reads **100 power** in the ROM's move table and deals literally
nothing without Stockpile. Measured, not inferred: ten consecutive turns of a
level-100 PELIPPER hitting a WALREIN for 0 damage while the Elite Four ran the
clock out, and the same move then blocked every TM in the bag because the
teacher priced the sacrifice at 100 and refused.

The teacher already had the right instinct -- "refuse to trade away something
good" -- applied to the wrong number. These tests pin the measure, in both
directions, because the failure is silent either way: overvalue and no TM is
ever learnable, undervalue and a real STAB move gets thrown away.
"""

import pytest

from pokeagent.teaching import Teacher


class _Names:
    """Just the two lookups `effective_power` uses."""

    def __init__(self, table):
        self.table = table            # {id: (name, power)}

    def move(self, mid):
        return self.table[mid][0]

    def move_data(self, mid):
        class _D:
            power = self.table[mid][1]
        return _D()


def _teacher(table):
    t = Teacher.__new__(Teacher)      # no emulator needed for this measure
    t.names = _Names(table)
    return t


@pytest.mark.unit
def test_spit_up_is_worth_nothing():
    """The exact move that blocked every TM in the bag."""
    t = _teacher({1: ("SPIT UP", 100)})
    assert t.effective_power(1) == 0


@pytest.mark.unit
def test_ordinary_moves_keep_their_power():
    t = _teacher({1: ("SURF", 95), 2: ("HYDRO PUMP", 120), 3: ("FLY", 70)})
    assert t.effective_power(1) == 95
    assert t.effective_power(2) == 120
    assert t.effective_power(3) == 70


@pytest.mark.unit
def test_every_conditional_move_is_zeroed():
    table = {i: (name, 100) for i, name in
             enumerate(sorted(Teacher.CONDITIONAL_POWER))}
    t = _teacher(table)
    assert all(t.effective_power(i) == 0 for i in table), \
        "a conditional-power move must never be priced at its listed power"


@pytest.mark.unit
def test_case_is_not_load_bearing():
    t = _teacher({1: ("Spit Up", 100), 2: ("spit up", 100)})
    assert t.effective_power(1) == 0
    assert t.effective_power(2) == 0


@pytest.mark.unit
def test_an_unknown_move_is_free_not_priceless():
    """A lookup failure must not make a slot un-overwritable.

    The old code answered 999 for an unreadable move, which is the same
    refuse-everything failure by another route.
    """
    class _Broken:
        def move(self, mid):
            raise KeyError(mid)

        def move_data(self, mid):
            raise KeyError(mid)

    t = Teacher.__new__(Teacher)
    t.names = _Broken()
    assert t.effective_power(7) == 0


@pytest.mark.unit
def test_the_useless_move_is_the_one_given_up():
    """End to end on SEA BIRD's real moveset: SPIT UP goes, not HYDRO PUMP."""
    table = {1: ("SURF", 95), 2: ("SPIT UP", 100),
             3: ("FLY", 70), 4: ("HYDRO PUMP", 120)}
    t = _teacher(table)
    hms = frozenset({1, 3})           # SURF and FLY come from HMs
    keepable = [m for m in (1, 2, 3, 4) if m not in hms]
    assert min(keepable, key=t.effective_power) == 2
    assert t.names.move(2) == "SPIT UP"
