"""The Safari Zone has no weakening phase, and its balls are not in the bag.

Two wrong pools and one impossible wait, all in the catch path:

* `balls_available()` counted the ball POCKET, which inside the zone is a pool
  the game will not let you throw from. `gNumSafariBalls` is the real one
  (pret/src/safari_zone.c:28,62).
* `decide()` waited for the target's HP to drop before throwing. There is no
  player mon on the field -- the engine zeroes it
  (pret/src/battle_main.c:3711-3715) -- so there are no moves, the target sits
  at full HP forever, and the wait never ends while a 15%-per-turn flee roll
  runs (pret/src/battle_ai_script_commands.c:1668-1674).
"""

import pytest

from pokeagent.catching import BALL_RESERVE, Catcher

pytestmark = pytest.mark.unit


class _Battle:
    def __init__(self, kinds):
        self.kinds = kinds


class _State:
    def __init__(self, kinds=("wild",), safari_balls=0, pocket=None):
        self._kinds = kinds
        self._safari_balls = safari_balls
        self._pocket = pocket if pocket is not None else {"POKé BALL": 12}

    def battle(self):
        return _Battle(self._kinds)

    def bag(self):
        return {"poke_balls": dict(self._pocket)}

    def in_safari(self):
        return self._safari_balls > 0

    def safari_balls(self):
        return self._safari_balls

    def safari_steps(self):
        return 400 if self._safari_balls else 0


class _Driver:
    def __init__(self, state):
        self.state = state


def _catcher(state):
    c = object.__new__(Catcher)
    c.d = _Driver(state)
    c.thrown = 0
    c.caught = 0
    c.last_reason = ""
    c._dex = False
    return c


def test_safari_balls_come_from_the_zone_not_the_bag():
    """Thirty in the zone, an empty pocket: still thirty throwable."""
    state = _State(kinds=("safari",), safari_balls=30, pocket={})
    assert _catcher(state).balls_available() == 30


def test_an_empty_zone_is_empty_even_with_a_full_pocket():
    """The opposite error: a stocked bag must not authorise a throw the game
    has no ball for."""
    state = _State(kinds=("safari",), safari_balls=0, pocket={"GREAT BALL": 28})
    c = _catcher(state)
    # Not in a Safari visit at all (balls 0 -> in_safari False), so the pocket
    # is the honest pool; the point is that the zone reading WINS when in one.
    assert c.balls_available() == 28

    state._safari_balls = 1          # one ball left, mid-visit
    assert _catcher(state).balls_available() == 1


def test_outside_the_zone_the_bag_is_still_the_answer():
    state = _State(kinds=("wild",), safari_balls=0, pocket={"POKé BALL": 7})
    assert _catcher(state).balls_available() == 7


def test_the_reserve_guard_now_measures_the_right_pool():
    """BALL_RESERVE against Safari balls, not against Poke Balls."""
    plenty = _State(kinds=("safari",), safari_balls=BALL_RESERVE + 1, pocket={})
    assert _catcher(plenty).balls_available() > BALL_RESERVE
    spent = _State(kinds=("safari",), safari_balls=1, pocket={"POKé BALL": 99})
    assert _catcher(spent).balls_available() == 1


def test_a_safari_battle_approaches_once_then_throws():
    """GO NEAR is the only lever on the odds, and only the first is worth it.

    The catch-factor bonus falls 4,3,2,1 while the flee-rate penalty stays a
    flat 4 (pret/data/btl_attrs.s:380-391), so one approach and then balls --
    never a second approach, never a wait for HP that cannot fall.
    """
    state = _State(kinds=("safari",), safari_balls=30, pocket={})
    c = _catcher(state)
    inner_called = []
    policy = c.policy(object(), inner=lambda f: inner_called.append(f) or "attack")
    healthy = {"enemy": {"hp": 200, "max_hp": 200, "species": 263}}

    assert policy(healthy) == "go_near", "first turn must improve the odds"
    assert policy(healthy) == ("ball", "SAFARI BALL")
    assert policy(healthy) == ("ball", "SAFARI BALL"), "one approach, not two"
    assert not inner_called, "handed the turn to a policy with no move to pick"
    assert c.thrown == 2


def test_each_safari_battle_gets_its_own_approach():
    """The flag is per-battle: a fresh policy approaches again."""
    state = _State(kinds=("safari",), safari_balls=30, pocket={})
    c = _catcher(state)
    healthy = {"enemy": {"hp": 200, "max_hp": 200, "species": 263}}
    first = c.policy(object(), inner=lambda f: "attack")
    assert first(healthy) == "go_near"
    assert first(healthy) == ("ball", "SAFARI BALL")
    second = c.policy(object(), inner=lambda f: "attack")
    assert second(healthy) == "go_near", "a new battle starts a new approach"


def test_a_fainted_target_is_still_not_thrown_at():
    state = _State(kinds=("safari",), safari_balls=30, pocket={})
    c = _catcher(state)
    policy = c.policy(object(), inner=lambda f: "attack")
    assert policy({"enemy": {"hp": 0, "max_hp": 200}}) == "attack"
