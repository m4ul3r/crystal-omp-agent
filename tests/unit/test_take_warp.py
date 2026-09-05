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

from crystalagent.driver import Driver

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
    it, exactly like the engine. `warps` is the map's own warp table in
    def_warp_events order -- warp ids are 1-based positions in it."""

    warps = {}

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

    class Nav:
        warps = dict(getattr(world, "warps", {}) or {})
    d.nav = Nav()
    d._map_const = lambda: world.map
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


class InternalLadder(World):
    """Victory Road's floors live in ONE map joined by same-map
    warp_events, so entering (13,31) lands on (13,17) with the map name
    unchanged. take_warp used to call that a failure.

    The warp table is the map's own, in def_warp_events order:
    `warp_event 13, 31, VICTORY_ROAD, 3` lands on the THIRD entry."""

    warps = {"VICTORY_ROAD": {(9, 67): ("VICTORY_ROAD_GATE", 5),
                              (13, 31): ("VICTORY_ROAD", 3),
                              (13, 17): ("VICTORY_ROAD", 2)}}

    def __init__(self):
        super().__init__(cell=(13, 31), start=(13, 30),
                         map_name="VICTORY_ROAD")

    def step(self, mv):
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[mv]
        nxt = (self.pos[0] + dx, self.pos[1] + dy)
        if nxt == self.cell:
            self.entered.append(mv)
            self.pos = (13, 17)             # the paired ladder cell
            return "warp"
        self.pos = nxt
        return "moved"


def test_same_map_ladder_counts_as_a_fired_warp():
    w = InternalLadder()
    d = warp_driver(w)
    assert d.take_warp(*w.cell) is True
    assert w.entered == ["D"]
    assert d.map_name() == "VICTORY_ROAD"
    assert d.pos()[2:] == (13, 17)
    assert d.last_warp_reason is None


def test_same_map_landing_comes_from_the_maps_own_warp_table():
    """Warp ids are 1-based positions in def_warp_events, so the pairing
    is data, not a guess."""
    d = warp_driver(InternalLadder())
    assert d._same_map_landing((13, 31)) == (13, 17)
    assert d._same_map_landing((13, 17)) == (13, 31)
    assert d._same_map_landing((9, 67)) is None      # leaves the map
    assert d._same_map_landing((0, 0)) is None       # not a warp


def test_only_the_paired_cell_counts_as_a_same_map_warp():
    """A DISTANCE cannot tell a ladder from a walk. "moved more than 3
    cells" reported success for Kurt's house exit (3,7), where the tap
    fallback walks west to (0,7) on the same map with no warp fired --
    take_warp answered True with the player still indoors
    (tests/integration/test_take_warp_entry.py)."""
    fired = Driver._warp_fired
    ladder = (13, 17)
    # real ladder: entered (13,31) from (13,30), landed on the paired cell
    assert fired("VICTORY_ROAD", (13, 30), (13, 31),
                 "VICTORY_ROAD", (13, 17), ladder) is True
    # arrival drifts up to ~2 cells past the modeled landing (gotcha 14)
    assert fired("VICTORY_ROAD", (13, 30), (13, 31),
                 "VICTORY_ROAD", (13, 19), ladder) is True
    # a walk that shuffled two cells and never entered the warp
    assert fired("VICTORY_ROAD", (13, 17), (13, 31),
                 "VICTORY_ROAD", (15, 17), ladder) is False
    # the Kurt's house false positive: 4 cells away, same map, no pairing
    assert fired("KURTS_HOUSE", (4, 7), (3, 7),
                 "KURTS_HOUSE", (0, 7), None) is False
    # standing on the tile is never entering it
    assert fired("VICTORY_ROAD", (13, 30), (13, 31),
                 "VICTORY_ROAD", (13, 31), ladder) is False
    # a different map is always a fired warp
    assert fired("ILEX_FOREST", (3, 40), (3, 41),
                 "ILEX_FOREST_AZALEA_GATE", (3, 41)) is True


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
