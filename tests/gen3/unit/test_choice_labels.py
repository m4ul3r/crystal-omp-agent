"""Answering a question by NAME, resolved against the cartridge's own lists.

Mr. Briney asks "Where are we bound?" and offers PETALBURG / SLATEPORT /
CANCEL. The generic YES answer takes option 0 and sails the run BACK to the
mainland it just left -- which looks like progress in the log and is the exact
opposite. Picking by index instead is a magic constant that rots the moment a
list changes.

So the index is resolved from `gMultichoiceLists`, the table the game's own
scripts draw from. Two things make that safe rather than clever: the caller
states the whole box it expects, and an ambiguous answer is refused.
"""

import pytest

from pokeagent.menus import Menus

pytestmark = pytest.mark.unit

#: The real three-option lists that contain SLATEPORT, read from the ROM.
#: SLATEPORT sits at 1 in the first and 0 in the third -- which is why the
#: label alone cannot decide.
ROM_LISTS = [
    ["PETALBURG", "SLATEPORT", "CANCEL"],
    ["LITTLEROOT", "SLATEPORT", "LILYCOVE"],
    ["SLATEPORT", "LILYCOVE", "CANCEL"],
    ["SLATEPORT", "BATTLE TOWER", "CANCEL"],
]


class FakeMenus(Menus):
    """Menus with the ROM table and the box state supplied by the test."""

    def __init__(self, bounds=(0, 2), cursor=2, lists=None, armed=True):
        self._bounds, self._cursor = bounds, cursor
        self._lists = ROM_LISTS if lists is None else lists
        self._armed = armed
        self.picked = None
        self.last_reason = None

    def bounds(self):
        return self._bounds

    def cursor(self):
        return self._cursor

    def multichoice_labels(self, count=None):
        return [l for l in self._lists if count is None or len(l) == count]

    def wait_for_choice(self, max_presses=16):
        return self._armed

    def select_index(self, index, confirm=True, tries=12):
        self.picked = index
        return True


def test_the_expected_box_resolves_the_label():
    m = FakeMenus()
    assert m.select_label("SLATEPORT",
                          among=("PETALBURG", "SLATEPORT", "CANCEL")) is True
    assert m.picked == 1


def test_a_bare_label_that_is_ambiguous_is_refused():
    """SLATEPORT is option 1 in one list and option 0 in another. Guessing
    sails to the wrong town, and a story scene does not undo."""
    m = FakeMenus()
    assert m.select_label("SLATEPORT") is False
    assert "ambiguous" in m.last_reason
    assert m.picked is None


def test_a_bare_label_that_is_unambiguous_still_works():
    """The `among` guard is for ambiguity, not ceremony."""
    m = FakeMenus()
    assert m.select_label("BATTLE TOWER") is True
    assert m.picked == 1


def test_a_box_the_rom_does_not_have_is_refused():
    """If the expectation matches no real list, something has changed and
    pressing on would be guessing."""
    m = FakeMenus()
    assert m.select_label("SLATEPORT", among=("PETALBURG", "SLATEPORT", "OK")) is False
    assert "no ROM list reads" in m.last_reason


def test_an_expectation_of_the_wrong_size_is_refused():
    m = FakeMenus(bounds=(0, 2))
    assert m.select_label("SLATEPORT", among=("PETALBURG", "SLATEPORT")) is False
    assert "2-option box" in m.last_reason


def test_a_label_that_is_not_offered_is_refused():
    m = FakeMenus()
    assert m.select_label("LAVARIDGE") is False
    assert "offers" in m.last_reason


def test_a_box_that_never_appears_is_refused_not_guessed():
    """gMenu's bounds are LEFTOVERS until the box is drawn, so they read as an
    open menu while a message box is still printing. Briney's greeting takes
    eight A presses before the options exist; driving the cursor before then
    moves nothing and looks identical to a menu that ignores input."""
    m = FakeMenus(armed=False)
    assert m.select_label("SLATEPORT",
                          among=("PETALBURG", "SLATEPORT", "CANCEL")) is False
    assert "never started" in m.last_reason
    assert m.picked is None


def test_nothing_open_at_all_is_refused():
    m = FakeMenus(bounds=(0, 0))
    assert m.select_label("SLATEPORT") is False
    assert "no choice box open" in m.last_reason


def test_the_passive_probe_never_presses_anything():
    """The follow-up check must not go looking for a box.

    `advance_story` answers a question, lets the scene run, then checks
    whether another one appeared. If that second check presses A to find out,
    it mashes sixteen presses into the cutscene the first answer just started.
    That cancelled the sail to Slateport on every attempt -- the log read
    "chose SLATEPORT" and then "could not pick 'SLATEPORT'", over and over,
    while the boat never left.
    """
    class Counting(FakeMenus):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.presses = 0

        def wait_for_choice(self, max_presses=16):
            self.presses += 1
            return self._armed

        def choice_is_up(self):
            return self._armed

    m = Counting(armed=False)
    assert m.select_label("SLATEPORT", among=("PETALBURG", "SLATEPORT", "CANCEL"),
                          press=False) is False
    assert m.presses == 0, "the passive probe pressed A"

    m = Counting(armed=True)
    assert m.select_label("SLATEPORT", among=("PETALBURG", "SLATEPORT", "CANCEL"),
                          press=True) is True
    assert m.presses == 1, "the active call must still be allowed to press"


def test_the_passive_probe_still_answers_a_box_that_is_really_up():
    """Passive means "do not go hunting", not "do nothing": a second genuine
    question in the same scene still gets answered."""
    class Ready(FakeMenus):
        def choice_is_up(self):
            return True

    m = Ready()
    assert m.select_label("SLATEPORT", among=("PETALBURG", "SLATEPORT", "CANCEL"),
                          press=False) is True
    assert m.picked == 1
