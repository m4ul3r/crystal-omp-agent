"""A party menu that survives B presses is a forced replacement.

`gUnknown_02038473 == 1` is the engine's own "send this one out directly"
flag, and it is right when it is set -- but it is not set for every
replacement the engine demands. When a mon faints mid-turn the menu that comes
up cannot be backed out of, so the "stale menu, press B" branch pressed B
against an immovable menu while the opponent attacked for free.

Measured in the Elite Four: ten consecutive "stale party menu" lines followed
by "sent out party slot 1 but gBattlerPartyIndexes is [0]", losing to Drake
twice with healing items still in the bag.

The fix trusts the observation over the flag. These tests pin both halves,
because getting it wrong in either direction is expensive: treat a real stale
menu as forced and the run switches when it meant to attack; treat a real
replacement as stale and the battle deadlocks.
"""

import pytest


class _Menu:
    """The decision under test, lifted out of the battle loop verbatim."""

    def __init__(self, flag, dismissable):
        self.flag = flag
        self.dismissable = dismissable
        self._stale_party = 0
        self.open = True
        self.forced_calls = 0
        self.b_presses = 0

    def _press_b(self):
        self.b_presses += 1
        if self.dismissable:
            self.open = False

    def step(self):
        forced = self.flag == 1
        if not forced and self._stale_party >= 2:
            forced = True
        if forced:
            self._stale_party = 0
            self.forced_calls += 1
            self.open = False
            return "forced"
        self._stale_party += 1
        self._press_b()
        return "backed-out"

    def run(self, rounds=8):
        seen = []
        for _ in range(rounds):
            if not self.open:
                break
            seen.append(self.step())
        return seen


@pytest.mark.unit
def test_the_flag_is_honoured_immediately():
    """When the engine says forced, switch on the FIRST look -- no B presses."""
    m = _Menu(flag=1, dismissable=False)
    assert m.run() == ["forced"]
    assert m.b_presses == 0
    assert m.forced_calls == 1


@pytest.mark.unit
def test_a_dismissable_menu_stays_stale():
    """A genuinely stale menu goes away on one B and is never forced."""
    m = _Menu(flag=0, dismissable=True)
    assert m.run() == ["backed-out"]
    assert m.forced_calls == 0
    assert not m.open


@pytest.mark.unit
def test_a_menu_that_survives_b_becomes_forced():
    """The Elite Four case: flag clear, menu immovable. Must NOT spin."""
    m = _Menu(flag=0, dismissable=False)
    seen = m.run()
    assert seen == ["backed-out", "backed-out", "forced"], seen
    assert m.forced_calls == 1
    # Two presses to learn, then it acts -- not ten.
    assert m.b_presses == 2


@pytest.mark.unit
def test_it_cannot_loop_forever():
    """Whatever the flag says, the menu is resolved within three looks."""
    for flag in (0, 1, 255):
        m = _Menu(flag=flag, dismissable=False)
        m.run(rounds=20)
        assert not m.open, f"flag {flag} left the menu open"
        assert m.b_presses <= 2


@pytest.mark.unit
def test_the_counter_resets_between_menus():
    """A later stale menu must get its own two B presses, not inherit them."""
    m = _Menu(flag=1, dismissable=False)
    m.step()                      # forced, resets the counter
    assert m._stale_party == 0
    m.flag, m.open = 0, True
    assert m.step() == "backed-out"
