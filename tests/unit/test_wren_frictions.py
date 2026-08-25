"""claude-wren run frictions: scene-textbox drain in goto/travel, first-call
menu races in use_item/heal_pokecenter/mart_buy, and the multi-warp door-row
ping-pong (Sprout Tower 1F) held-entry fallback."""
from collections import deque
from pathlib import Path

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


def test_drain_scene_aborts_on_real_choice_cursor():
    """A real YES/NO box -- an actual cursor glyph ($ec/$ed) drawn --
    stops the drain cold: 'menu', zero presses (gotcha 13)."""
    for glyph in "▶▷":
        d = bare_driver()
        d.battle = lambda: 0
        d.textbox = lambda: True
        d.emu.rows[7] = f"         {glyph}YES       "
        d.emu.rows[8] = "          NO        "
        presses = []
        d.press = lambda seq: presses.append(seq)
        assert d._drain_scene() == "menu"
        assert presses == []


def test_drain_scene_blank_textbox_waits_then_pages():
    """A drawn-but-EMPTY pre-battle textbox (no text, no cursor) is a
    still-rendering page, NOT a choice menu: bounded wait, then A
    (leg-2: 8 false 'blocked by choice menu' aborts)."""
    d = bare_driver()
    d.battle = lambda: 0
    state = {"pages": 1}
    d.textbox = lambda: state["pages"] > 0
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(10)
        if seq.startswith("A"):
            state["pages"] -= 1

    d.press = press                       # screen stays blank forever
    assert d._drain_scene() == "done"     # never 'menu'
    assert [p for p in presses if p.startswith("A")] == ["A:2 .:8"]
    assert any(p.startswith(".") for p in presses)   # waited first


def test_drain_scene_blank_box_pages_once_text_renders():
    """The wait is re-checked: the moment the page's text lands, A goes
    out without burning the rest of the wait budget."""
    d = bare_driver()
    d.battle = lambda: 0
    state = {"pages": 1}
    d.textbox = lambda: state["pages"] > 0
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(10)
        if seq.startswith("."):
            if sum(1 for p in presses if p.startswith(".")) == 2:
                d.emu.rows[14] = " BUG CATCHER WADE   "
        elif seq.startswith("A"):
            state["pages"] -= 1
            d.emu.rows[14] = " " * 20

    d.press = press
    assert d._drain_scene() == "done"
    assert [p for p in presses if p.startswith("A")] == ["A:2 .:8"]
    assert sum(1 for p in presses if p.startswith(".")) == 2


def test_drain_scene_blank_box_menu_when_cursor_appears():
    """If the 'blank box' resolves into a choice (cursor materializes
    during the wait), the drain still aborts with 'menu' -- no A."""
    d = bare_driver()
    d.battle = lambda: 0
    d.textbox = lambda: True
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(10)
        if seq.startswith("."):
            d.emu.rows[7] = "         ▶YES       "

    d.press = press
    assert d._drain_scene() == "menu"
    assert not any(p.startswith("A") for p in presses)


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

    def grid(self, m):
        # goto's out-of-bounds guard: 32x18 plane accepts the old
        # fixtures' in-range goals
        return [[0] * 32 for _ in range(18)]


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


# -- items-pocket cursor persistence (leg 2: 'no potion visible' w/ 2 held) --

def pocket_driver(start, items, row_text=None):
    """Driver + fake scrolling pocket whose WRAM cursor starts at `start`
    (the pack persists wItemsPocketCursor between opens)."""
    d = bare_driver()
    state = {"cur": start, "confirmed": [], "ups": 0, "downs": 0}

    class M:
        def scroll_abs(self):
            return state["cur"]

        def cursor_row(self):
            text = row_text or (items[state["cur"]] + "        ×  2")
            return (2, text)

    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("U"):
            state["ups"] += 1
            state["cur"] = max(0, state["cur"] - 1)
        elif seq.startswith("D"):
            state["downs"] += 1
            state["cur"] = min(len(items) - 1, state["cur"] + 1)
        elif seq.startswith("A"):
            state["confirmed"].append(state["cur"])

    d.press = press
    return d, state


POCKET = ["POTION", "ANTIDOTE", "PARLYZ HEAL", "POKE BALL"]


def test_pocket_select_climbs_up_from_persisted_cursor():
    """Cursor left mid-list by a previous pack open: selection walks UP
    on the live WRAM index (DOWN-only scrapes could never reach row 0)."""
    d, state = pocket_driver(start=3, items=POCKET)
    assert d._pocket_select(0, "POTION") is True
    assert state["ups"] == 3 and state["downs"] == 0
    assert state["confirmed"] == [0]      # A only once, on the right row


def test_pocket_select_refuses_wram_screen_mismatch():
    """WRAM says the right index but the highlighted TEXT is a different
    item: never blind-A."""
    d, state = pocket_driver(start=0, items=POCKET, row_text="ANTIDOTE  ×  1")
    assert d._pocket_select(0, "POTION") is False
    assert state["confirmed"] == []


def test_pocket_select_bails_when_cursor_pinned():
    """A cursor that stops responding (wrong menu, list edge) fails fast
    instead of mashing to the step cap."""
    d, state = pocket_driver(start=2, items=POCKET)
    d.press = lambda seq: d.emu.tick(5)   # presses move nothing
    assert d._pocket_select(0, "POTION") is False
    assert state["confirmed"] == []


def use_item_world(d, monkeypatch, start_cursor=3, consume_on_use=True):
    """Wire use_item's collaborators: START menu paints, pack opens, the
    pocket cursor starts mid-list, USE consumes (or not)."""
    items = POCKET
    world = {"cur": start_cursor, "qty": 2, "ups": 0}
    d.emu.u8["wMenuCursorY"] = 1      # party-menu cursor on row 0
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: 0)
    monkeypatch.setattr(trek, "bag_quantity", lambda *a, **k: world["qty"])
    monkeypatch.setattr(trek, "goto_pocket", lambda menu, pocket: True)
    monkeypatch.setattr(trek, "cancel_pack", lambda menu: None)

    class M:
        def select_label(self, label, max_presses=14):
            if label == "USE" and consume_on_use:
                world["qty"] -= 1         # engine consumes the item
            return True

        def wait_for_label(self, label, timeout_frames=300):
            return True

        def wait_for(self, pred, timeout_frames=600):
            return True                   # target party list appeared

        def scroll_abs(self):
            return world["cur"]

        def cursor_row(self):
            return (2, items[world["cur"]] + "     ×  2")

    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("START"):
            d.emu.rows[5] = "  ▶PACK".ljust(20)
        elif seq.startswith("U"):
            world["ups"] += 1
            world["cur"] = max(0, world["cur"] - 1)
        elif seq.startswith("D"):
            world["cur"] = min(len(items) - 1, world["cur"] + 1)
        elif seq.startswith("B"):
            d.emu.rows[5] = " " * 20      # pack closes

    d.press = press
    return world


def test_use_item_finds_potion_from_mid_list_cursor(monkeypatch):
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    world = use_item_world(d, monkeypatch, start_cursor=3)
    assert d.use_item("POTION") is True
    assert world["ups"] == 3              # climbed back up to row 0
    assert world["qty"] == 1              # confirmed via bag read-back


def test_use_item_false_when_bag_never_decrements(monkeypatch):
    """Menus can flow perfectly while a swallowed A used nothing: the
    bag read-back is the success gate."""
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    use_item_world(d, monkeypatch, start_cursor=0, consume_on_use=False)
    assert d.use_item("POTION") is False


class FakeHealDriver:
    """Duck-typed d for heal_pokecenter: heals on the Nth nurse visit."""

    def __init__(self, heal_on_visit):
        self.heal_on = heal_on_visit
        self.gotos = 0
        self.healed = False
        self.on_counter = False
        self.steps = []
        self.emu = FakeEmu()
        self.names = None

        class M:
            def wait_for(self, pred, timeout_frames=600):
                return False              # no YES/NO box in the fake flow

            def select_label(self, label, **kw):
                return True

        self.menu = M()

    def map_name(self):
        return "VIOLET_POKECENTER_1F"

    def textbox(self):
        return False

    def goto(self, x, y, label=""):
        self.gotos += 1
        self.on_counter = True            # standing at the nurse counter
        if self.gotos >= self.heal_on:
            self.healed = True
        return True

    def step_dir(self, mv):
        self.steps.append(mv)
        if mv == "D":
            self.on_counter = False       # stepped off the counter tile
            return "moved"
        return "blocked"                  # facing turn only

    def press(self, seq):
        self.emu.tick(5)

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


def test_heal_pokecenter_success_steps_off_counter(monkeypatch):
    """After a confirmed heal the player steps south off the counter tile
    so no residual nurse prompt is armed (two leg-2 wedges)."""
    d = FakeHealDriver(heal_on_visit=1)
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": d.party()})
    trek.heal_pokecenter(d)
    assert d.steps and d.steps[-1] == "D"
    assert d.steps.count("D") == 1        # one step away, not a walk
    assert d.on_counter is False          # prompt can't re-arm


def test_heal_pokecenter_failure_never_steps_away(monkeypatch):
    """Failure paths unchanged: no step-away when the heal didn't land."""
    d = FakeHealDriver(heal_on_visit=99)
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": d.party()})
    with pytest.raises(RuntimeError, match="not fully healed"):
        trek.heal_pokecenter(d)
    assert "D" not in d.steps


def test_mart_buy_retalks_once_when_shop_never_opens():
    d = bare_driver()                     # blank screen: no ¥ ever
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    d.press = lambda seq: d.emu.tick(5)
    talks = []
    d.talk_to = lambda x, y, label="": talks.append((x, y)) or "talked"
    with pytest.raises(RuntimeError, match="shop menu did not open"):
        d.mart_buy(1, 3, "POTION")    # registry actions fail LOUDLY now
    assert len(talks) == 2            # first call + exactly one retry


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


# -- save dirty-screen guard (wren pt3: stuck pack baked into wren.state) ----

def save_driver(tmp_path):
    d = bare_driver()
    d.state_path = tmp_path / "work.state"
    d.textbox = lambda: False
    d.status = lambda: "ok"
    d.saved = []
    d.emu.save = lambda p: d.saved.append(Path(p))
    return d


def test_save_refuses_open_menu(tmp_path):
    """A live menu cursor on screen (stuck pack layer) refuses the save
    after a bounded B recovery -- baking it in poisons every fork."""
    d = save_driver(tmp_path)
    d.emu.rows[8] = " ▶USE".ljust(20)
    presses = []
    d.press = lambda seq: presses.append(seq) or d.emu.tick(5)
    with pytest.raises(RuntimeError, match="menu cursor"):
        d.save()
    assert d.saved == []
    assert sum(1 for p in presses if p.startswith("B")) == 4   # bounded


def test_save_succeeds_after_recovery_cleanup(tmp_path):
    """The bounded B recovery closes the stray layer: save proceeds."""
    d = save_driver(tmp_path)
    d.emu.rows[8] = " ▶USE".ljust(20)

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("B"):
            d.emu.rows[8] = " " * 20     # the B closes the stray layer

    d.press = press
    d.save()
    assert d.saved == [d.state_path]


def test_save_force_bypasses_dirty_screen(tmp_path):
    d = save_driver(tmp_path)
    d.emu.rows[8] = " ▶USE".ljust(20)
    presses = []
    d.press = lambda seq: presses.append(seq)
    d.save(force=True)
    assert d.saved == [d.state_path]
    assert presses == []                 # no recovery even attempted


def test_save_refuses_mid_battle_without_b_mash(tmp_path):
    d = save_driver(tmp_path)
    d.emu.u8["wBattleMode"] = 1
    presses = []
    d.press = lambda seq: presses.append(seq)
    with pytest.raises(RuntimeError, match="battle"):
        d.save()
    assert presses == []                 # never B-mash inside a battle


# -- default battle policy plumbing (Whitney lesson, wren pt3) ----------------

def fight_world(d, monkeypatch):
    """Wire fight()'s collaborators so a REAL fight() run records the
    policy handed to Battle.play."""
    played = []

    class FakeBattle:
        def __init__(self, emu, names, bdata):
            self.emu = emu

        def enemy(self):
            return {"name": "MILTANK"}

        def play(self, policy=None, **kw):
            played.append(policy)
            self.emu.u8["wBattleMode"] = 0
            return "win"

    mon = {"name": "GATOR", "level": 29, "hp": 20, "max_hp": 40,
           "egg": False}
    monkeypatch.setattr(trek, "Battle", FakeBattle)
    monkeypatch.setattr(trek, "game_state", lambda *a, **k: {
        "player": {"money": 100}, "party": [mon]})
    d.names = None
    d.bdata = None
    d.state_path = None
    d.default_policy = None
    d._pending_nickname = None
    d._whiteout_pending = False
    d.whiteouts = 0
    d.whiteout_policy = "abort"
    d._resolve_learn_flow = lambda *a, **k: None
    d.flush_dialog = lambda *a, **k: "done"
    d.press = lambda seq: d.emu.tick(5)
    return played


def test_talk_to_intercept_uses_default_policy(monkeypatch):
    """A trainer battle triggered by talk_to (gym leaders!) obeys the
    pre-armed default_policy instead of silently fighting default."""
    d = bare_driver()
    played = fight_world(d, monkeypatch)

    def custom(rows, me, enemy):
        return None

    d.default_policy = custom
    d._approach_cell = lambda x, y: (x - 1, y)
    d.goto = lambda *a, **k: True
    d.step_dir = lambda f: "moved"

    def flush(*a, **k):
        d.emu.u8["wBattleMode"] = 1      # trainer script starts the fight
        return "done"

    d.flush_dialog = flush
    assert d.talk_to(5, 5) == "battle"
    assert played == [custom]


def test_explicit_fight_policy_overrides_default(monkeypatch):
    d = bare_driver()
    played = fight_world(d, monkeypatch)
    d.default_policy = lambda rows, me, enemy: "flee"

    def explicit(rows, me, enemy):
        return None

    d.emu.u8["wBattleMode"] = 1
    d.fight(policy=explicit)
    assert played == [explicit]


def test_fight_no_policy_falls_back_to_default(monkeypatch):
    d = bare_driver()
    played = fight_world(d, monkeypatch)

    def default(rows, me, enemy):
        return None

    d.default_policy = default
    d.emu.u8["wBattleMode"] = 1
    d.fight()
    assert played == [default]


# -- use_item REVIVE: fainted-target party menu (wren pt3 live repro) ---------

def revive_world(d, monkeypatch, start_row=3):
    """Bag holds 1 REVIVE at pocket index 0; the party menu opens with a
    persisted mid-list cursor (wMenuCursorY row `start_row`); slot 1 is
    fainted. Only an A on the fainted row consumes the item."""
    world = {"qty": 1, "hp": [24, 0, 30], "cur": 0, "party_open": False,
             "wrong": []}
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: 0)
    monkeypatch.setattr(trek, "bag_quantity", lambda *a, **k: world["qty"])
    monkeypatch.setattr(trek, "goto_pocket", lambda menu, pocket: True)
    monkeypatch.setattr(trek, "cancel_pack", lambda menu: None)
    d.emu.u8["wMenuCursorY"] = start_row + 1   # persisted, NOT row 0

    class M:
        def select_label(self, label, max_presses=14):
            if label == "USE":
                world["party_open"] = True     # target list appears
            return True

        def wait_for_label(self, label, timeout_frames=300):
            return True

        def wait_for(self, pred, timeout_frames=600):
            return True

        def scroll_abs(self):
            return world["cur"]

        def cursor_row(self):
            return (2, "REVIVE      ×  1")

    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("START"):
            d.emu.rows[5] = "  ▶PACK".ljust(20)   # START menu paints
        elif seq.startswith("B"):
            d.emu.rows[5] = " " * 20              # pack closes
        elif seq.startswith("U"):
            d.emu.u8["wMenuCursorY"] = max(1, d.emu.u8["wMenuCursorY"] - 1)
        elif seq.startswith("D"):
            d.emu.u8["wMenuCursorY"] = min(3, d.emu.u8["wMenuCursorY"] + 1)
        elif seq.startswith("A") and world["party_open"]:
            row = d.emu.u8["wMenuCursorY"] - 1
            if row == 1 and world["hp"][1] == 0 and world["qty"]:
                world["qty"] -= 1              # revive consumed
                world["hp"][1] = 15            # target back above zero
            elif row != 1:
                world["wrong"].append(row)     # "won't have any effect"

    d.press = press
    return world


def test_use_item_revive_fainted_slot1_mid_list_cursor(monkeypatch):
    """REVIVE on fainted slot 1 with the party cursor persisted mid-list:
    WRAM-steered targeting hits the right row (blind D-counts from an
    assumed top row picked a healthy mon -- live repro: returned False,
    bag never decremented)."""
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    world = revive_world(d, monkeypatch, start_row=3)
    assert d.use_item("REVIVE", target_slot=1) is True
    assert world["qty"] == 0             # bag decremented
    assert world["hp"][1] > 0            # target revived
    assert world["wrong"] == []          # never A'd a healthy mon
