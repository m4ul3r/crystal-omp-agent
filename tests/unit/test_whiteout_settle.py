"""A whiteout must be REPORTED as what actually happened.

Losing warps the player to the last Pokécenter and heals the party, but
the fade + warp + heal take thousands of frames. `fight()` used to log
"auto-healed at last Pokécenter" the moment it detected the wipe, and a
live run then stood on the battle cell (AZALEA_TOWN (5,11)) with QUILAVA
at 0/59 while every caller believed it was healed.
"""
import pytest

import crystalagent.driver.battle as battle_owner
from crystalagent.driver import Driver

pytestmark = pytest.mark.unit

BLANK = [""] * 18
# textbox rows carry text: pressing A pages the whiteout message
TEXT = BLANK[:14] + ["", "OMP is out of", "useable POKéMON!", ""]
# a cursor glyph above the box: a choice is open, A must not be pressed
CHOICE = (BLANK[:8] + ["┌────┐", "│▶YES│", "│ NO │", "└────┘"] + BLANK[:2]
          + ["", "Use next PKMN?", "", ""])


class Wiped(Driver):
    """_settle_whiteout only; the party revives after `pages` A presses."""

    def __init__(self, pages=2, rows=TEXT, revives=True):
        self.rows = rows
        self.pages = pages
        self.revives = revives
        self.presses = []
        self.emu = type("E", (), {"frame": 0, "screen_text": lambda s: None})()
        self.emu.screen_text = lambda: list(self.rows)
        self.names = None

    def press(self, seq):
        self.presses.append(seq)
        self.emu.frame += 40
        if seq.startswith("A"):
            self.pages -= 1

    def battle(self):
        return False

    def settle(self, *a, **kw):
        pass

    def _party(self):
        hp = 59 if (self.revives and self.pages <= 0) else 0
        return [{"hp": hp, "max_hp": 59, "egg": False},
                {"hp": 0, "max_hp": 18, "egg": True}]


@pytest.fixture(autouse=True)
def _party_state(monkeypatch):
    monkeypatch.setattr(battle_owner, "game_state",
                        lambda emu, names: {"party": emu.owner._party()})


def _driver(**kw):
    d = Wiped(**kw)
    d.emu.owner = d
    return d


def test_waits_for_the_heal_before_claiming_one():
    d = _driver(pages=2)
    assert d._settle_whiteout() is True
    assert d.presses.count("A:2 .:16") == 2       # paged the cutscene


def test_reports_failure_when_the_party_never_comes_back():
    d = _driver(pages=1, revives=False)
    assert d._settle_whiteout(max_frames=400) is False


def test_never_presses_a_through_a_choice_box():
    """The egg-hatch / next-mon prompts own their own decision."""
    d = _driver(pages=99, rows=CHOICE, revives=False)
    d._settle_whiteout(max_frames=400)
    assert not any(p.startswith("A") for p in d.presses)
