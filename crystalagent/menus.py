"""Menu navigation driven by the decoded text layer.

The engine draws its menu cursor (charmap "▶", tile $ed) on the selected
row of every standard menu -- battle menu, move list, START menu, party
lists, pack submenus -- so "which entry is highlighted?" is readable from
the same screen text we already decode. Scrolling lists additionally track
their absolute position in WRAM (wMenuScrollPosition + wMenuCursorY).
"""

from .emu import parse_sequence


CURSOR = "▶"
CURSORS = "▷▶"   # $ec (1D static menus) / $ed (2D + scrolling menus)


def _cursor_x(row):
    """Index of the leftmost cursor glyph in a decoded row, or -1."""
    return min((row.find(c) for c in CURSORS if c in row), default=-1)


class Menus:
    def __init__(self, emu):
        self.emu = emu

    def press(self, seq):
        self.emu.run_sequence(parse_sequence(seq))

    # -- observation -------------------------------------------------------

    def screen(self):
        return self.emu.screen_text()

    def cursor_row(self):
        """(row_index, text_after_arrow) of the highlighted row, or None."""
        for y, row in enumerate(self.screen()):
            x = _cursor_x(row)
            if x >= 0:
                return y, row[x + 1:].lstrip(" ")
        return None

    def cursor_labels(self):
        """Text right of the arrow for every highlighted possibility (the
        BG shadow tilemap can lag a frame behind; callers poll)."""
        seen = []
        for y, row in enumerate(self.screen()):
            x = _cursor_x(row)
            if x >= 0:
                seen.append(row[x + 1:].lstrip(" "))
        return seen

    def scroll_abs(self):
        """Absolute 0-based index of the selection in a ScrollingMenu list."""
        return self.emu.read_u8("wMenuScrollPosition") + \
            self.emu.read_u8("wMenuCursorY") - 1

    # -- actions -----------------------------------------------------------

    def wait_for(self, predicate, timeout_frames=600):
        """Tick until predicate(screen_text) is true; returns success."""
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            if predicate(self.screen()):
                return True
            self.press(".:4")
        return False

    def has_label(self, rows, label):
        """Is the cursor sitting immediately before `label` on any row?"""
        for row in rows:
            x = _cursor_x(row)
            if x >= 0 and row[x + 1:].strip().startswith(label):
                return True
        return False

    def select_label(self, label, max_presses=14, confirm=True,
                     timeout_frames=400):
        """In the current vertical menu, press DOWN until the arrow sits on
        `label`, then press A. Works across menus because every static menu
        paints ▶ next to its entries. Returns True if confirmed."""
        pressed = 0
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            rows = self.screen()
            if self.has_label(rows, label):
                if confirm:
                    self.press("A:2 .:10")
                return True
            if pressed >= max_presses:
                return False
            self.press("D:6 .:4")
            pressed += 1
        return False

    def select_abs(self, target, max_steps=30, confirm=True):
        """Navigate a scrolling list until the absolute selection index is
        `target`, then A. Uses WRAM position, not the text layer, so it
        works for entries scrolled off-screen."""
        steps = 0
        while steps < max_steps:
            cur = self.scroll_abs()
            if cur == target:
                if confirm:
                    self.press("A:6 .:18")
                return True
            self.press("D:6 .:4")
            steps += 1
        return False

    def wait_for_label(self, label, timeout_frames=300):
        """Tick until some row has the cursor sitting on `label`."""
        return self.wait_for(lambda rows: self.has_label(rows, label),
                             timeout_frames)

    def close(self):
        self.press("B:2 .:8")


def battle_menu_up(rows):
    joined = "".join(rows)
    return "FIGHT" in joined and "RUN" in joined


def naming_keyboard_up(rows):
    """The naming keyboard is identifiable by its DEL and END keys."""
    return any("DEL" in r for r in rows) and any("END" in r for r in rows)


def textbox_up(rows):
    """The bottom two textbox rows hold text (non-blank, non-border)."""
    bottom = rows[14] + rows[15] + rows[16] + rows[17] if len(rows) >= 18 else ""
    return any(ch not in " ─└┘│ " for ch in bottom)
