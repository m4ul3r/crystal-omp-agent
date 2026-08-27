"""The mart's purchase confirmation, and a frame budget that is not a loss.

Grievance 7 (PROGRESS pt12): `mart_buy` raised "FULL RESTORE x6 failed
(bag 0 -> 0, bought=False)" for an item that was in stock. Two causes,
both live-reproduced at the Indigo Plateau mart (clerk (11,7)):

  * the clerk's BUY/SELL/QUIT menu comes FIRST and nothing chose BUY, so
    the priced list -- the only screen with a '¥' -- never appeared;
  * confirming the quantity opens "N ITEM(S) will be ¥NNNN." over a
    YES/NO box. flush_dialog refuses choice boxes (gotcha 13), so the
    purchase never happened -- while `bought` was set to True anyway.

Grievance 8 (#82): fight() reported "UNRESOLVED (timeout) and the battle is
STILL LIVE" against Lance with five of six down. The frame budget is a
clock, not an outcome: re-entering play() resumes the same battle.
"""
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

    def read_u8(self, name):
        return self.u8.get(name, 0)


def rows(*lines):
    out = [" " * 20 for _ in range(18)]
    for i, text in lines:
        out[i] = text.ljust(20)[:20]
    return out


# the live screens, in order
CLERK_MENU = rows((1, "Welcome! How may I"), (4, "▶BUY"), (6, " SELL"),
                  (8, " QUIT"))
BUY_LIST = rows((1, "  ¥29370"), (4, " ▶FULL RESTORE"), (5, "     ¥3000"))
PICKER = rows((14, "How many?"), (16, "  ×06  ¥18000"))
CONFIRM = rows((14, "6 FULL RESTORE(S)"), (16, "will be ¥18000."),
               (8, "▶YES"), (10, " NO"))


def shop_driver():
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.settle = lambda **kw: None
    d.press = lambda seq: d.emu.tick(5)
    d.textbox = lambda: False
    d.menu_open = lambda: False
    return d


# -- shop screen classifiers -------------------------------------------------

def test_shop_list_is_the_screen_with_the_money_window():
    d = shop_driver()
    assert d._shop_list_up(BUY_LIST) is True
    assert d._shop_list_up(CLERK_MENU) is False    # BUY/SELL/QUIT: no ¥


def test_quantity_picker_is_detected_so_it_cannot_be_left_open():
    d = shop_driver()
    assert d._shop_picker_up(PICKER) is True
    assert d._shop_picker_up(BUY_LIST) is False


def test_shop_exit_keeps_pressing_b_until_the_picker_is_gone():
    """The old exit stopped at "no ¥ and no cursor", which a quantity
    picker satisfies while still owning every input press."""
    d = shop_driver()
    d.emu.rows = PICKER
    presses = []

    def press(seq):
        presses.append(seq)
        d.emu.tick(5)
        if seq.startswith("B"):
            d.emu.rows = [" " * 20 for _ in range(18)]
    d.press = press
    assert d._shop_exit() is True
    assert any(p.startswith("B") for p in presses)


def test_shop_exit_reports_a_picker_it_could_not_close(caplog):
    d = shop_driver()
    d.emu.rows = PICKER                            # B never clears it
    assert d._shop_exit(max_presses=3) is False


# -- the purchase actually happens ------------------------------------------

def buying_driver(monkeypatch, stock=6):
    """A mart where every screen advances the way the real one did:
    clerk menu -> buy list -> quantity picker -> "will be ¥NNNN." YES/NO
    -> the item in the bag."""
    d = shop_driver()
    d.names = None
    d.bag = {"FULLRESTORE": 0}
    d.emu.rows = CLERK_MENU
    d.state = {"screen": "clerk"}
    d.flush_dialog = lambda *a, **k: "done"
    d.talk_to = lambda x, y, label="": "talked"
    d.select_menu_row = lambda label, **kw: (
        d.state.__setitem__("screen", "list")
        or d.emu.__setattr__("rows", BUY_LIST) or True)
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: 0)
    d.emu.sym = {"wItems": (1, 0xD892), "wBalls": (1, 0xD8D7)}
    d.emu.u8["wNumItems"] = 1
    # wItems is [item id, quantity] pairs; the quantity is the bag count
    d.emu.read = lambda where, n=1: bytes([1, d.bag["FULLRESTORE"]])

    class M:
        """Only wait_for is used by mart_buy."""

        def wait_for(self, pred, timeout_frames=600):
            for _ in range(8):
                if pred(d.emu.screen_text()):
                    return True
                d.press(".:8")
            return False
    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if not seq.startswith("A"):
            return
        if d.state["screen"] == "list":
            d.state["screen"] = "picker"
            d.emu.rows = PICKER
        elif d.state["screen"] == "picker":
            d.state["screen"] = "confirm"
            d.emu.rows = CONFIRM
        elif d.state["screen"] == "confirm":
            d.state["screen"] = "done"
            d.bag["FULLRESTORE"] += stock
            d.emu.rows = BUY_LIST
    d.press = press
    d._shop_exit = lambda **kw: True
    return d


def test_mart_buy_answers_the_purchase_yes_box(monkeypatch):
    """Without this the bag never changes: "bag 0 -> 0, bought=True"."""
    d = buying_driver(monkeypatch)
    assert d.mart_buy(11, 7, "FULL RESTORE", 6) is True
    assert d.state["screen"] == "done"       # the YES box was answered
    assert d.bag["FULLRESTORE"] == 6


def test_mart_buy_fails_when_the_yes_box_is_never_answered(monkeypatch):
    """The old flow handed the confirmation to flush_dialog, which refuses
    choice boxes -- and then reported bought=True with an empty bag."""
    d = buying_driver(monkeypatch, stock=0)
    with pytest.raises(RuntimeError, match=r"bag 0 -> 0"):
        d.mart_buy(11, 7, "FULL RESTORE", 6)


def test_mart_buy_raises_when_the_buy_list_never_opens():
    d = shop_driver()
    d.names = None
    d.flush_dialog = lambda *a, **k: "done"
    talks = []
    d.talk_to = lambda x, y, label="": talks.append((x, y)) or "talked"
    d.emu.sym = {"wItems": (1, 0xD892)}
    d.emu.read = lambda where, n=1: bytes(n)
    with pytest.raises(RuntimeError, match="buy list did not open"):
        d.mart_buy(1, 3, "POTION")
    assert len(talks) == 2                     # one retry, then loud


# -- a frame budget is not an outcome (#82) ---------------------------------

class FakeBattle:
    """play() that needs three budgets to finish, like Lance did."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def play(self, **kw):
        self.calls += 1
        return self.outcomes.pop(0) if self.outcomes else "won"

    def enemy(self):
        return {"name": "DRAGONITE", "level": 50}


def fight_driver(monkeypatch, battle, live_after):
    """`live_after`: how many play() calls the battle stays live for."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.names = None
    d.bdata = None
    d.default_policy = None
    d.state_path = None
    d.whiteouts = 0
    d.encounter_events = None
    d._pending_nickname = None
    d._fight_diag_prints = 0
    d.battle = lambda: 2 if battle.calls < live_after else 0
    d.keyboard_open = lambda: False
    d.map_name = lambda: "LANCES_ROOM"
    d.pos = lambda: (0, 0, 5, 5)
    d.lead = lambda: {"name": "PANIC", "hp": 100, "max_hp": 200}
    d.flush_dialog = lambda *a, **k: "done"
    d._resolve_learn_flow = lambda *a, **k: None
    d._party_moves = lambda: {}
    d._diff_learned_moves = lambda before: None
    d._log_turns = lambda b, s, o: (0, 0)
    d._turn_policy = lambda *a, **k: ({"turns": 3, "autos": 0}, None)
    d._resolve_nickname = lambda nick, species: None
    d._battle_text_handler = lambda rows: None
    d._fight_diag = lambda b, outcome: None
    monkeypatch.setattr(trek, "Battle", lambda *a, **k: battle)
    monkeypatch.setattr(trek, "game_state", lambda emu, names: {
        "player": {"money": 100}, "party": []})
    return d


def test_fight_resumes_a_spent_frame_budget_instead_of_reporting_it(
        monkeypatch, caplog):
    battle = FakeBattle(["timeout", "timeout", "won"])
    d = fight_driver(monkeypatch, battle, live_after=3)
    d.fight()
    assert battle.calls == 3                   # two resumes, then the win
    assert "UNRESOLVED" not in caplog.text


def test_fight_stops_resuming_at_the_cap_and_says_recalling_resumes(
        monkeypatch, caplog):
    battle = FakeBattle(["timeout"] * 9)
    d = fight_driver(monkeypatch, battle, live_after=99)
    d.fight(resume=2)
    assert battle.calls == 3                   # first budget + 2 resumes
    assert "UNRESOLVED" in caplog.text
    assert "RESUMES" in caplog.text             # the caller is told how


def test_fight_never_resumes_a_wedged_battle(monkeypatch):
    """'stuck'/'stalled'/'wedged' mean the battle stopped CHANGING; more
    frames buy nothing, so those must not loop."""
    battle = FakeBattle(["stalled"] * 5)
    d = fight_driver(monkeypatch, battle, live_after=99)
    d.fight()
    assert battle.calls == 1
