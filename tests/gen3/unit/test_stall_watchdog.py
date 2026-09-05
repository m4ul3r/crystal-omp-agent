"""A wedged run must free itself, because nobody is watching at 3am.

An unattended run that wedges is worse than one that crashes: a crash restarts
under the supervisor, a wedge just keeps logging cheerfully. This happened for
real -- fifteen minutes on Route 116, zero steps, heartbeats every minute, the
frame counter climbing the whole time because a party summary screen was open
with field controls locked.

That last detail is the reason the watchdog does not look at frames. Frames are
not progress. Position, battles fought and badges earned are.

The Session these exercise is built without `__init__` on purpose: the real one
opens a ROM. The watchdog only touches a handful of attributes, so supplying
exactly those keeps the test about stall logic rather than about emulator
setup.
"""

import time

import pytest

from scripts.play import Session

pytestmark = pytest.mark.unit


class FakeMon:
    def __init__(self, species="ZIGZAGOON", level=5, hp=20):
        self.species, self.level, self.hp = species, level, hp


class FakeState:
    def __init__(self, badges=0, party=None, money=1000):
        self._badges = badges
        self._party = party if party is not None else [FakeMon()]
        self._money = money

    def badges(self):
        return ["stone"] * self._badges

    def party(self):
        return self._party

    def money(self):
        return self._money


class FakeDriver:
    """A driver that goes nowhere unless a test moves it."""

    def __init__(self):
        self.pos_value = (32, 17)
        self.map_value = "Route116"
        self.state = FakeState()
        self.scenes_advanced = 0
        self.settles = 0

    def map_name(self):
        return self.map_value

    def pos(self):
        return self.pos_value

    def advance_scene(self, frames=0):
        self.scenes_advanced += 1
        return True

    def settle(self, *a, **k):
        self.settles += 1
        return True


def make_session(driver=None):
    session = object.__new__(Session)
    session.d = driver or FakeDriver()
    session.battles = 0
    session._stall_key = None
    session._stall_since = time.time()
    session._stall_level = 0
    session._lead_fails = {"EMBER"}
    session._last_quest_kind = "badge"
    session._story_tries = {"devon": 4}
    # Abandoned work, with the time it was abandoned. Level 3 forgives all of
    # it: a gate that refused an hour ago may be open now, and in this game
    # that is the normal case rather than the exception.
    session._story_given_up = {"sail to Slateport": 1.0}
    session._travel_given_up = {"DewfordTown": 1.0}
    return session


def age(session, seconds):
    """Pretend the stall started `seconds` ago."""
    session._stall_since = time.time() - seconds


def test_a_moving_run_is_never_touched():
    """The expensive half of a watchdog is the false positive."""
    session = make_session()
    session.watch_for_a_stall()
    for step in range(1, 6):
        session.d.pos_value = (32, 17 + step)
        age(session, 10_000)          # would trip instantly if it were stalled
        session.watch_for_a_stall()
    assert session._stall_level == 0
    assert session.d.scenes_advanced == 0


def test_a_battle_counter_alone_is_not_progress():
    """Reversed deliberately, because the old rule caused the worst stall.

    A trainer battle does hold position for minutes, and counting battles as
    progress stopped the watchdog interrupting one. But the counter advances
    on its OWN whenever the loop is fighting -- which is when the worst stalls
    happen. A Lottad used STRENGTH on a Grimer for hundreds of turns with both
    HP bars frozen, and because every turn bumped the counter, the stall
    detector saw progress and never fired.

    A long fight is still protected: it changes HP, and HP is in the
    signature. What is no longer protected is a fight that changes NOTHING,
    which is the case worth catching.
    """
    session = make_session()
    session.watch_for_a_stall()
    session.battles += 1
    age(session, Session.STALL_AFTER + 1)
    session.watch_for_a_stall()
    assert session._stall_level == 1, (
        "a bare battle counter must not read as progress"
    )


def test_a_fight_that_changes_hp_is_progress():
    session = make_session()
    session.watch_for_a_stall()
    session.d.state = FakeState(party=[FakeMon(hp=11)])
    age(session, Session.STALL_AFTER + 1)
    session.watch_for_a_stall()
    assert session._stall_level == 0, "HP moving is a fight making progress"


def test_a_badge_counts_as_progress_even_standing_in_the_gym():
    session = make_session()
    session.watch_for_a_stall()
    session.d.state = FakeState(badges=1)
    age(session, 10_000)
    session.watch_for_a_stall()
    assert session._stall_level == 0


def test_nothing_happening_escalates_through_the_ladder():
    """The whole point: no movement, no battles, no badges -> act.

    Escalating matters because the cheap fix covers the common case. Level 1 is
    just backing out of a screen, which is all the Route 116 freeze needed.
    """
    session = make_session()
    session.watch_for_a_stall()          # first sighting only records the key

    age(session, Session.STALL_AFTER + 1)
    session.watch_for_a_stall()
    assert session._stall_level == 1
    assert session.d.scenes_advanced == 1, "level 1 should back out of the screen"

    age(session, Session.STALL_AFTER * 2 + 1)
    session.watch_for_a_stall()
    assert session._stall_level == 2
    assert session.d.settles == 1
    assert session._lead_fails == set(), "level 2 forgets the leads it gave up on"
    assert session._last_quest_kind is None

    age(session, Session.STALL_AFTER * 3 + 1)
    session.watch_for_a_stall()
    assert session._story_tries == {}, "level 3 forgives the refused story steps"
    assert session._story_given_up == {}, "an abandoned step must be reconsidered"
    assert session._travel_given_up == {}
    assert session._stall_level == 0, "the ladder resets so it can climb again"


def test_it_waits_before_acting():
    """A slow stretch is not a wedge. Recovery mid-Pokecenter would be worse
    than the stall it is guarding against."""
    session = make_session()
    session.watch_for_a_stall()
    age(session, Session.STALL_AFTER - 30)
    session.watch_for_a_stall()
    assert session._stall_level == 0
    assert session.d.scenes_advanced == 0


def test_recovery_that_throws_does_not_end_the_run():
    """The watchdog is the safety net; it must not become the failure."""
    class Exploding(FakeDriver):
        def advance_scene(self, frames=0):
            raise RuntimeError("emulator said no")

    session = make_session(Exploding())
    session.watch_for_a_stall()
    age(session, Session.STALL_AFTER + 1)
    session.watch_for_a_stall()          # must not raise
    assert session._stall_level == 1


def test_a_read_that_fails_is_not_mistaken_for_progress():
    """If position cannot be read the run is in trouble, but a half-built key
    would reset the timer every minute and the watchdog would never fire."""
    class Unreadable(FakeDriver):
        def pos(self):
            raise RuntimeError("no avatar yet")

    session = make_session(Unreadable())
    before = session._stall_since
    session.watch_for_a_stall()
    assert session._stall_since == before, "a failed read must not reset the clock"
