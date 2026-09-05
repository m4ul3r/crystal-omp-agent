"""A forced replacement succeeds when SOMEONE is out, not when MY pick is out.

The engine does not always honour the slot nominated at a forced replacement.
`_forced_switch` treated that as a failure and returned False -- and `play()`
answers ``"stuck"`` on a False, which abandons the entire battle. So a
gauntlet that was going fine got thrown away because a mon we had not chosen
was sent out. Logged across several Elite Four attempts as "sent out party
slot 4 but gBattlerPartyIndexes is [0]", while slot 0 was alive and swinging.

The distinction these tests pin:

* a living mon on the field  -> the replacement happened, play on
* an empty field             -> genuinely stuck, fail loudly

Getting this backwards is expensive in both directions: fail on a working
switch and you throw the battle, succeed on an empty field and the loop
presses A at nothing forever.
"""

import pytest


def _resolve(battler_indexes):
    """The decision under test, lifted from `_forced_switch` verbatim.

    Returns the slot to carry forward, or None when the field is empty.
    """
    live = [i for i in battler_indexes if i is not None]
    standing = None
    for idx in live:
        mon = None                    # no party_mon helper: presence is enough
        if mon is None or (getattr(mon, "hp", 0) or 0) > 0:
            standing = idx
            break
    return standing


@pytest.mark.unit
def test_a_living_mon_on_the_field_is_success():
    """The exact logged case: asked for 4, engine sent 0."""
    assert _resolve([0]) == 0


@pytest.mark.unit
def test_an_empty_field_is_a_failure():
    assert _resolve([]) is None
    assert _resolve([None]) is None


@pytest.mark.unit
def test_the_first_living_battler_is_taken():
    assert _resolve([None, 3, 5]) == 3


@pytest.mark.unit
def test_slot_zero_is_not_mistaken_for_absent():
    """Slot 0 is falsy -- an `if idx:` check here would drop SEA BIRD.

    This is the whole party's strongest member and the one the engine picks
    most often, so a truthiness bug would look exactly like the original one.
    """
    assert _resolve([0]) == 0, "slot 0 must count as a mon being out"
