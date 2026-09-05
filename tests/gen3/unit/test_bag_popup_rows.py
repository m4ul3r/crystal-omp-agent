"""The item popup's rows must be READ, never assumed.

`teach()` pressed A on an item and trusted that "its first row is USE for a
machine". For a machine it is. The bug was making the assumption at all: with
the bag DISPLAYING the POKE BALLS pocket, that blind A opened a GREAT BALL's
popup, whose first row is GIVE -- and GIVE opens the party list too, so the flow
sailed through `_wait_for_party_list`, picked the lead and handed it the ball.

The game said so plainly:

    SEA BIRD is already holding one GREAT BALL.
    Would you like to switch the two items?

That unanswered YES/NO box wedged the run at Route 110 (6,38) for fourteen
minutes, position frozen and 0 steps, and was reported from the couch as "still
trying to give seabird a pokeball to hold".
"""

import pytest

from pokeagent.teaching import ITEMS_POCKET, TMHM_POCKET, Teacher

pytestmark = pytest.mark.unit

USE = 2          # stand-in for ITEM_ACTION_USE_0
GIVE = 3
TOSS = 4
CANCEL = 5


class _Emu:
    def __init__(self, rows, pocket, cursor=0):
        self.rows = list(rows)
        self.pocket = pocket
        self.cursor = cursor
        self.presses = []

    def u32(self, _name):
        return 0x1000

    def u8(self, name):
        if name == "gUnknown_02038564":
            return len(self.rows)
        if name == "sPopupMenuSelection":
            return self.cursor
        if name == "sCurrentBagPocket":
            return self.pocket
        return 0

    def read(self, _addr, size):
        return bytes(self.rows[:size])

    def run_sequence(self, seq):
        self.presses.append(seq)
        key = seq.split(":")[0]
        # The real grid: UP/DOWN flip bit 0, LEFT/RIGHT step by two.
        if key == "DOWN":
            self.cursor |= 1
        elif key == "UP":
            self.cursor &= ~1
        elif key == "RIGHT":
            self.cursor = min(len(self.rows) - 1, self.cursor + 2)
        elif key == "LEFT":
            self.cursor = max(0, self.cursor - 2)


def _teacher(emu):
    t = object.__new__(Teacher)
    t.emu = emu
    t.last_reason = None
    t._action_use = USE
    return t


def test_a_popup_without_use_is_refused_before_pressing_a():
    """A ball offers GIVE/TOSS/CANCEL. None of them is USE."""
    emu = _Emu([GIVE, TOSS, CANCEL], pocket=1)
    t = _teacher(emu)
    assert t.choose_use() is False
    assert "no USE action" in t.last_reason
    assert not any(p.startswith("A") for p in emu.presses), \
        "pressed A on a popup with no USE row"


def test_use_is_found_when_it_is_not_row_zero():
    emu = _Emu([GIVE, USE, CANCEL], pocket=TMHM_POCKET)
    t = _teacher(emu)
    assert t.choose_use() is True
    assert emu.cursor == 1
    assert any(p.startswith("A") for p in emu.presses)


def test_row_zero_use_is_confirmed_without_navigating():
    emu = _Emu([USE, CANCEL], pocket=TMHM_POCKET)
    t = _teacher(emu)
    assert t.choose_use() is True
    assert emu.presses == ["A:4 .:16"], "moved a cursor that was already right"


def test_the_displayed_pocket_is_checked_not_just_the_target():
    """`_selected_item(pocket)` is right data about the wrong thing when the
    bag is showing something else."""
    on_balls = _teacher(_Emu([GIVE], pocket=1))
    assert on_balls._on_pocket(TMHM_POCKET) is False
    assert on_balls._on_pocket(ITEMS_POCKET) is False

    on_tms = _teacher(_Emu([USE], pocket=TMHM_POCKET))
    assert on_tms._on_pocket(TMHM_POCKET) is True


def test_a_stuck_cursor_is_reported_rather_than_looped():
    class _Stuck(_Emu):
        def run_sequence(self, seq):
            self.presses.append(seq)      # never moves

    emu = _Stuck([GIVE, USE], pocket=TMHM_POCKET)
    t = _teacher(emu)
    assert t.choose_use() is False
    assert "stuck" in t.last_reason
