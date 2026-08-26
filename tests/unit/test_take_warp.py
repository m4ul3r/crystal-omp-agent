"""Standing ON a warp is not entering it.

A warp fires on the step that ENTERS its tile, with the key still down;
arriving on one never re-triggers it. Every door arrival therefore ends
standing on a tile that looks like an exit and does nothing, which cost a
live session turns at the Ilex/Azalea gate, the Union Cave north mouth,
the Olivine pier and three ship cabin doors. `travel` reported it as
`warp D at (3,41) -- expected ILEX_FOREST_AZALEA_GATE ... (step result:
blocked)` when the answer was "step off, then back on".

Also covered here: the map-edge slide (Azalea's east edge crosses at
y=14 while the plan said 13) and the money guard's false positives on
winnings.
"""
import logging

import pytest

from trek import Driver

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

    def read_be(self, sym, n):
        return 0


class World:
    """A one-warp map: the warp at `cell` fires only when a step ENTERS
    it, exactly like the engine."""

    def __init__(self, cell=(3, 41), start=None, map_name="ILEX_FOREST"):
        self.cell = cell
        self.pos = start or cell            # arrived ON the warp
        self.map = map_name
        self.entered = []

    def step(self, mv):
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[mv]
        nxt = (self.pos[0] + dx, self.pos[1] + dy)
        self.pos = nxt
        if nxt == self.cell:
            self.entered.append(mv)
            self.map = "ILEX_FOREST_AZALEA_GATE"
            return "warp"
        return "moved"


def warp_driver(world):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.map_name = lambda: world.map
    d.pos = lambda: (0, 0, world.pos[0], world.pos[1])
    d.tile_at = lambda x, y, map_name=None: (
        "warp" if (x, y) == world.cell else "floor")
    d._step = world.step
    d.step_hold = world.step
    d._step_warp_tap = world.step
    d.settle = lambda **kw: None
    d.press = lambda seq: None
    d.goto = lambda x, y, label="", **kw: False
    d.last_goto_reason = "unreachable"
    return d


def test_standing_on_the_warp_steps_off_and_back_on():
    """The pt12 failure verbatim: `travel` ended a leg standing on
    (3,41), stepping `D` from there just walked away, and the leg failed
    with 'step result: blocked'."""
    w = World(cell=(3, 41))
    d = warp_driver(w)
    assert d.pos()[2:] == w.cell            # arrived ON it
    assert d.take_warp(*w.cell) is True
    assert d.map_name() == "ILEX_FOREST_AZALEA_GATE"
    assert w.entered, "the warp must have been ENTERED, not stood on"
    assert d.last_warp_reason is None


def test_entering_from_an_adjacent_cell_needs_no_step_off():
    w = World(cell=(3, 41), start=(3, 40))
    d = warp_driver(w)
    assert d.take_warp(*w.cell) is True
    assert w.entered == ["D"]


def test_a_warp_that_never_fires_reports_a_reason():
    w = World(cell=(3, 41), start=(3, 40))
    w.step = lambda mv: "blocked"           # nothing moves
    d = warp_driver(w)
    d._step = w.step
    d.step_hold = w.step
    d._step_warp_tap = w.step
    assert d.take_warp(*w.cell) is False
    assert "still ILEX_FOREST" in d.last_warp_reason


def test_no_walkable_neighbour_is_a_distinct_reason():
    w = World(cell=(3, 41))
    d = warp_driver(w)
    d.tile_at = lambda x, y, map_name=None: (
        "warp" if (x, y) == w.cell else "blocked")
    assert d.take_warp(*w.cell) is False
    assert "no walkable neighbour" in d.last_warp_reason


def test_a_far_away_warp_routes_adjacent_first():
    w = World(cell=(3, 41), start=(9, 30))
    d = warp_driver(w)
    routed = []

    def goto(x, y, label="", **kw):
        routed.append((x, y))
        w.pos = (x, y)
        return True
    d.goto = goto
    assert d.take_warp(*w.cell) is True
    assert routed and abs(routed[0][0] - 3) + abs(routed[0][1] - 41) == 1


# -- the map-edge slide ------------------------------------------------

def test_the_edge_slide_finds_the_row_that_actually_crosses():
    """Azalea Town's east edge fires at y=14; the plan said y=13. A hand
    written `cross()` helper sliding along the edge was what got a live
    session through, so travel does it now."""
    state = {"pos": (39, 13), "map": "AZALEA_TOWN"}

    def step(mv):
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[mv]
        nxt = (state["pos"][0] + dx, state["pos"][1] + dy)
        if mv == "R":
            if state["pos"][1] == 14:       # only this row crosses
                state["map"] = "ROUTE_33"
                return "warp"
            return "blocked"
        state["pos"] = nxt
        return "moved"

    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.map_name = lambda: state["map"]
    d.pos = lambda: (0, 0, *state["pos"])
    d._step = step
    d.step_hold = step
    assert d._slide_edge({"dir": "R"}, "ROUTE_33") is True
    assert state["map"] == "ROUTE_33"


def test_the_edge_slide_gives_up_when_boxed_in():
    state = {"pos": (39, 13), "map": "AZALEA_TOWN"}
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.map_name = lambda: state["map"]
    d.pos = lambda: (0, 0, *state["pos"])
    d._step = lambda mv: "blocked"
    d.step_hold = lambda mv: "blocked"
    assert d._slide_edge({"dir": "R"}, "ROUTE_33") is False


# -- the money guard warns on a DECREASE only --------------------------

class WalletEmu(FakeEmu):
    def __init__(self, wallet):
        super().__init__()
        self.wallet = list(wallet)
        self.reads = 0

    def read_be(self, sym, n):
        val = self.wallet[min(self.reads, len(self.wallet) - 1)]
        self.reads += 1
        return val


def money_driver(wallet):
    d = Driver.__new__(Driver)
    d.emu = WalletEmu(wallet)
    d.map_name = lambda: "ROUTE_34"
    d.pos = lambda: (1, 2, 9, 12)
    d._goto_walk = lambda *a, **k: True
    d.press = lambda seq: None
    d.textbox = lambda: False
    return d


def test_winnings_are_recorded_but_do_not_warn(caplog):
    """`MONEY +216 ... movement must never spend money` fired on trainer
    winnings and taught the reader to ignore the line."""
    d = money_driver([500, 716])
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d.goto(1, 1) is True
    assert d.last_money_delta == 216
    assert "MONEY" not in caplog.text


def test_a_decrease_still_warns(caplog):
    d = money_driver([13000, 11800])
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d.goto(1, 1) is True
    assert d.last_money_delta == -1200
    assert "MONEY -1200" in caplog.text
    assert "never SPEND" in caplog.text
