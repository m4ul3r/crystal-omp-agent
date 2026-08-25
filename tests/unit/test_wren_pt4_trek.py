"""wren pt4 frictions (trek.py side): two-word item names through
use_item/_pocket_select (SUPER POTION vs bag key SUPERPOTION) and
move-learn transparency (LEARN log lines + d.move_changes)."""
import logging

import pytest

import trek
from trek import Driver, _item_row_matches, _norm_item

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


# -- _item_row_matches: normalize BOTH sides ---------------------------------

def test_row_match_two_word_row_vs_bag_key():
    """Screen shows 'SUPER POTION' (space); the bag key is 'SUPERPOTION'."""
    assert _item_row_matches("SUPER POTION", _norm_item("SUPERPOTION"))
    assert _item_row_matches("SUPER POTION", _norm_item("SUPER POTION"))
    assert _item_row_matches("PARLYZ HEAL", _norm_item("PARLYZHEAL"))


def test_row_match_tolerates_quantity_and_clipping():
    # quantity digits bleeding onto the scrape
    assert _item_row_matches("SUPER POTION   ×  2", _norm_item("SUPERPOTION"))
    # right-edge tile loss (name clipped by up to 2 chars)
    assert _item_row_matches("SUPER POTIO", _norm_item("SUPER POTION"))


def test_row_match_never_crosses_items():
    # 'POTION' can never confirm a SUPER/HYPER/MAX POTION row or vice versa
    assert not _item_row_matches("SUPER POTION", _norm_item("POTION"))
    assert not _item_row_matches("POTION", _norm_item("SUPER POTION"))
    assert not _item_row_matches("HYPER", _norm_item("HYPER POTION"))
    assert not _item_row_matches("ANTIDOTE  ×  1", _norm_item("POTION"))
    assert not _item_row_matches("", _norm_item("POTION"))


# -- _pocket_select: two-word rows ------------------------------------------

def pocket_driver(start, items, row_text=None):
    """Fake scrolling pocket (mirrors test_wren_frictions.pocket_driver):
    WRAM cursor starts at `start`; rows show SPACED names."""
    d = bare_driver()
    state = {"cur": start, "confirmed": [], "ups": 0}

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
            state["cur"] = min(len(items) - 1, state["cur"] + 1)
        elif seq.startswith("A"):
            state["confirmed"].append(state["cur"])

    d.press = press
    return d, state


POCKET = ["SUPER POTION", "POTION", "ANTIDOTE", "POKE BALL"]


def test_pocket_select_two_word_row():
    d, state = pocket_driver(start=2, items=POCKET)
    assert d._pocket_select(0, "SUPER POTION") is True
    assert state["confirmed"] == [0]


def test_pocket_select_two_word_alias():
    d, state = pocket_driver(start=2, items=POCKET)
    assert d._pocket_select(0, "SUPERPOTION") is True
    assert state["confirmed"] == [0]


def test_pocket_select_rescan_when_stale_glyph_shadows():
    """cursor_row() returns the FIRST glyph row; a stale leftover (e.g.
    START-menu remnant) shadows the live selection. The rescan finds the
    ACTIVE ▶ row naming the item and still confirms."""
    d, state = pocket_driver(start=0, items=POCKET, row_text="PACK")
    d.emu.rows[6] = ("       ▶SUPER POTION").ljust(20)[:20]
    assert d._pocket_select(0, "SUPER POTION") is True
    assert state["confirmed"] == [0]


def test_pocket_select_still_refuses_wrong_item():
    d, state = pocket_driver(start=0, items=POCKET, row_text="ANTIDOTE  ×  1")
    assert d._pocket_select(0, "SUPER POTION") is False
    assert state["confirmed"] == []


# -- use_item end to end: spaced screen rows, normalized bag keys ------------

def use_item_world(d, monkeypatch, consumed):
    """Wire use_item's collaborators. The fake bag is keyed by NORMALIZED
    names ('SUPERPOTION'), exactly like Driver._bag(); lookups normalize
    the caller's argument the way the real bag_item_index does. Screen
    rows show the SPACED names the pack really draws."""
    order = [_norm_item(n) for n in POCKET]
    world = {"cur": 2, "ups": 0,
             "bag": {"SUPERPOTION": 2, "POTION": 3, "ANTIDOTE": 1}}
    d.emu.u8["wMenuCursorY"] = 1          # party-menu cursor on row 0

    def fake_index(emu, names, item_name, pocket="items"):
        key = _norm_item(item_name)
        return order.index(key) if key in world["bag"] else None

    def fake_qty(emu, names, item_name, pocket="items"):
        return world["bag"].get(_norm_item(item_name))

    monkeypatch.setattr(trek, "bag_item_index", fake_index)
    monkeypatch.setattr(trek, "bag_quantity", fake_qty)
    monkeypatch.setattr(trek, "goto_pocket", lambda menu, pocket: True)
    monkeypatch.setattr(trek, "cancel_pack", lambda menu: None)

    class M:
        def select_label(self, label, max_presses=14):
            if label == "USE":
                world["bag"][_norm_item(consumed)] -= 1   # engine consumes
            return True

        def wait_for_label(self, label, timeout_frames=300):
            return True

        def wait_for(self, pred, timeout_frames=600):
            return True                    # target party list appeared

        def scroll_abs(self):
            return world["cur"]

        def cursor_row(self):
            return (2, POCKET[world["cur"]])   # spaced, as drawn

    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("START"):
            d.emu.rows[5] = "  ▶PACK".ljust(20)
        elif seq.startswith("U"):
            world["ups"] += 1
            world["cur"] = max(0, world["cur"] - 1)
        elif seq.startswith("D"):
            world["cur"] = min(len(POCKET) - 1, world["cur"] + 1)
        elif seq.startswith("B"):
            d.emu.rows[5] = " " * 20       # pack closes

    d.press = press
    return world


def test_use_item_two_word_name(monkeypatch):
    """Live repro (wren pt4): use_item('SUPER POTION') returned False."""
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    world = use_item_world(d, monkeypatch, consumed="SUPER POTION")
    assert d.use_item("SUPER POTION") is True
    assert world["bag"]["SUPERPOTION"] == 1   # bag read-back confirmed
    assert world["ups"] == 2                  # climbed the persisted cursor


def test_use_item_two_word_alias(monkeypatch):
    """use_item('SUPERPOTION') (no space) resolves the same item."""
    d = bare_driver()
    d.names = None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    world = use_item_world(d, monkeypatch, consumed="SUPERPOTION")
    assert d.use_item("SUPERPOTION") is True
    assert world["bag"]["SUPERPOTION"] == 1


# -- move-learn transparency (BITE -> SCARY FACE, 3 whiteouts) ----------------

def learn_driver(before, after, handler_frames=2):
    """Driver amid an on-screen learn flow: the prompt stays up for
    `handler_frames` handler calls, then resolves. _party_moves serves
    `before` on the first snapshot and `after` afterwards."""
    d = bare_driver()
    d.move_changes = []
    d.emu.rows[13] = "TRYING TO LEARN".ljust(20)
    state = {"handled": 0, "snapped": 0}

    def handler(rows):
        state["handled"] += 1
        d.emu.tick(20)
        if state["handled"] >= handler_frames:
            d.emu.rows[13] = " " * 20      # flow resolved
        return True

    def party_moves():
        state["snapped"] += 1
        return before if state["snapped"] == 1 else after

    d._battle_text_handler = handler
    d._party_moves = party_moves
    return d, state


def test_learn_flow_logs_and_records_replacement(caplog):
    before = [("GATOR", ["BITE", "WATER GUN", "RAGE", "SCRATCH"])]
    after = [("GATOR", ["SCARY FACE", "WATER GUN", "RAGE", "SCRATCH"])]
    d, _ = learn_driver(before, after)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("LEARN:")]
    assert lines == [
        "LEARN: GATOR forgot BITE -> learned SCARY FACE (slot 1)"]
    assert d.move_changes == [{"mon": "GATOR", "forgot": "BITE",
                               "learned": "SCARY FACE", "slot": 1}]


def test_learn_flow_declined_no_entries(caplog):
    moves = [("GATOR", ["BITE", "WATER GUN", "RAGE", "SCRATCH"])]
    d, _ = learn_driver(moves, [(n, list(m)) for n, m in moves])
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert d.move_changes == []
    assert not any(r.getMessage().startswith("LEARN:")
                   for r in caplog.records)


def test_learn_flow_empty_slot_no_entries(caplog):
    """A move landing in a previously EMPTY slot shifts no existing slot
    mapping: nothing recorded (the game's own text already announced it)."""
    before = [("GATOR", ["BITE", "WATER GUN"])]
    after = [("GATOR", ["BITE", "WATER GUN", "SCARY FACE"])]
    d, _ = learn_driver(before, after)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert d.move_changes == []
    assert not any(r.getMessage().startswith("LEARN:")
                   for r in caplog.records)


def test_learn_flow_no_prompt_never_snapshots():
    """The common no-flow call stays cheap: no party read, no diff."""
    d, state = learn_driver([("GATOR", ["BITE"])], [("GATOR", ["BITE"])])
    d.emu.rows[13] = " " * 20              # no learn prompt at all
    assert d._resolve_learn_flow() is True
    assert state["snapped"] == 0
    assert d.move_changes == []


def test_learn_flow_relabeled_mon_skipped():
    """Evolution (no nickname) changes the label mid-flow: skip rather
    than misattribute the diff."""
    before = [("CROCONAW", ["BITE", "WATER GUN"])]
    after = [("FERALIGATR", ["SCARY FACE", "WATER GUN"])]
    d, _ = learn_driver(before, after)
    assert d._resolve_learn_flow() is True
    assert d.move_changes == []


def test_move_changes_accumulate_across_flows():
    d, _ = learn_driver([("GATOR", ["BITE"])], [("GATOR", ["SLASH"])])
    d.move_changes = [{"mon": "REED", "forgot": "GUST",
                       "learned": "WING ATTACK", "slot": 2}]
    d._resolve_learn_flow()
    assert [c["mon"] for c in d.move_changes] == ["REED", "GATOR"]
