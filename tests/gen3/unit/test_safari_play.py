"""Safari battles: play() must drive the four options, never the move path.

A Safari encounter has no moves and no party on the player's side (the engine
zeroes it, pret/src/battle_main.c:3711-3715), so the ordinary loop -- which
needs tactics.outlook() and a move slot -- span with the player frozen. These
tests pin the routing and the default, both of which are behaviour a caller
depends on and neither of which needs an emulator.
"""

import pytest

pytestmark = pytest.mark.unit


class FakeEmu:
    """Frame counter and a tick that costs frames -- nothing else is read."""

    def __init__(self):
        self.frame = 0

    def tick(self, frames=1):
        self.frame += frames


class FakeSafari:
    """The smallest thing that can be asked "is this a safari battle?"."""

    def __init__(self, balls=3, catch_on=2):
        self.balls = balls
        self.catch_on = catch_on
        self.thrown = 0
        self.went_near = 0
        self.fled = False
        self.ticks = 0
        self.reasons = []
        self.emu = FakeEmu()
        self.last_reason = None

    def frame(self) -> dict:
        """What the policy is handed. Shape only -- it is a Safari battle, so
        there are no moves and no party on our side to describe."""
        return {"wild": True, "safari": True, "balls": self.balls,
                "thrown": self.thrown}

    def _result(self, outcome, start, reason):
        return {"outcome": outcome, "reason": reason,
                "frames": self.emu.frame - start}

    # --- the surface _play_safari actually touches -------------------------
    def active(self):
        return not self.fled and self.thrown < self.catch_on

    def at_safari_menu(self):
        return True

    def naming_open(self):
        return False

    def safari_ball(self):
        self.thrown += 1
        return True

    def safari_go_near(self):
        self.went_near += 1
        return True

    def safari_flee(self):
        self.fled = True
        return True

    def outcome_name(self):
        return "B_OUTCOME_CAUGHT"


def _run(session, policy=None):
    """Drive the real _play_safari against the fake."""
    from pokeagent.battle import BattleSession

    return BattleSession._play_safari(session, policy, 0, 10_000, None)


def test_default_policy_throws_balls_not_moves():
    """With nothing steering it, a Safari battle spends balls.

    There is no damage to deal, so "attack" is not a fallback that exists;
    fleeing forfeits the encounter. A ball is the only action that can end a
    Safari battle in the caller's favour.
    """
    s = FakeSafari(catch_on=2)
    out = _run(s)
    assert s.thrown == 2
    assert s.went_near == 0, "GO NEAR pays +4 flee rate; it is not a free default"
    assert out["outcome"] == "B_OUTCOME_CAUGHT"


def test_go_near_is_taken_only_when_asked():
    """GO NEAR is a judgement call: +4 catch factor for +4 flee rate."""
    calls = []

    def policy(_frame):
        calls.append(1)
        return "go_near" if len(calls) == 1 else ("ball", None)

    s = FakeSafari(catch_on=1)
    _run(s, policy)
    assert s.went_near == 1
    assert s.thrown == 1


def test_flee_ends_the_battle_without_spending_a_ball():
    s = FakeSafari(catch_on=99)
    _run(s, lambda _f: "flee")
    assert s.fled is True
    assert s.thrown == 0


def test_a_raising_policy_falls_back_to_a_ball_and_does_not_escape():
    """A broken policy must not abandon the player inside a battle."""

    def policy(_frame):
        raise ValueError("boom")

    s = FakeSafari(catch_on=1)
    out = _run(s, policy)
    assert s.thrown == 1
    assert out["outcome"] == "B_OUTCOME_CAUGHT"


def test_a_refused_throw_reports_stuck_rather_than_spinning():
    """If the ball will not throw, say so; do not loop on a dead menu."""

    class Jammed(FakeSafari):
        def safari_ball(self):
            self.last_reason = "the action menu never came back"
            return False

    s = Jammed(catch_on=99)
    out = _run(s)
    assert out["outcome"] == "stuck"
    assert "never came back" in out["reason"]
