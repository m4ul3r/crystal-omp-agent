"""The PC box driver's decisions, without the emulator.

22 of the missing dex species are evolutions of Pokemon sitting in the boxes,
and until this driver existed there was no way to get one into the party --
`deposit`/`withdraw` lived only in the Crystal tree. Every one of those slots
was unreachable.

The emulator-driven half is proved by driving it (party 5 -> 6, box 30 -> 29,
verified on a real save). What is worth unit-testing is the arithmetic and the
refusals, because those are what turn a wrong guess into a lost Pokemon.

Three real bugs are pinned here, each of which cost a round of debugging:

* `OPEN_PRESSES` was 6 and only worked by accident -- with a FULL party the
  extra press hit "Your party is full!" and bounced back to the menu, hiding
  the overshoot. One freed slot later, the same press opened the box grid and
  picked a mon.
* The box grid is 6 WIDE, so DOWN is +6. Walking it one step at a time with
  the wrong geometry declared itself stuck.
* A full box silently swallows a deposit: the flow ends in a box PICKER, and
  BOX1 of this save is 30/30.
"""

import pytest

from pokeagent.storage import (
    AREA_BOX, AREA_PARTY, BOX_COUNT, BOX_SLOTS,
    MENU_WITHDRAW, MENU_DEPOSIT, MENU_MOVE, MENU_SEE_YA,
    Storage,
)


class FakeMon:
    def __init__(self, nickname, species=1):
        self.nickname = nickname
        self.species = species


class FakeState:
    def __init__(self, party):
        self._party = party

    def party(self):
        return self._party


class FakeDriver:
    def __init__(self, party=()):
        self.state = FakeState([FakeMon(n) for n in party])


def _storage(party=()):
    s = Storage.__new__(Storage)
    s.d = FakeDriver(party)
    s.last_reason = None
    return s


@pytest.mark.unit
def test_the_menu_row_order_matches_the_rom():
    """src/pokemon_storage_system.c:28-33 lists WITHDRAW, DEPOSIT, MOVE,
    SEE YA in that order, and the menu opens on the first one."""
    assert (MENU_WITHDRAW, MENU_DEPOSIT, MENU_MOVE, MENU_SEE_YA) == (0, 1, 2, 3)


@pytest.mark.unit
def test_open_stops_at_the_menu_and_not_past_it():
    """Five presses: boot, multichoice, "Accessed", "opened", then the menu.

    Counted off screenshots. Six was the old value and it overshot into the
    box grid as soon as the party had room.
    """
    assert Storage.OPEN_PRESSES == 5


@pytest.mark.unit
def test_the_box_grid_is_six_wide():
    """boxes[14][30] is laid out 6x5, measured live:
    RIGHT (0,0)->(0,1), DOWN (0,2)->(0,8), UP (0,13)->(0,7)."""
    assert Storage.GRID_WIDTH == 6
    assert BOX_SLOTS == 30 and BOX_COUNT == 14


@pytest.mark.unit
def test_the_cursor_settle_is_long_enough_to_read():
    """150 frames returned the PREVIOUS index, which made a working grid look
    stuck. 400 was measured good."""
    assert Storage.CURSOR_SETTLE >= 400


@pytest.mark.unit
def test_first_free_box_skips_a_full_one():
    s = _storage()
    s.box_counts = lambda: [BOX_SLOTS, 24] + [0] * 12
    assert s.first_free_box() == 1


@pytest.mark.unit
def test_first_free_box_is_none_when_everything_is_full():
    """Better a refusal than a deposit into a box that cannot take it."""
    s = _storage()
    s.box_counts = lambda: [BOX_SLOTS] * BOX_COUNT
    assert s.first_free_box() is None


@pytest.mark.unit
def test_deposit_refuses_the_last_party_member():
    """The engine refuses too, but its refusal leaves a menu open that this
    driver would then have to unpick."""
    s = _storage(party=("SEA BIRD",))
    assert s.deposit(0) is False
    assert "last party member" in s.last_reason


@pytest.mark.unit
def test_deposit_refuses_an_empty_slot():
    s = _storage(party=("SEA BIRD", "NINJA"))
    assert s.deposit(4) is False
    assert "empty" in s.last_reason


@pytest.mark.unit
def test_withdraw_refuses_a_full_party():
    """WITHDRAW with six answers "Your party is full!" and refuses, so this
    says so up front instead of walking a cursor for nothing."""
    s = _storage(party=("A", "B", "C", "D", "E", "F"))
    assert s.withdraw(0, 0) is False
    assert "full" in s.last_reason


@pytest.mark.unit
def test_the_two_cursor_areas_are_distinct():
    assert (AREA_BOX, AREA_PARTY) == (0, 1)
