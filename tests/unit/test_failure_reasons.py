"""No primitive may fail silently.

Every one of these paths used to answer a bare ``False``. The cost was
paid live, more than once: ``use_item`` returning False with the bag
untouched and nothing to say which of six menu layers had missed, and
``select_label('PACK')`` reporting success from a cursor glyph while the
pack never opened (gotcha 2 -- the frame a menu is drawn its input loop
is not running yet).

So the contract these tests pin is: a falsy return leaves a non-empty,
DISTINCT reason string behind -- ``Menus.last_reason``,
``Battle.last_reason``, ``Driver.last_menu_reason``,
``Driver.last_step_reason``, ``Driver.last_goto_reason``.
"""
import logging

import pytest

from trek import Driver
from crystalagent.battle import Battle
from crystalagent.menus import Menus

pytestmark = pytest.mark.unit


class FakeEmu:
    """Screen + WRAM stub: presses are recorded, never interpreted."""

    def __init__(self, rows=None, u8=None):
        self.frame = 0
        self.rows = rows or [" " * 20 for _ in range(18)]
        self.u8 = dict(u8 or {})
        self.pressed = []

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)

    def read_be(self, sym, n):
        return self.u8.get(sym, 0)

    def run_sequence(self, seq):
        # seq is Menus.press's PARSED sequence; keep it as text so tests
        # can ask "did it press A?"
        self.pressed.append(str(seq))
        self.frame += 24


def screen(*lines):
    rows = [l.ljust(20)[:20] for l in lines]
    return rows + [" " * 20] * (18 - len(rows))


def menus(rows=None, u8=None):
    return Menus(FakeEmu(rows, u8))


# -- Menus ---------------------------------------------------------------

def test_select_label_says_the_cursor_never_reached_the_label():
    m = menus(screen("▶ POKEMON", "  PACK", "  SAVE"))
    assert m.select_label("NOPE", max_presses=2) is False
    assert "NOPE" in m.last_reason and "DOWN presses" in m.last_reason


def test_select_label_with_expect_fails_when_the_press_is_swallowed():
    """The exact PACK bug: the cursor IS on the row, A is pressed, and the
    expected screen never comes up. Reporting True there is what let
    use_item run its whole flow against a closed pack."""
    m = menus(screen("▶ PACK"))
    ok = m.select_label("PACK", expect=lambda rows: "ITEM POCKET" in
                        "".join(rows), expect_tries=2)
    assert ok is False
    assert "PACK" in m.last_reason and "not reached" in m.last_reason
    assert m.emu.pressed, "it must actually have tried to confirm"


def test_select_label_with_expect_succeeds_once_the_state_is_reached():
    emu = FakeEmu(screen("▶ PACK"))
    m = Menus(emu)

    def pred(rows):
        # the pack "opens" only after a confirm has been pressed
        return any("'a'" in p for p in emu.pressed)

    assert m.select_label("PACK", expect=pred) is True
    assert m.last_reason is None


def test_wait_for_reports_the_timeout():
    m = menus()
    assert m.wait_for(lambda rows: False, timeout_frames=50) is False
    assert "predicate never true" in m.last_reason


def test_select_row_text_reports_a_label_that_is_not_there():
    m = menus(screen("  POTION   × 2", "  ANTIDOTE × 1"))
    assert m.select_row_text("ETHER", max_presses=2) is False
    assert "ETHER" in m.last_reason


def test_select_abs_reports_where_it_stopped():
    m = menus(u8={"wMenuScrollPosition": 0, "wMenuCursorY": 1})
    assert m.select_abs(7, max_steps=3) is False
    assert "select_abs(7)" in m.last_reason and "index 0" in m.last_reason


def test_has_label_sees_a_glyph_that_is_not_the_leftmost():
    """A battle party list keeps its own ▷ painted while a submenu draws
    ▶ over it; a leftmost-only read answers about the wrong list."""
    m = menus()
    rows = screen("▷ GATOR   ▶ SWITCH")
    assert m.has_label(rows, "SWITCH") is True
    assert m.has_label(rows, "GATOR") is True
    assert m.cursor_labels() == []          # blank screen: no glyphs at all


# -- Battle --------------------------------------------------------------

def bare_battle(rows=None, u8=None):
    b = Battle.__new__(Battle)
    b.emu = FakeEmu(rows, u8)
    b.menu = Menus(b.emu)
    b.names = None
    b.data = None
    b.switch_refused = False
    return b


def test_battle_pocket_select_says_where_the_cursor_stuck():
    b = bare_battle(u8={"wMenuScrollPosition": 0, "wMenuCursorY": 1})
    assert b._pocket_select(9, "POTION", max_steps=6) is False
    assert "pocket_select(POTION)" in b.last_reason
    assert "pinned" in b.last_reason


def test_battle_party_target_says_where_the_cursor_stuck():
    b = bare_battle(u8={"wMenuCursorY": 1})
    assert b._party_target(3, max_steps=6) is False
    assert "party_target(3)" in b.last_reason


# -- Driver menu helpers -------------------------------------------------

def bare_driver(rows=None, u8=None):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(rows, u8)
    d.menu = Menus(d.emu)
    d.names = None
    d.settle = lambda **kw: None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    d.menu_open = lambda: False
    d.close_menus = lambda *a, **k: None
    d.press = lambda seq: d.emu.run_sequence(seq)
    return d


def test_driver_pocket_select_reports_the_pinned_cursor():
    d = bare_driver(u8={"wMenuScrollPosition": 0, "wMenuCursorY": 1})
    assert d._pocket_select(9, "POTION", max_steps=6) is False
    assert "pocket_select(POTION)" in d.last_menu_reason


def test_driver_party_target_reports_the_pinned_cursor():
    d = bare_driver(u8={"wMenuCursorY": 1})
    assert d._party_target(4, max_steps=6) is False
    assert "party_target(4)" in d.last_menu_reason


def test_items_pocket_by_screen_reports_an_empty_screen():
    d = bare_driver(screen("  nothing here"))
    assert d._items_pocket_by_screen() is False
    assert "items_pocket" in d.last_menu_reason


def test_start_menu_pack_row_reports_a_menu_that_never_drew():
    d = bare_driver()
    d._wait_screen = lambda *a, **k: False
    assert d._start_menu_pack_row() is False
    assert "start_menu" in d.last_menu_reason


def test_open_pack_reports_a_pocket_that_never_came_up():
    d = bare_driver()
    d._start_menu_pack_row = lambda: True
    d._pack_up = lambda rows=None: False
    d.menu.select_label = lambda *a, **k: True
    d._items_pocket_by_screen = lambda: False
    assert d._open_pack(max_confirms=2) is False
    assert "open_pack" in d.last_menu_reason


# -- stepping ------------------------------------------------------------

def test_a_map_without_a_decoded_grid_is_recorded_not_shrugged_off():
    """A KeyError here silently turns every door on the map into "not a
    warp cell", which is how a held-step door never fires (gotcha 12)."""
    d = bare_driver()
    d.map_name = lambda: "NOWHERE"

    class NoGrid:
        surf = False

        def grid(self, name):
            raise KeyError(name)

    d.nav = NoGrid()
    assert d._is_warp_cell(3, 4) is False
    assert "warp-cell(3,4)" in d.last_step_reason
    assert "KeyError" in d.last_step_reason


def test_walk_records_why_it_gave_up():
    d = bare_driver()
    d.map_name = lambda: "ROUTE_39"
    d.pos = lambda: (1, 2, 5, 6)
    d._step = lambda mv: "blocked"
    assert d.walk("R*2") is False
    assert "blocked stepping R" in d.last_step_reason
    assert "ROUTE_39" in d.last_step_reason


# -- reasons must be DISTINCT -------------------------------------------

def test_no_two_primitives_share_a_reason_string():
    """A shared reason is as useless as no reason: it cannot tell the
    caller which layer missed."""
    seen = []

    m = menus(screen("▶ POKEMON"))
    m.select_label("NOPE", max_presses=1)
    seen.append(m.last_reason)
    m = menus()
    m.wait_for(lambda rows: False, timeout_frames=30)
    seen.append(m.last_reason)
    m = menus(screen("  POTION"))
    m.select_row_text("ETHER", max_presses=1)
    seen.append(m.last_reason)
    m = menus(u8={"wMenuCursorY": 1})
    m.select_abs(4, max_steps=2)
    seen.append(m.last_reason)

    b = bare_battle(u8={"wMenuCursorY": 1})
    b._party_target(3, max_steps=4)
    seen.append(b.last_reason)
    b = bare_battle(u8={"wMenuScrollPosition": 0, "wMenuCursorY": 1})
    b._pocket_select(9, "POTION", max_steps=4)
    seen.append(b.last_reason)

    d = bare_driver(u8={"wMenuCursorY": 1})
    d._party_target(4, max_steps=4)
    seen.append(d.last_menu_reason)
    d = bare_driver(screen("  nothing"))
    d._items_pocket_by_screen()
    seen.append(d.last_menu_reason)

    assert all(seen), seen
    assert len(set(seen)) == len(seen), seen


# -- save(force=True) ----------------------------------------------------

def test_forcing_a_save_over_a_dirty_screen_says_so(tmp_path, caplog):
    """force is legitimate; being QUIET about it is not. A state baked
    with a menu open reloads with dead movement (gotcha 7)."""
    d = bare_driver(screen("▶ POKEMON", "  PACK"))
    d.battle = lambda: False
    d.state_path = tmp_path / "fork.state"
    d.status = lambda: "status"
    saved = []
    d.emu.save = saved.append
    d.emu.frame = 1000
    with caplog.at_level(logging.WARNING, logger="trek"):
        d.save(force=True)
    assert saved == [d.state_path]
    text = caplog.text
    assert "saving OVER blockers" in text and "menu cursor" in text


def test_a_clean_screen_forced_save_says_nothing(tmp_path, caplog):
    d = bare_driver()
    d.battle = lambda: False
    d.state_path = tmp_path / "fork.state"
    d.status = lambda: "status"
    d.emu.save = lambda p: None
    with caplog.at_level(logging.WARNING, logger="trek"):
        d.save(force=True)
    assert "saving OVER blockers" not in caplog.text
