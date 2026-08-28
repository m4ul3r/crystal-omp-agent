"""trek.Driver._choice_box / _choice_labels: reading an open choice box
drawn over overworld map art. Labels must come from the box's own
column span (the bars either side of the cursor glyph), not the whole
row, so a box overlapping map tiles decodes cleanly and blank tiles
('\u03bb\u03bb\u03bb\u03bb' -- Greek lambda, alnum but never ASCII) never
leak in as options."""
import pytest

from crystalagent.menus import CURSORS
from trek import Driver

pytestmark = pytest.mark.unit

# real decoded frames: a YES/NO box drawn on top of overworld art, plus a
# blank-tile row inside the same column span
YES_ROW = "\u2583\u2584\u2596\u259b\u259b\u25aa\u2583\u2584\u2582\u2582\u2582\u2582\u03bb\u03bb\u2502 YES\u2502"
NO_ROW = "\u2582\u2582\u2593\u03b7\u03b7\u25cb\u2582\u2582\u2582\u2582\u2582\u2582\u03bb\u03bb\u2502\u25b6NO \u2502"
BLANK_ROW = "\u2582\u2582\u2593\u2593\u2593\u2593\u2582\u2582\u2582\u2582\u2582\u2582\u03bb\u03bb\u2502\u03bb\u03bb\u03bb\u03bb\u2502"
ROWS = [YES_ROW, NO_ROW, BLANK_ROW]


def test_cursor_glyphs_are_the_real_menu_glyphs():
    """Regression guard: if crystalagent.menus.CURSORS ever changes, this
    fixture's cursor char must track it rather than silently stop
    matching."""
    assert "\u25b6" in CURSORS
    assert "\u25b6" in NO_ROW


def test_box_over_overworld_art_decodes_yes_no_with_cursor_on_no():
    box = Driver._choice_box(ROWS)
    assert box is not None
    assert box["cursor"] == 1
    assert box["options"] == [(0, "YES"), (1, "NO")]


def test_choice_labels_matches_box_options_in_order():
    assert Driver._choice_labels(ROWS) == ["YES", "NO"]


def test_blank_map_tiles_inside_the_box_span_are_never_options():
    box = Driver._choice_box(ROWS)
    labels = [t for _, t in box["options"]]
    assert "\u03bb\u03bb\u03bb\u03bb" not in labels
    assert not any("\u03bb" in t for t in labels)
    # the blank row sits inside the scan window but contributed nothing
    assert all(row != 2 for row, _ in box["options"])


def test_no_cursor_glyph_anywhere_yields_none_and_no_labels():
    plain_rows = ["\u2502 YES\u2502", "\u2502 NO \u2502", "just overworld text"]
    assert not any(c in r for r in plain_rows for c in CURSORS)
    assert Driver._choice_box(plain_rows) is None
    assert Driver._choice_labels(plain_rows) == []
