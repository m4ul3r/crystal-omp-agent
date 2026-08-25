"""Choice-box label parsing + resolve_choice gating (gotcha-13 safe)."""
import pytest

import trek
import trek
from trek import Driver

pytestmark = pytest.mark.unit

# real decoded frames: nurse-style YES/NO box and a multi-option picker
YESNO = ["", "", "┌────┐", "│▶YES│", "│    │", "│ NO │", "└────┘"]
PICKER = ["┌──────────┐", "│▶MONDAY   │", "│ TUESDAY  │", "│ WEDNESDAY│",
          "└──────────┘"]


def test_parses_cursor_row_and_neighbors():
    assert Driver._choice_labels(YESNO) == ["YES", "NO"]


def test_multi_option_box_keeps_order():
    assert Driver._choice_labels(PICKER) == [
        "MONDAY", "TUESDAY", "WEDNESDAY"]


def test_no_cursor_means_no_options():
    assert Driver._choice_labels(["│ YES │", "│ NO  │"]) == []


def test_frame_borders_never_become_labels():
    labels = Driver._choice_labels(YESNO)
    assert all("─" not in l and "┌" not in l for l in labels)


class _Menu:
    """select_label that closes the box on success (like the engine)."""

    def __init__(self, world, flaky=False):
        self.world = world
        self.flaky = flaky
        self.got = None
        self.calls = 0

    def select_label(self, label, max_presses=14):
        self.got = label
        self.calls += 1
        if self.flaky and self.calls == 1:
            return True              # whiffed: box still settling
        self.world["open"] = False
        return True


class _Emu:
    def __init__(self, world):
        self.world = world
        self.frame = 0

    def screen_text(self):
        return list(YESNO) if self.world["open"] else ["overworld"]

    def tick(self, frames):
        self.frame += frames

    def read_u8(self, sym):
        return 0                      # naming sig 0 / options byte 0

    def write(self, name, value):
        pass


class _Driver(trek.Driver):
    """Real Driver methods; only the touched surface is faked."""

    def __init__(self, flaky=False):
        self.hooks = None             # legacy drain path in tests
        self.auto_fight = True
        self.encounter_events = []
        self.last_choice_options = []
        self.press = lambda seq: None
        self.textbox = lambda: True   # a dialog is up during the prompt
        self.flush_dialog = lambda max_frames=3000: "menu"  # box already up
        self.emu = _Emu({"open": True})
        self.menu = _Menu(self.emu.world, flaky=flaky)


def test_resolve_choice_answers_and_verifies_close():
    d = _Driver()
    out = d.resolve_choice("YES")
    assert out["answered"] is True and out["chose"] == "YES"
    assert d.menu.got == "YES"


def test_resolve_choice_retries_when_box_stays_open():
    d = _Driver(flaky=True)
    out = d.resolve_choice("YES")
    assert out["answered"] is True
    assert d.menu.calls == 2            # one bounded retry happened


def test_resolve_choice_refuses_invisible_label():
    d = _Driver()
    out = d.resolve_choice("QUIT")
    assert out["answered"] is False and out["chose"] is None
    assert d.menu.got is None            # never pressed a blind key
