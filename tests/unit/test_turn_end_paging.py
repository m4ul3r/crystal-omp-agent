"""End-of-turn text paging and quiet probes.

A turn's last screens ("<mon> fainted!", the EXP bar, the level-up panels)
each wait for a BUTTON. The old post-turn wait only ticked, so every
battle-ending turn idled through its whole 2000-frame budget and logged
"wait_for: predicate never true in 2000 frames" for what is the NORMAL
path. `Battle._await_turn_end` pages that text like a player -- and refuses
to press A while a choice box is open (gotcha 13).
"""
import logging
from types import SimpleNamespace

import pytest

from crystalagent.battle import Battle
from crystalagent.menus import Menus

pytestmark = pytest.mark.unit

BLANK = [""] * 18
MENU = BLANK[:14] + ["", "FIGHT   PKMN", "PACK    RUN", ""]
# textbox rows carry text: this screen only advances on a button
FAINTED = BLANK[:14] + ["", "Enemy PIDGEY", "fainted!", ""]
# a YES/NO box above the textbox: a cursor glyph outside the box
LEARN_MOVE = (BLANK[:8] + ["┌────┐", "│▶YES│", "│ NO │", "└────┘"]
              + BLANK[:2] + ["", "EMBER is trying to", "learn EMBER?", ""])


class ScriptedEmu:
    """Screens handed out in order; each press advances the script."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.frame = 0
        self.i = 0

    def screen_text(self):
        return list(self.screens[min(self.i, len(self.screens) - 1)])

    def advance(self):
        self.i += 1


class CountingMenu:
    def __init__(self, emu, advance_on=("A",)):
        self.emu = emu
        self.presses = []
        self.advance_on = advance_on

    def press(self, seq):
        self.presses.append(seq)
        self.emu.frame += 12
        if seq.split(":")[0] in self.advance_on:
            self.emu.advance()

    def keys(self):
        return [s.split(":")[0] for s in self.presses]


class Turn(Battle):
    """Only _await_turn_end runs; observation is faked."""

    def __init__(self, screens, alive_for=99):
        self.emu = ScriptedEmu(screens)
        self.menu = CountingMenu(self.emu)
        self.names = SimpleNamespace(moves={}, species={})
        self.alive_for = alive_for

    def active(self):
        return self.emu.i < self.alive_for


def test_pages_the_faint_and_exp_text_instead_of_idling():
    t = Turn([FAINTED, FAINTED, MENU])
    assert t._await_turn_end() is True
    assert t.menu.keys().count("A") == 2      # paged, did not sit it out
    assert t.emu.frame < 2000                 # nowhere near the budget


def test_stops_as_soon_as_the_battle_is_over():
    t = Turn([FAINTED, FAINTED], alive_for=1)
    assert t._await_turn_end() is True
    assert t.menu.keys() == ["A"]             # one page, then wBattleMode=0


def test_never_answers_a_choice_box():
    """The learn-a-move / choose-next-mon boxes belong to their own
    decider: a blind A here picks whatever the cursor sits on."""
    t = Turn([LEARN_MOVE])
    assert t._await_turn_end(timeout_frames=200) is False
    assert "A" not in t.menu.keys() and t.menu.keys()               # ticked only


def test_reports_timeout_without_pressing_forever():
    t = Turn([FAINTED], alive_for=99)         # nothing ever resolves
    t.menu.advance_on = ()                    # A never changes the screen
    assert t._await_turn_end(timeout_frames=240) is False


class Ticking:
    """Menus with a real wait_for over a frame counter that never matches."""

    def __init__(self):
        self.frame = 0

    def screen_text(self):
        return ["nothing here"]


def _menus():
    m = Menus(Ticking())
    m.press = lambda seq: setattr(m.emu, "frame", m.emu.frame + 4)
    return m


def test_quiet_probe_records_its_reason_without_logging(caplog):
    m = _menus()
    with caplog.at_level(logging.INFO, logger="trek"):
        assert m.wait_for(lambda rows: False, timeout_frames=40,
                          quiet=True) is False
    assert "predicate never true" in m.last_reason
    assert caplog.records == []


def test_loud_probe_still_logs(caplog):
    m = _menus()
    with caplog.at_level(logging.INFO, logger="trek"):
        assert m.wait_for(lambda rows: False, timeout_frames=40) is False
    assert any("predicate never true" in r.getMessage()
               for r in caplog.records)
