"""claude-wren run frictions: scene-textbox drain in goto/travel, first-call
menu races in use_item/heal_pokecenter/mart_buy, and the multi-warp door-row
ping-pong (Sprout Tower 1F) held-entry fallback."""
from collections import deque

import pytest

import trek
from trek import Driver

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {}

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)


def bare_driver():
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.settle = lambda **kw: None
    return d


# -- _drain_scene ------------------------------------------------------------

def test_drain_scene_pages_until_script_clears(monkeypatch):
    """Textbox pages get A'd through; a scriptmode movement phase after the
    last page is waited out (never pressed into); then 'done'."""
    d = bare_driver()
    state = {"pages": 2, "movement": 1}
    d.battle = lambda: 0
    d.textbox = lambda: state["pages"] > 0
    d.emu.read_u8 = lambda sym: (
        1 if sym == "wScriptMode" and (state["pages"] or state["movement"])
        else 0)
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(10)
        if seq.startswith("A"):
            state["pages"] -= 1
        elif state["pages"] == 0:
            state["movement"] -= 1          # scene finishes its walk

    d.press = press
    monkeypatch.setattr(trek, "dialog_press_safe", lambda rows: True)
    assert d._drain_scene() == "done"
    assert sum(1 for p in presses if p.startswith("A")) == 2
    assert any(not p.startswith("A") for p in presses)  # waited, no mash


def test_drain_scene_aborts_on_choice_menu(monkeypatch):
    """A cursor outside the box (gotcha 13) stops the drain cold: 'menu',
    zero presses -- mashing would pick something."""
    d = bare_driver()
    d.battle = lambda: 0
    d.textbox = lambda: True
    presses = []
    d.press = lambda seq: presses.append(seq)
    monkeypatch.setattr(trek, "dialog_press_safe", lambda rows: False)
    assert d._drain_scene() == "menu"
    assert presses == []


def test_drain_scene_reports_battle(monkeypatch):
    d = bare_driver()
    battles = deque([0, 1])
    d.battle = lambda: battles.popleft() if battles else 1
    d.textbox = lambda: True
    d.press = lambda seq: d.emu.tick(10)
    monkeypatch.setattr(trek, "dialog_press_safe", lambda rows: True)
    assert d._drain_scene() == "battle"


def test_drain_scene_page_cap(monkeypatch):
    """An unending scene times out instead of mashing forever."""
    d = bare_driver()
    d.battle = lambda: 0
    d.textbox = lambda: True
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(10)

    d.press = press
    monkeypatch.setattr(trek, "dialog_press_safe", lambda rows: True)
    assert d._drain_scene(max_pages=5) == "timeout"
    assert len(presses) == 5


# -- goto wiring -------------------------------------------------------------

class FakeNav:
    """Straight-line same-map pathing (goal east of start on one row)."""
    warps = {}
    blocked = {}

    def find_path(self, m, cur, goal, avoid=()):
        return ["R"] * (goal[0] - cur[0])


def goto_driver(step_results):
    """Driver whose _step consumes scripted results; anything after the
    script (and any 'moved') advances the player one cell."""
    d = bare_driver()
    world = {"map": "TEST_MAP", "cell": (0, 0)}
    d._world = world
    d.map_name = lambda: world["map"]
    d.pos = lambda: (0, 0) + world["cell"]
    d.nav = FakeNav()
    d._refresh_nav_blocks = lambda: None
    d._resolve_map = lambda name: world["map"] if name is None else name
    d._is_warp_cell = lambda x, y: False
    d.npc_cells = lambda: set()
    d.menu_open = lambda: False
    d.press = lambda seq: None
    d.flush_calls = []
    d.flush_dialog = lambda *a, **k: d.flush_calls.append(a) or "done"
    script = deque(step_results)

    def _step(mv):
        r = script.popleft() if script else "moved"
        if r == "moved":
            x, y = world["cell"]
            dx, dy = trek.STEP[mv]
            world["cell"] = (x + dx, y + dy)
        return r

    d._step = _step
    return d


def test_goto_textbox_block_drains_then_succeeds():
    d = goto_driver(["blocked"])          # first step hits the scene
    scene = {"up": True}
    d.textbox = lambda: scene["up"]
    drains = []

    def drain(**kw):
        drains.append(1)
        scene["up"] = False               # scene fully consumed
        return "done"

    d._drain_scene = drain
    assert d.goto(2, 0) is True
    assert d._world["cell"] == (2, 0)
    assert len(drains) == 1
    assert d.flush_calls == []            # drain replaced the legacy flush


def test_goto_drain_choice_menu_surfaces_failure():
    d = goto_driver(["blocked"])
    d.textbox = lambda: True
    d._drain_scene = lambda **kw: "menu"
    assert d.goto(2, 0) is False
    assert "choice menu" in d.last_goto_reason


def test_goto_battle_mid_drain_uses_existing_fight_path():
    # blocked -> drain reports battle -> next pass's _step returns
    # 'battle' -> the existing fight intercept runs, then walking resumes
    d = goto_driver(["blocked", "battle"])
    scene = {"up": True}
    d.textbox = lambda: scene["up"]

    def drain(**kw):
        scene["up"] = False
        return "battle"

    d._drain_scene = drain
    fights = []
    d.fight = lambda *a, **k: fights.append(1)
    d._whiteout_stop = lambda where: False
    assert d.goto(2, 0) is True
    assert fights == [1]


# -- first-call menu races (gotcha 2) ----------------------------------------

def test_use_item_retries_start_menu_once_then_fails(monkeypatch):
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: 0)
    presses = []
    d.press = lambda seq: presses.append(seq) or d.emu.tick(5)
    assert d.use_item("POTION") is False  # menu never opens
    assert sum(1 for p in presses if p.startswith("START")) == 2


def test_use_item_start_gate_passes_on_retry(monkeypatch):
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: 0)
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(5)
        starts = sum(1 for p in presses if p.startswith("START"))
        if seq.startswith("START") and starts == 2:
            d.emu.rows[5] = "  ▶PACK".ljust(20)   # menu paints on retry

    d.press = press

    class FakeMenu:
        calls = []

        def select_label(self, label, max_presses=8):
            self.calls.append(label)
            return False                  # stop the flow after the gate

    d.menu = FakeMenu()
    assert d.use_item("POTION") is False
    assert d.menu.calls == ["PACK"]       # gate passed on the retry


class FakeHealDriver:
    """Duck-typed d for heal_pokecenter: heals on the Nth nurse visit."""

    def __init__(self, heal_on_visit):
        self.heal_on = heal_on_visit
        self.gotos = 0
        self.healed = False
        self.emu = self.names = None

    def map_name(self):
        return "VIOLET_POKECENTER_1F"

    def goto(self, x, y, label=""):
        self.gotos += 1
        if self.gotos >= self.heal_on:
            self.healed = True
        return True

    def step_dir(self, mv):
        return "blocked"

    def press(self, seq):
        pass

    def flush_dialog(self, *a, **k):
        return "done"

    def settle(self, **kw):
        pass

    def lead(self):
        return {"name": "GATOR", "hp": 24, "max_hp": 24}

    def party(self):
        hp = 24 if self.healed else 7
        return [{"species": "TOTODILE", "hp": hp, "max_hp": 24}]


def test_heal_pokecenter_retries_once_then_succeeds(monkeypatch):
    d = FakeHealDriver(heal_on_visit=2)
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": d.party()})
    trek.heal_pokecenter(d)               # must not raise
    assert d.gotos == 2                   # exactly one retry


def test_heal_pokecenter_raises_after_single_retry(monkeypatch):
    d = FakeHealDriver(heal_on_visit=99)  # never heals
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": d.party()})
    with pytest.raises(RuntimeError, match="not fully healed"):
        trek.heal_pokecenter(d)
    assert d.gotos == 2                   # retried once, not forever


def test_mart_buy_retalks_once_when_shop_never_opens():
    d = bare_driver()                     # blank screen: no ¥ ever
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    d.press = lambda seq: d.emu.tick(5)
    talks = []
    d.talk_to = lambda x, y, label="": talks.append((x, y)) or "talked"
    assert d.mart_buy(1, 3, "POTION") is False
    assert len(talks) == 2                # first call + exactly one retry


# -- multi-warp door-row held entry (gotcha 12) -------------------------------

def warp_step(cell=(9, 15)):
    return {"kind": "warp", "from": "SPROUT_TOWER_1F", "to": "ROUTE_31",
            "dir": "R", "cell": list(cell), "dest": [2, 7], "warp_id": 1,
            "notes": None,
            "approaches": [{"x": 8, "y": 15, "dir": "R"}]}


def test_held_warp_entry_adjacent_holds_onto_tile():
    d = bare_driver()
    d.pos = lambda: (0, 0, 8, 15)
    holds, taps = [], []
    d.step_hold = lambda mv, hold=80: holds.append(mv) or "warp"
    d._step_warp_tap = lambda mv: taps.append(mv) or "warp"
    assert d._held_warp_entry(warp_step()) == "warp"
    assert holds == ["R"] and taps == []


def test_held_warp_entry_falls_back_to_taps():
    d = bare_driver()
    d.pos = lambda: (0, 0, 8, 15)
    d.step_hold = lambda mv, hold=80: "moved"   # glide again, no warp
    taps = []
    d._step_warp_tap = lambda mv: taps.append(mv) or "warp"
    assert d._held_warp_entry(warp_step()) == "warp"
    assert taps == ["R"]


def test_held_warp_entry_walks_back_along_row():
    # glided PAST the double door to (11,15): re-enter tapping LEFT
    d = bare_driver()
    d.pos = lambda: (0, 0, 11, 15)
    holds, taps = [], []
    d.step_hold = lambda mv, hold=80: holds.append(mv) or "warp"
    d._step_warp_tap = lambda mv: taps.append(mv) or "warp"
    assert d._held_warp_entry(warp_step()) == "warp"
    assert holds == [] and taps == ["L"]  # not adjacent: taps only


def test_held_warp_entry_refuses_offaxis_and_far():
    d = bare_driver()
    d.step_hold = lambda mv, hold=80: pytest.fail("must not step")
    d._step_warp_tap = lambda mv: pytest.fail("must not tap")
    d.pos = lambda: (0, 0, 5, 5)          # off both axes
    assert d._held_warp_entry(warp_step()) is None
    d.pos = lambda: (0, 0, 9, 15)         # standing on the tile
    assert d._held_warp_entry(warp_step()) is None
    d.pos = lambda: (0, 0, 3, 15)         # same row but 6 cells out
    assert d._held_warp_entry(warp_step()) is None


def test_travel_pingpong_falls_back_to_held_entry():
    d = bare_driver()
    world = {"map": "SPROUT_TOWER_1F", "cell": (11, 15)}
    d.map_name = lambda: world["map"]
    d.pos = lambda: (0, 0) + world["cell"]
    d._resolve_map = lambda name: name
    d._refresh_nav_blocks = lambda: None
    d.textbox = lambda: False
    steps = [{"kind": "walk", "map": "SPROUT_TOWER_1F", "x": 8, "y": 15,
              "why": "approach warp to ROUTE_31"}, warp_step()]
    d.route = lambda dest, max_cost=None: steps

    def goto(x, y, label=""):
        world["cell"] = (x, y)
        return True

    d.goto = goto

    def _step(mv):
        world["cell"] = (11, 15)          # held glide crosses BOTH doors
        return "moved"

    d._step = _step
    d.step_hold = lambda mv, hold=80: "blocked"
    taps = []

    def tap(mv):
        taps.append(mv)
        world["map"], world["cell"] = "ROUTE_31", (2, 7)
        return "warp"

    d._step_warp_tap = tap
    out = d.travel("ROUTE_31")
    assert out == steps                   # arrived, no TravelError
    assert taps == ["L"]                  # real _held_warp_entry drove it
    assert world["map"] == "ROUTE_31"
