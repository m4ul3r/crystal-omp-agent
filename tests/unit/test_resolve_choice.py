"""Choice-box label parsing + resolve_choice gating (gotcha-13 safe)."""
import pytest

from trek import Driver

pytestmark = pytest.mark.unit

# real decoded frames: nurse-style YES/NO box and the mom day-picker
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
    def __init__(self, ok):
        self.ok = ok
        self.got = None

    def select_label(self, label, max_presses=14):
        self.got = label
        return self.ok


def _driver_with_rows(rows, menu_ok=True):
    d = Driver.__new__(Driver)
    d.emu = type("E", (), {"screen_text": staticmethod(lambda: rows)})()
    d.menu = _Menu(menu_ok)
    return d


def test_resolve_choice_answers_visible_label():
    d = _driver_with_rows(YESNO)
    out = d.resolve_choice("YES")
    assert out == {"answered": True, "chose": "YES",
                   "options": ["YES", "NO"]}
    assert d.menu.got == "YES"


def test_resolve_choice_refuses_invisible_label():
    d = _driver_with_rows(YESNO)
    out = d.resolve_choice("QUIT")
    assert out["answered"] is False and out["chose"] is None
    assert d.menu.got is None            # never pressed a blind key
