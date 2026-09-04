"""The TM/HM teach flow, ported from the by-hand recipe that always worked.

FUCK_I_MESSED_UP.md #68/#71: `teach_tm` failed five times for HM06 and
again for HM07 with `tmhm_use: party list never opened`, while the same
steps driven by hand taught the move every time. Live on the
claude-goldeen checkpoint the cause is visible frame by frame:

  * the YES/NO teach prompt eats the FIRST A it is given (gotcha 2), so
    one A leaves the box up -- and the old code then only TICKED, waiting
    for a party list that needed another press;
  * the wait keyed on 'CANCEL' AND 'ABLE'. CANCEL is the row after the
    last mon, so a six-mon party puts it under the description textbox
    and the predicate can never come true.

The flow is now classify-then-act, and the party-list test is checked
BEFORE any press because an A press there picks a mon.
"""
import pytest

from crystalagent.driver import Driver

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self, screens):
        self.screens = list(screens)
        self.frame = 0
        self.u8 = {}

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.screens[0])

    def read_u8(self, name):
        return self.u8.get(name, 0)


def rows(*lines):
    out = [" " * 20 for _ in range(18)]
    for i, text in lines:
        out[i] = text.ljust(20)[:20]
    return out


USE_SUBMENU = rows((8, "▶USE"), (10, " QUIT"))
BOOTED = rows((14, "Booted up an HM."))
YES_NO = rows((14, "Teach WATERFALL to"), (8, "▶YES"), (10, " NO"))
# five mons: name rows 1,3,5,7,9 and ABLE/NOT ABLE tags on the row below
PARTY_LIST = rows((1, "▷  NOCTOWL"), (2, "        ᴸ16 NOT ABLE"),
                  (3, "   GOLDEEN"), (4, "        ᴸ22 ABLE"),
                  (11, " CANCEL"))
# six mons: CANCEL is at row 13, behind the description textbox
FULL_PARTY_LIST = rows((1, "▷  NOCTOWL"), (2, "        ᴸ16 NOT ABLE"),
                       (11, "   GEODUDE"), (12, "        ᴸ7  ABLE"),
                       (14, "Which ᴾᴹ?"))


def tm_driver(script):
    """`script`: the screens the flow walks through, advanced by any press
    that is not a pure wait."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(script)
    d.presses = []

    def press(seq):
        d.presses.append(seq)
        d.emu.tick(5)
        if not seq.startswith(".") and len(d.emu.screens) > 1:
            d.emu.screens.pop(0)
    d.press = press
    d.textbox = lambda: any("Booted" in r or "Teach" in r
                            for r in d.emu.screen_text())
    return d


def test_tmhm_use_presses_through_a_swallowed_yes():
    """One A on the YES/NO box is not enough: the box is still up, and the
    party list only arrives after the next press. This is the exact live
    sequence (HM07 -> GOLDEEN)."""
    d = tm_driver([USE_SUBMENU, BOOTED, YES_NO, YES_NO, PARTY_LIST])
    assert d._tmhm_use() is True
    assert d.presses.count("A:5 .:45") == 2     # YES answered twice


def test_tmhm_use_stops_the_moment_the_party_list_is_up():
    """No A press once ABLE tags are on screen -- that press would select
    a mon (a probe of this flow put 'not compatible' on screen that way)."""
    d = tm_driver([USE_SUBMENU, BOOTED, YES_NO, PARTY_LIST, PARTY_LIST])
    assert d._tmhm_use() is True
    tail = d.presses[-1]
    assert tail.startswith("A")                  # the press that opened it
    # and nothing after it: the loop returned instead of pressing again
    assert d.emu.screens[0] is PARTY_LIST


def test_party_list_is_recognised_without_cancel():
    """A six-mon party hides CANCEL under the textbox; ABLE is the tag the
    engine actually writes (party_menu.asm PlacePartyMonTMHMCompatibility)."""
    d = tm_driver([FULL_PARTY_LIST])
    assert d._tmhm_party_list_up() is True
    assert not any("CANCEL" in r for r in d.emu.screen_text())


def test_tmhm_use_reports_the_screen_when_it_gives_up():
    d = tm_driver([rows((14, "nothing here"))])
    assert d._tmhm_use(max_steps=3) is False
    assert "party list never opened" in d.last_menu_reason
    assert "nothing here" in d.last_menu_reason


def test_able_under_cursor_reads_the_row_wmenucursory_points_at():
    """Mon n (1-based, = wMenuCursorY) has its tag on screen row 2n --
    party_menu.asm starts at hlcoord 12, 2 and adds 2 rows per mon."""
    d = tm_driver([PARTY_LIST])
    d.emu.u8["wMenuCursorY"] = 1                 # NOCTOWL: NOT ABLE
    assert d._able_under_cursor() is False
    d.emu.u8["wMenuCursorY"] = 2                 # GOLDEEN: ABLE
    assert d._able_under_cursor() is True


def test_able_under_cursor_falls_back_to_the_glyph():
    """WRAM behind the paint (cursor row blank): the glyph scan still
    answers, as it did before."""
    d = tm_driver([PARTY_LIST])
    d.emu.u8["wMenuCursorY"] = 9                 # off the drawn rows
    assert d._able_under_cursor() is False       # glyph sits on NOCTOWL
