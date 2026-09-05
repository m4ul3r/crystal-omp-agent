"""Pack detection by SCREEN (session claude-wren pt6).

Live failure this covers: mid-Blackthorn-gym, 4 SUPER POTIONs in the bag,
`use_item` returned False four times in a row without ever moving a
cursor -- goto_pocket's wJumptableIndex gate read a non-pocket value in
field context while the pack was plainly drawn on screen. The screen is
the fallback truth: a pocket banner, or the pack's 'x N' quantity column.
"""
from types import SimpleNamespace

import pytest

from crystalagent.driver import Driver
from crystalagent.driver.inventory import _pack_pocket_banner, _pack_quantity_rows

pytestmark = pytest.mark.unit


def screen(*lines):
    rows = [l.ljust(20)[:20] for l in lines]
    return rows + [" " * 20] * (18 - len(rows))


# -- banner / quantity scrapers ----------------------------------------------

def test_banner_recognizes_every_pocket():
    for name in ("ITEM POCKET", "BALL POCKET", "KEY POCKET", "TM POCKET"):
        assert _pack_pocket_banner(screen(name)) == name


def test_banner_none_when_no_pocket_drawn():
    assert _pack_pocket_banner(screen("POKéDEX", "POKéMON", "PACK")) is None


def test_quantity_rows_detect_the_pack_column():
    assert _pack_quantity_rows(screen("SUPER POTION", "          ×  7"))
    # plain-ASCII decode of the same column, scroll arrow tolerated
    assert _pack_quantity_rows(screen("POTION        x  4 ▼"))


def test_quantity_rows_ignore_plain_dialog():
    assert not _pack_quantity_rows(screen("WREN used STRENGTH!"))
    assert not _pack_quantity_rows(screen("It's a x thing"))


# -- _items_pocket_by_screen -------------------------------------------------

def pack_driver(screens):
    """`screens` is consumed one entry per screen_text() call; the last
    entry repeats. Records every press."""
    d = Driver.__new__(Driver)
    seen = {"presses": [], "i": 0}

    def screen_text():
        i = min(seen["i"], len(screens) - 1)
        seen["i"] += 1
        return screens[i]

    d.emu = SimpleNamespace(screen_text=screen_text)
    d.press = lambda seq: seen["presses"].append(seq)
    return d, seen


def test_items_pocket_already_up_costs_no_presses():
    d, seen = pack_driver([screen("ITEM POCKET", "POTION   ×  4")])
    assert d._items_pocket_by_screen() is True
    assert seen["presses"] == []


def test_cycles_left_from_tm_pocket_to_items():
    d, seen = pack_driver([screen("TM POCKET"),
                           screen("KEY POCKET"),
                           screen("BALL POCKET"),
                           screen("ITEM POCKET")])
    assert d._items_pocket_by_screen() is True
    # pockets cycle ITEM <- BALL <- KEY <- TM: exactly three L presses
    assert len(seen["presses"]) == 3
    assert all(p.startswith("L") for p in seen["presses"])


def test_unreadable_banner_with_quantity_rows_counts_as_open():
    d, seen = pack_driver([screen("§¤◊", "SUPER POTION  ×  2")])
    assert d._items_pocket_by_screen() is True
    assert seen["presses"] == []


def test_no_pack_drawn_fails_fast_without_spinning():
    d, seen = pack_driver([screen("WREN", "The water is calm.")])
    assert d._items_pocket_by_screen() is False
    assert seen["presses"] == []


def test_wrong_pocket_that_never_cycles_gives_up_bounded():
    d, seen = pack_driver([screen("TM POCKET")])
    assert d._items_pocket_by_screen() is False
    assert len(seen["presses"]) == 4      # bounded, never unbounded spin
