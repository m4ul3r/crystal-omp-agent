"""Our own PP count, and a lesson that outlives one battle.

Both come from the same observed failure: ROCKY sat at PROTECT 0/10 while
every other move was at maximum PP. Ten uses of a move that changes nothing
means the retirement worked and then was FORGOTTEN, once per battle, ten
times -- ending in a mon whose only options were status moves.
"""

import pytest

from pokeagent.battle import BattleSession


class _Mon:
    def __init__(self, species, moves, pp):
        self.species = species
        self.moves = moves
        self.pp = pp


def _session():
    """A session with nothing wired up but the ledger under test."""
    s = BattleSession.__new__(BattleSession)
    s._pp = {}
    s._futile = set()
    return s


@pytest.mark.unit
def test_pp_left_takes_the_lower_of_the_two_counts():
    s = _session()
    mon = _Mon(1, (10, 20, 0, 0), (5, 8, 0, 0))
    assert s.pp_left(mon, 0) == 5
    s._pp_spend(mon, 0)
    mon.pp = (5, 8, 0, 0)  # a read that has not caught up
    assert s.pp_left(mon, 0) == 4, "our count must win when it is lower"


@pytest.mark.unit
def test_a_higher_live_read_reseeds_the_ledger():
    """A Centre visit or an Ether restores PP; a monotonic counter would stay
    wrong for the rest of the run."""
    s = _session()
    mon = _Mon(1, (10, 0, 0, 0), (1, 0, 0, 0))
    assert s.pp_left(mon, 0) == 1
    s._pp_spend(mon, 0)
    assert s.pp_left(mon, 0) == 0
    mon.pp = (10, 0, 0, 0)  # healed
    assert s.pp_left(mon, 0) == 10


@pytest.mark.unit
def test_usable_slots_skips_empty_and_spent():
    s = _session()
    mon = _Mon(1, (10, 20, 30, 0), (0, 4, 0, 0))
    assert s.usable_slots(mon) == [1]


@pytest.mark.unit
def test_the_ledger_is_per_species_not_per_slot():
    """A switch puts a different mon in the same slot; its PP is its own."""
    s = _session()
    a = _Mon(1, (10, 0, 0, 0), (3, 0, 0, 0))
    b = _Mon(2, (10, 0, 0, 0), (7, 0, 0, 0))
    s.pp_left(a, 0)
    s._pp_spend(a, 0)
    assert s.pp_left(a, 0) == 2
    assert s.pp_left(b, 0) == 7, "another species must not inherit the spend"


@pytest.mark.unit
def test_futility_survives_the_battle_it_was_learned_in():
    s = _session()
    s._futile.add((1, 10))
    s.battler = lambda idx: _Mon(1, (10, 20, 0, 0), (5, 5, 0, 0))
    s.menu_battler = lambda: 0
    assert s.futile(("attack", 0)) is True
    assert s.futile(("attack", 1)) is False


@pytest.mark.unit
def test_only_attacks_earn_a_permanent_lesson():
    """A switch that did nothing says something about the turn, not forever."""
    s = _session()
    s.battler = lambda idx: _Mon(1, (10, 0, 0, 0), (5, 0, 0, 0))
    s.menu_battler = lambda: 0
    assert s._futile_key(("switch", 1)) is None
    assert s._futile_key("flee") is None
    assert s._futile_key(("attack", 0)) == (1, 10)
