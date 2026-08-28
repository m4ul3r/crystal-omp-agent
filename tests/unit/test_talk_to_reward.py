"""talk_to: approach choice and the post-battle reward tail.

Two live failures from the ZEPHYR run:
  * standing face-to-face with Violet Gym's Bird Keeper Abe, `_approach_cell`
    picked his FAR side and the route ran through Abe himself, so talk_to
    answered `blocked-by-stationary-npc ... severs the only path` for a
    trainer one step away;
  * `talk_to` returned 'battle' the moment the fight ended, leaving the
    script's tail -- Falkner's badge and TM31, Sage Li's HM05 -- unread, so
    the reward only arrived if the caller happened to talk a second time.
"""
import pytest

import trek

pytestmark = pytest.mark.unit


class Nav:
    def __init__(self, walkable):
        self.walkable = walkable

    def grid(self, name):
        w = max(x for x, _ in self.walkable) + 2
        h = max(y for _, y in self.walkable) + 2
        return [[0] * w for _ in range(h)]

    def find_path(self, name, a, b, avoid=()):
        """A straight-line path, so its LENGTH ranks candidates the way
        the real BFS does."""
        if b not in self.walkable or b in avoid:
            return None
        return ["step"] * (abs(a[0] - b[0]) + abs(a[1] - b[1]))


class Approacher(trek.Driver):
    """Only _approach_cell runs."""

    def __init__(self, here, npcs, walkable):
        self._here = here
        self._npcs = set(npcs)
        self.nav = Nav(set(walkable))

    def pos(self):
        return (0, 0) + self._here

    def map_name(self):
        return "VIOLET_GYM"

    def npc_cells(self):
        return set(self._npcs)

    def _standable(self, name, cell):
        return cell in self.nav.walkable


# Abe stands at (3,10); the player is already on his right at (4,10)
WALKABLE = {(2, 10), (4, 10), (3, 9), (3, 11), (5, 10)}


def test_already_adjacent_cell_wins_outright():
    d = Approacher((4, 10), {(3, 10)}, WALKABLE)
    assert d._approach_cell(3, 10) == (4, 10)      # no walking at all


def test_otherwise_the_nearest_side_wins():
    """Sides of (3,10) are (3,9), (3,11), (2,10), (4,10); from (6,10) the
    right-hand one is the short walk, and the far side is a route THROUGH
    the NPC."""
    d = Approacher((6, 10), {(3, 10)}, WALKABLE)
    assert d._approach_cell(3, 10) == (4, 10)


def test_unreachable_sides_are_skipped():
    d = Approacher((6, 10), {(3, 10)}, {(2, 10)})
    assert d._approach_cell(3, 10) == (2, 10)


class Talker(trek.Driver):
    """talk_to with every emu-touching primitive faked. The battle starts
    when the A press lands, like a sight-line trainer."""

    def __init__(self, battles=1):
        self.pending = battles
        self.in_battle = False
        self.flushed = []
        self.fought = 0
        self.emu = type("E", (), {"frame": 10_000})()

    # -- fakes -------------------------------------------------------------
    def battle(self):
        return self.in_battle

    def fight(self, *a, **kw):
        self.fought += 1
        self.in_battle = False

    def _whiteout_stop(self, label):
        return False

    def settle(self, *a, **kw):
        pass

    def press(self, seq):
        self.emu.frame += 60
        if seq.startswith("A") and self.pending:
            self.pending -= 1
            self.in_battle = True

    def step_dir(self, mv):
        return True

    def goto(self, x, y, label=""):
        return True

    def flush_dialog(self, max_frames=3000):
        self.flushed.append(max_frames)
        return "done"

    def _approach_cells(self, x, y):
        return [(x, y + 1)]


def test_reward_tail_is_read_after_a_trainer_battle():
    d = Talker()
    assert d.talk_to(5, 1) == "battle"
    assert d.fought == 1
    # the pre-battle dialog flush AND the post-battle script tail
    assert len(d.flushed) >= 2, d.flushed


def test_plain_talk_does_not_fight_or_double_flush():
    d = Talker(battles=0)
    assert d.talk_to(5, 1) == "talked"
    assert d.fought == 0
    assert len(d.flushed) == 1


class Racer(Talker):
    """The first approach cell is unreachable (a wanderer took it)."""

    def __init__(self, blocked):
        super().__init__(battles=0)
        self.blocked = blocked
        self.tried = []
        self.last_goto_reason = "npc on target cell"

    def _approach_cells(self, x, y):
        return [(x, y - 1), (x, y + 1)]

    def goto(self, x, y, label=""):
        self.tried.append((x, y))
        return (x, y) != self.blocked


def test_a_blocked_side_falls_through_to_the_next_one():
    d = Racer(blocked=(5, 0))
    assert d.talk_to(5, 1) == "talked"
    assert d.tried == [(5, 0), (5, 2)]      # tried both, talked from below


def test_every_side_blocked_still_reports_failure():
    class AllBlocked(Racer):
        def goto(self, x, y, label=""):
            self.tried.append((x, y))
            return False
    d = AllBlocked(blocked=None)
    assert d.talk_to(5, 1) is False
    assert len(d.tried) == 2


def test_a_global_blocker_stops_the_side_walk_immediately():
    """An open choice box is not about the side we approach from: retrying
    the other seven multiplied one Route 34 phone-number prompt into forty
    replan storms."""
    class Boxed(Racer):
        def goto(self, x, y, label=""):
            self.tried.append((x, y))
            self.last_goto_reason = (
                "blocked by choice menu ['YES', 'NO'] -- resolve_choice")
            return False
    d = Boxed(blocked=None)
    assert d.talk_to(5, 1) is False
    assert d.tried == [(5, 0)]          # stopped after the first


def test_driver_exposes_heal_as_a_method(monkeypatch):
    """The capability table documents `d.heal()`; the only implementation
    was the module function, so a live gym run hit AttributeError."""
    seen = {}
    monkeypatch.setattr(trek, "heal_pokecenter",
                        lambda d, tries=2: seen.setdefault("tries", tries))
    d = Talker(battles=0)
    assert d.heal() == 2
    assert seen == {"tries": 2}
