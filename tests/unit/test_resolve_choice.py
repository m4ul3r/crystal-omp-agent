"""Choice-box label parsing + resolve_choice gating (gotcha-13 safe).

The fake world models what the ENGINE does, because that is where the live
bug was: a YES/NO box paints its cursor on a row, U/D move it WITHOUT
wrapping, and A confirms whatever row it sits on. `resolve_choice` used to
confirm through `Menus.select_label`, which only presses DOWN -- with the
cursor defaulted onto NO (mom's day-picker, the clock prompts) it could
never reach YES and every fresh game stalled in PLAYERS_HOUSE_1F.
"""
import pytest

from crystalagent.driver import Driver
from crystalagent.driver import Driver

pytestmark = pytest.mark.unit

# real decoded frames: nurse-style YES/NO box and a multi-option picker
YESNO = ["", "", "┌────┐", "│▶YES│", "│    │", "│ NO │", "└────┘"]
PICKER = ["┌──────────┐", "│▶MONDAY   │", "│ TUESDAY  │", "│ WEDNESDAY│",
          "└──────────┘"]
# the same box drawn OVER the overworld: map art shares the rows
OVERWORLD_YESNO = [
    "▘▙◖▛▛◪▘▙▂▂▂▂λλ┌────┐",
    "▃▄◖▛▛◪▃▄▂▂▂▂λλ│ YES│",
    "▘▙◨◧◧◩▘▙▂▂▂▂λλ│    │",
    "▂▂▓ηη○▂▂▂▂▂▂λλ│▶NO │",
    "▂▂▂▂▂▂▂▂▂▂▂▂λλ└────┘",
]


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


def test_box_over_overworld_reads_only_the_box():
    """Map art left of the box must not end up glued to the label."""
    box = Driver._choice_box(OVERWORLD_YESNO)
    assert [t for _, t in box["options"]] == ["YES", "NO"]
    assert box["cursor"] == 3                      # cursor sits on NO


class _World:
    """A YES/NO box the way the engine draws and drives it."""

    def __init__(self, options=("YES", "NO"), cursor=1, swallow_first=False):
        self.options = list(options)
        self.cursor = cursor
        self.open = True
        self.swallow = swallow_first
        self.presses = []

    def key(self, k):
        self.presses.append(k)
        if not self.open:
            return
        if k == "U":
            self.cursor = max(0, self.cursor - 1)      # no wrap: the bug
        elif k == "D":
            self.cursor = min(len(self.options) - 1, self.cursor + 1)
        elif k == "A":
            if self.swallow:                           # gotcha 2
                self.swallow = False
                return
            self.open = False
            self.answer = self.options[self.cursor]

    def rows(self):
        if not self.open:
            return ["overworld"]
        w = max(len(o) for o in self.options)
        out = ["┌" + "─" * (w + 1) + "┐"]
        for i, opt in enumerate(self.options):
            out.append(f"│{'▶' if i == self.cursor else ' '}{opt:<{w}}│")
        out.append("└" + "─" * (w + 1) + "┘")
        return out


class _Emu:
    def __init__(self, world):
        self.world = world
        self.frame = 0

    def screen_text(self):
        return self.world.rows()

    def tick(self, frames):
        self.frame += frames

    def read_u8(self, sym):
        return 0                      # naming sig 0 / options byte 0

    def write(self, name, value):
        pass


class _Driver(Driver):
    """Real Driver methods; only the touched surface is faked."""

    def __init__(self, **kw):
        self.hooks = None             # legacy drain path in tests
        self.auto_fight = True
        self.encounter_events = []
        self.last_choice_options = []
        self.textbox = lambda: True   # a dialog is up during the prompt
        self.flush_dialog = lambda max_frames=3000: "menu"  # box already up
        self.world = _World(**kw)
        self.emu = _Emu(self.world)
        self.press = self._press

    def _press(self, seq):
        for token in seq.split():
            k = token.split(":")[0]
            if k in ("U", "D", "L", "R", "A", "B"):
                self.world.key(k)


def test_answers_a_box_whose_cursor_starts_below_the_target():
    d = _Driver(cursor=1)              # default cursor on NO, YES above it
    out = d.resolve_choice("YES")
    assert out["answered"] is True and out["chose"] == "YES"
    assert d.world.answer == "YES"
    assert "U" in d.world.presses      # walked UP: a DOWN-only walk cannot
    assert "D" not in d.world.presses


def test_answers_a_box_whose_cursor_starts_above_the_target():
    d = _Driver(cursor=0)
    out = d.resolve_choice("NO")
    assert out["answered"] is True and out["chose"] == "NO"
    assert d.world.answer == "NO"
    assert "D" in d.world.presses and "U" not in d.world.presses


def test_retries_when_the_first_confirm_is_swallowed():
    d = _Driver(cursor=1, swallow_first=True)
    out = d.resolve_choice("YES")
    assert out["answered"] is True and d.world.answer == "YES"
    assert d.world.presses.count("A") == 2      # one bounded retry happened


def test_refuses_invisible_label():
    d = _Driver()
    out = d.resolve_choice("QUIT")
    assert out["answered"] is False and out["chose"] is None
    assert d.world.presses == []                # never pressed a blind key
    assert d.world.open is True


def test_multi_option_picker_walks_several_rows():
    d = _Driver(options=("MONDAY", "TUESDAY", "WEDNESDAY"), cursor=0)
    out = d.resolve_choice("WEDNESDAY")
    assert out["answered"] is True and d.world.answer == "WEDNESDAY"
    assert d.world.presses.count("D") == 2
