"""A thrown ball is progress, even though it moves no HP.

The stall detector exists for moves that execute and accomplish nothing (a
Lottad whose STRENGTH the engine would not run, sitting at 67 HP against a
Grimer at 21 for hundreds of turns). It measures "nothing happened" as "both
HP bars are unchanged".

A ball throw NEVER changes either HP bar -- that is what a ball is -- so the
rule condemned every single throw. Two throws retired the ball action and four
FLED the battle outright, which is why a collection run flew to three maps,
bought 41 Poke Balls, and caught nothing while logging six consecutive
"changed neither side's HP" lines against one full-health MEDICHAM.

The honest measure of a throw is whether a ball left the bag. A spent ball
means the attempt happened and lost a dice roll, and it is self-limiting
because the supply runs out.
"""

import pytest

from pokeagent.battle import BattleSession


class FakeState:
    def __init__(self, balls):
        self._balls = balls

    def bag(self):
        return {"poke_balls": dict(self._balls)}


def _session(balls):
    s = BattleSession.__new__(BattleSession)
    s.state = FakeState(balls)
    return s


@pytest.mark.unit
@pytest.mark.parametrize("action,expected", [
    (("ball", "ULTRA BALL"), True),
    (("ball", "SAFARI BALL"), True),
    (("attack", 0), False),
    (("switch", 2), False),
    (("item", "POTION"), False),
    ("flee", False),
    (None, False),
])
def test_only_a_ball_throw_is_recognised_as_a_ball(action, expected):
    assert BattleSession._is_ball(action) is expected


@pytest.mark.unit
def test_ball_count_sums_every_kind():
    """Which ball was thrown does not matter, only that one left the bag."""
    s = _session({"POKé BALL": 41, "NET BALL": 6})
    assert s._ball_count() == 47


@pytest.mark.unit
def test_an_unreadable_bag_reports_minus_one_rather_than_raising():
    """A diagnostic must never be the thing that ends a battle."""
    s = BattleSession.__new__(BattleSession)

    class Boom:
        def bag(self):
            raise RuntimeError("SVBK moved under us")

    s.state = Boom()
    assert s._ball_count() == -1


@pytest.mark.unit
def test_an_empty_ball_pocket_is_zero_not_an_error():
    s = _session({})
    assert s._ball_count() == 0


@pytest.mark.unit
def test_a_spent_ball_is_detected_by_the_bag_going_down():
    """The exact signal the turn loop uses to spare a throw from the stall
    counter: same HP on both sides, but one fewer ball."""
    s = _session({"POKé BALL": 41})
    before = s._ball_count()

    s.state = FakeState({"POKé BALL": 40})          # the throw happened

    spent = BattleSession._is_ball(("ball", "POKé BALL")) and (
        before < 0 or s._ball_count() < before
    )
    assert spent, "a thrown ball must not be counted as a stalled turn"


@pytest.mark.unit
def test_a_ball_action_that_never_left_the_bag_is_still_a_stall():
    """The guard must not become a blanket exemption.

    If the ball MENU is broken and nothing is ever thrown, that genuinely is
    a stuck action and the stall counter has to keep catching it.
    """
    s = _session({"POKé BALL": 41})
    before = s._ball_count()

    spent = BattleSession._is_ball(("ball", "POKé BALL")) and (
        before < 0 or s._ball_count() < before
    )
    assert not spent
