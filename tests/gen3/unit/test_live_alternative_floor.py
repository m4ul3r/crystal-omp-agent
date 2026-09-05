"""`_live_alternative` must never hand back an action it was told is dead.

That is the method's own stated promise, and breaking it deadlocked the Elite
Four. The sequence, from the log:

    tactics wants ('switch', 0)  -> retired this battle
      -> fallback 'flee'         -> "cannot run from a trainer battle"
      -> 'flee' retired
      -> tactics still wants ('switch', 0) -> retired -> 'flee' -> ...

Both candidates were dead and the chain had no third option, so the run stood
in Glacia's room pressing the party menu open and shut. Reported from the
couch as "stuck flipping through the pokemon selection menu".

Struggle is the floor: an attack with no PP is exactly what the engine turns
into Struggle, so it always resolves the turn.
"""

import pytest

from pokeagent.battle import BattleSession


class _Tactics:
    def _cheapest_heal(self, analysis):
        return None


def _alt(dead, analysis):
    b = BattleSession.__new__(BattleSession)
    b._dead_actions = {repr(a) for a in dead}
    b._switch_broken = True
    b.tactics = _Tactics()
    b.futile = lambda action: 0
    return BattleSession._live_alternative(b, analysis)


@pytest.mark.unit
def test_a_usable_move_wins():
    analysis = {"moves": [{"slot": 2, "pp": 10, "kind": "damage",
                           "damage_max": 90}]}
    assert _alt([], analysis) == ("attack", 2)


@pytest.mark.unit
def test_flee_is_offered_when_it_is_still_alive():
    """No moves, switching broken: flight is the right answer -- once."""
    assert _alt([], {"moves": []}) == "flee"


@pytest.mark.unit
def test_a_dead_flee_is_never_returned():
    """The exact deadlock: flee already refused as a trainer battle."""
    got = _alt(["flee", ("switch", 0)], {"moves": []})
    assert got != "flee"
    assert got == ("attack", 0), got


@pytest.mark.unit
def test_the_floor_resolves_the_turn():
    """With literally everything dead it still returns an executable action."""
    dead = ["flee", ("switch", 0), ("switch", 1), ("attack", 0), ("attack", 1)]
    got = _alt(dead, {"moves": []})
    assert isinstance(got, tuple) and got[0] == "attack", got


@pytest.mark.unit
def test_a_pp_less_move_is_not_offered_as_a_normal_choice():
    """Struggle is a LAST resort, not a pick: a 0-PP move is filtered out."""
    analysis = {"moves": [{"slot": 1, "pp": 0, "kind": "damage",
                           "damage_max": 120}]}
    # switching is broken and the only move has no PP -> flight, not slot 1
    assert _alt([], analysis) == "flee"
