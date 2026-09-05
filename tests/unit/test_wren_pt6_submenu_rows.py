"""claude-wren pt6: text-targeted submenu selection.

The party slot submenu lists the selected mon's FIELD MOVES above the
fixed STATS/SWITCH rows ('GATOR's submenu lists CUT/STRENGTH/SURF above
SWITCH'), so its row count varies per mon, and pack windows scroll --
blind positional DOWN counts fired Strength in the field. Selection
must find the row by TEXT, step the exact delta from the cursor glyph,
and verify after every press (Menus.select_row_text, exposed as
Driver.select_menu_row)."""
import pytest

from crystalagent.battle import norm_item
from crystalagent.driver import Driver
from crystalagent.driver.inventory import _item_row_matches
from crystalagent.menus import Menus

pytestmark = pytest.mark.unit

W = 20


class FakeEmu:
    def __init__(self, world):
        self.world = world
        self.frame = 0

    def screen_text(self):
        return self.world.render()


def menus_for(world):
    m = Menus(FakeEmu(world))
    m.press = world.press          # keystrokes drive the fake world
    return m


def driver_for(world):
    d = Driver.__new__(Driver)
    d.menu = menus_for(world)
    return d


class SubmenuWorld:
    """Party slot submenu: field moves above STATS/SWITCH/ITEM/CANCEL,
    drawn in a right-hand box while the party list (with its own ▶
    glyph) stays painted behind it. The 1D submenu uses the ▷ tile."""

    def __init__(self, field_moves, cursor=0):
        self.entries = list(field_moves) + ["STATS", "SWITCH",
                                            "ITEM", "CANCEL"]
        self.cursor = cursor
        self.confirmed = None
        self.presses = []

    def render(self):
        rows = [" " * W for _ in range(18)]
        rows[2] = "▶GATOR".ljust(W)              # party cursor, behind
        rows[4] = " TOGEPI".ljust(W)
        for i, e in enumerate(self.entries):
            glyph = "▷" if i == self.cursor else " "
            rows[6 + i] = (" " * 11 + glyph + e).ljust(W)[:W]
        return rows

    def press(self, seq):
        self.presses.append(seq)
        k = seq[0]
        if k == "D":
            self.cursor = min(len(self.entries) - 1, self.cursor + 1)
        elif k == "U":
            self.cursor = max(0, self.cursor - 1)
        elif k == "A":
            self.confirmed = self.entries[self.cursor]


@pytest.mark.parametrize("field_moves",
                         [[], ["CUT"], ["CUT", "STRENGTH", "SURF"]])
def test_switch_selected_under_variable_field_moves(field_moves):
    """0, 1, and 3 field moves above SWITCH: the row TEXT is targeted,
    never a positional press count, and exactly one A confirms it."""
    w = SubmenuWorld(field_moves)
    m = menus_for(w)
    assert m.select_row_text("SWITCH")
    assert w.confirmed == "SWITCH"                # never a field move
    assert sum(1 for p in w.presses if p[0] == "A") == 1


def test_switch_from_persisted_cursor_below_moves_up():
    """The submenu cursor can persist below SWITCH (e.g. on CANCEL):
    the helper steps UP, never wraps blindly DOWN."""
    w = SubmenuWorld(["CUT"], cursor=4)           # CANCEL
    m = menus_for(w)
    assert m.select_row_text("SWITCH")
    assert w.confirmed == "SWITCH"
    assert any(p[0] == "U" for p in w.presses)
    assert not any(p[0] == "D" for p in w.presses)


def test_driver_select_menu_row_delegates():
    w = SubmenuWorld(["SURF"])
    d = driver_for(w)
    assert d.select_menu_row("SWITCH")
    assert w.confirmed == "SWITCH"


def test_cut_label_not_confused_with_nickname():
    """'CUT' must match the field-move row, not a party nickname that
    merely contains it (word-bounded match); confirm=False positions
    without pressing A (use_cut presses its own long A)."""
    w = SubmenuWorld(["CUT"], cursor=2)           # start on ITEM
    base = w.render

    def render():
        rows = base()
        rows[4] = " CUTIE".ljust(W)               # nickname behind box
        return rows

    w.render = render
    m = menus_for(w)
    assert m.select_row_text("CUT", confirm=False)
    assert w.entries[w.cursor] == "CUT"
    assert w.confirmed is None


def test_missing_label_never_blind_confirms():
    """A label that is nowhere in the menu: search pins at both list
    edges and returns False with zero A presses."""
    w = SubmenuWorld([])
    m = menus_for(w)
    assert not m.select_row_text("SURF", max_presses=10)
    assert w.confirmed is None
    assert not any(p[0] == "A" for p in w.presses)


# -- pack pocket (scrolling window) ------------------------------------------

ITEMS = ["POTION", "ANTIDOTE", "PARLYZ HEAL", "SUPER POTION", "REPEL",
         "ESCAPE ROPE", "FULL HEAL", "CANCEL"]


class PackWorld:
    """Scrolling items pocket: a 5-row window over the list; the cursor
    rides to the window edge before the window scrolls (pack.asm
    restores the cursor/scroll between opens, so a fresh open can start
    mid-list)."""
    WINDOW = 5

    def __init__(self, scroll=0, cur=0):
        self.scroll, self.cur = scroll, cur
        self.confirmed = None
        self.presses = []

    def render(self):
        rows = [" " * W for _ in range(18)]
        for i in range(self.WINDOW):
            idx = self.scroll + i
            if idx >= len(ITEMS):
                break
            glyph = "▶" if i == self.cur else " "
            rows[2 + i] = (" " * 4 + glyph + ITEMS[idx]).ljust(W)[:W]
        return rows

    def press(self, seq):
        self.presses.append(seq)
        k = seq[0]
        if k == "D":
            if self.cur < self.WINDOW - 1:
                self.cur += 1
            elif self.scroll + self.WINDOW < len(ITEMS):
                self.scroll += 1
        elif k == "U":
            if self.cur > 0:
                self.cur -= 1
            elif self.scroll > 0:
                self.scroll -= 1
        elif k == "A":
            self.confirmed = ITEMS[self.scroll + self.cur]


def _item_match(name):
    want = norm_item(name)
    return lambda t: _item_row_matches(t, want)


def test_pack_item_found_on_scrolled_window():
    """Pocket reopened mid-list (scroll=2): the wanted row is visible
    but NOT at its top-of-list screen position -- fixed-row scrapes
    miss it, text targeting does not."""
    w = PackWorld(scroll=2, cur=0)
    d = driver_for(w)
    assert d.select_menu_row("SUPER POTION",
                             match=_item_match("SUPER POTION"))
    assert w.confirmed == "SUPER POTION"


def test_pack_item_below_window_scrolls_down():
    w = PackWorld(scroll=0, cur=0)
    m = menus_for(w)
    assert m.select_row_text("FULL HEAL", match=_item_match("FULL HEAL"),
                             max_presses=20)
    assert w.confirmed == "FULL HEAL"


def test_pack_item_above_window_reverses_at_pin():
    """DOWN-only walks can never climb back up (leg-2 'no potion
    visible' with 2 in the bag): once the window pins at the bottom the
    search reverses. 'POTION' also never falsely matches the visible
    'SUPER POTION' row (normalized prefix match, both directions)."""
    w = PackWorld(scroll=3, cur=0)
    m = menus_for(w)
    assert m.select_row_text("POTION", match=_item_match("POTION"),
                             max_presses=25)
    assert w.confirmed == "POTION"


def test_stale_glyph_leftover_does_not_shadow():
    """A stale ▷ from the START menu higher up (wren pt4 shadow bug):
    only cursors in the target's own column band count, so the live
    row confirms with zero wandering presses."""
    w = PackWorld(scroll=2, cur=1)                # already on the item
    base = w.render

    def render():
        rows = base()
        rows[0] = "        ▷TOWN MAP   "[:W]      # stale leftover
        return rows

    w.render = render
    m = menus_for(w)
    assert m.select_row_text("SUPER POTION",
                             match=_item_match("SUPER POTION"),
                             confirm_seq="A:6 .:18")
    assert w.confirmed == "SUPER POTION"
    assert not any(p[0] in "UD" for p in w.presses)
