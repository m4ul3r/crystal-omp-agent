"""wren pt6: goto silently no-oped on unreachable/out-of-bounds targets
(journal: 'returns without moving or raising'). Now every navigation
failure sets a machine-checkable d.last_goto_reason distinguishing
outside-bounds / unreachable / target-occupied, and strict=True upgrades
those failures to TravelError while interactive handoffs stay False."""
from collections import deque

import pytest

from crystalagent.nav import STEP
from crystalagent.driver import Driver, TravelError

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return 0


class FakeNav:
    """Straight-line same-map pathing; per-test overrides on the class
    instance flip cells unreachable."""
    warps = {}
    blocked = {}

    def find_path(self, m, cur, goal, avoid=()):
        return ["R"] * (goal[0] - cur[0])

    def find_route(self, cur_map, cur, goal_map, goal, avoid=()):
        return None

    def grid(self, m):
        return [[0] * 32 for _ in range(18)]


def goto_driver(step_results=()):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.settle = lambda **kw: None
    d.auto_fight = True
    world = {"map": "TEST_MAP", "cell": (0, 0)}
    d._world = world
    d.map_name = lambda: world["map"]
    d.pos = lambda: (0, 0) + world["cell"]
    d.nav = FakeNav()
    d._refresh_nav_blocks = lambda: None
    d._resolve_map = lambda name: world["map"] if name is None else name
    d._is_warp_cell = lambda x, y: False
    d.npc_cells = lambda: set()
    d.textbox = lambda: False
    d.menu_open = lambda: False
    d.press = lambda seq: None
    d.flush_dialog = lambda *a, **k: "done"
    script = deque(step_results)

    def _step(mv):
        r = script.popleft() if script else "moved"
        if r == "moved":
            x, y = world["cell"]
            dx, dy = STEP[mv]
            world["cell"] = (x + dx, y + dy)
        return r

    d._step = _step
    return d


# -- unreachable -------------------------------------------------------------

def test_unreachable_same_map_sets_reason_and_returns_false():
    d = goto_driver()
    d.nav.find_path = lambda m, cur, goal, avoid=(): None
    assert d.goto(5, 0) is False
    assert "unreachable" in d.last_goto_reason
    assert "(0, 0)" in d.last_goto_reason and "(5, 0)" in d.last_goto_reason
    assert d._world["cell"] == (0, 0)          # never moved


def test_unreachable_cross_map_sets_reason():
    d = goto_driver()
    assert d.goto(3, 3, map_name="OTHER_MAP") is False   # find_route -> None
    assert "unreachable" in d.last_goto_reason
    assert "OTHER_MAP" in d.last_goto_reason


def test_unreachable_strict_raises_travelerror():
    d = goto_driver()
    d.nav.find_path = lambda m, cur, goal, avoid=(): None
    with pytest.raises(TravelError, match="unreachable"):
        d.goto(5, 0, strict=True)
    assert "unreachable" in d.last_goto_reason  # reason still recorded


# -- out-of-bounds -----------------------------------------------------------

def test_out_of_bounds_sets_outside_reason():
    d = goto_driver()
    assert d.goto(99, 99) is False             # grid is 32x18
    assert "outside" in d.last_goto_reason
    assert "(99,99)" in d.last_goto_reason


def test_out_of_bounds_strict_raises():
    d = goto_driver()
    with pytest.raises(TravelError, match="outside"):
        d.goto(99, 99, strict=True)


# -- target-occupied ---------------------------------------------------------

def test_npc_on_goal_cell_fails_target_occupied():
    """Static route exists, but an NPC stands ON the goal: after a few
    wander-tolerant passes goto names the real problem instead of
    storming to a generic no-progress give-up."""
    d = goto_driver(["blocked"] * 50)
    goal = (2, 0)
    d.npc_cells = lambda: {goal}
    d.nav.find_path = (lambda m, cur, g, avoid=():
                       None if avoid else ["R"] * (g[0] - cur[0]))
    assert d.goto(*goal) is False
    assert "target-occupied" in d.last_goto_reason
    assert str(goal) in d.last_goto_reason


def test_npc_stepping_off_goal_still_arrives():
    """Occupancy is re-checked per pass: a wanderer that leaves the goal
    within the tolerance window does not fail the goto."""
    d = goto_driver(["blocked"])
    goal = (2, 0)
    occupied = {"on": True}
    d.npc_cells = lambda: {goal} if occupied["on"] else set()
    d.nav.find_path = (lambda m, cur, g, avoid=():
                       None if (avoid and occupied["on"])
                       else ["R"] * (g[0] - cur[0]))

    real_press = d.press

    def press(seq):
        occupied["on"] = False                 # NPC wanders off mid-wait
        real_press(seq)

    d.press = press
    assert d.goto(*goal) is True
    assert d._world["cell"] == goal


# -- strict never hijacks interactive handoffs -------------------------------

def test_strict_success_returns_true():
    d = goto_driver()
    assert d.goto(2, 0, strict=True) is True
    assert d._world["cell"] == (2, 0)


def test_strict_manual_battle_handoff_returns_false_not_raise():
    d = goto_driver(["battle"])
    d.auto_fight = False
    assert d.goto(2, 0, strict=True) is False
    assert "auto_fight=manual" in d.last_goto_reason


# -- plumbing ----------------------------------------------------------------

def test_fresh_driver_reads_none_before_any_goto():
    d = Driver.__new__(Driver)
    assert d.last_goto_reason is None          # class default, no AttributeError
