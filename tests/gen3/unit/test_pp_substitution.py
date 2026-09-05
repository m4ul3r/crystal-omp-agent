"""Asked for an empty move, play a real one.

Refusing was correct and still wasted the turn: the caller had already decided,
the loop retried, and from outside that is indistinguishable from "we are
trying to use a move with no PP left" -- which is what kept being reported.

The live case that prompted it: LOUDRED with POUND 0, UPROAR 0, ASTONISH 13,
HOWL 40. Anything that asks for POUND should get ASTONISH.
"""

import pytest

from pokeagent.battle import BattleSession


class _Names:
    MOVES = {1: ("POUND", 40), 2: ("UPROAR", 90), 3: ("ASTONISH", 30),
             4: ("HOWL", 0)}

    def move(self, mid):
        return self.MOVES[mid][0]

    def move_data(self, mid):
        return type("MD", (), {"power": self.MOVES[mid][1]})()


class _Mon:
    species = 295            # LOUDRED
    moves = (1, 2, 3, 4)
    pp = (0, 0, 13, 40)


def _session():
    s = BattleSession.__new__(BattleSession)
    s._pp = {}
    s.names = _Names()
    return s


@pytest.mark.unit
def test_the_pp_table_reads_the_whole_moveset():
    s = _session()
    table = s._pp_table(_Mon())
    assert "POUND 0" in table and "ASTONISH 13" in table


@pytest.mark.unit
def test_usable_slots_excludes_the_empty_ones():
    s = _session()
    assert s.usable_slots(_Mon()) == [2, 3]


@pytest.mark.unit
def test_the_strongest_usable_move_is_the_substitute():
    """ASTONISH (30) beats HOWL (0); POUND and UPROAR are empty."""
    s = _session()
    mon = _Mon()
    spare = s.usable_slots(mon)
    best = max(spare, key=lambda i: s.names.move_data(mon.moves[i]).power or 0)
    assert best == 2, "ASTONISH, not the higher-power UPROAR it cannot use"


@pytest.mark.unit
def test_an_entirely_empty_moveset_has_no_substitute():
    class _Empty(_Mon):
        pp = (0, 0, 0, 0)

    s = _session()
    assert s.usable_slots(_Empty()) == []
