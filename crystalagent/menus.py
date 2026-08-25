"""Menu navigation driven by the decoded text layer.

The engine draws its menu cursor (charmap "▶", tile $ed) on the selected
row of every standard menu -- battle menu, move list, START menu, party
lists, pack submenus -- so "which entry is highlighted?" is readable from
the same screen text we already decode. Scrolling lists additionally track
their absolute position in WRAM (wMenuScrollPosition + wMenuCursorY).
"""

import re

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

    def select_row_text(self, label, max_presses=14, confirm=True,
                        match=None, confirm_seq="A:2 .:10"):
        """Text-targeted row selection for menus whose LAYOUT VARIES:
        the party slot submenu lists the selected mon's field moves
        (CUT/SURF/STRENGTH/...) ABOVE the fixed STATS/SWITCH rows, and
        pack windows scroll -- so positional press counts select the
        wrong entry (wren pt6: field-Strength misfires during reorder).

        Finds the visible row whose text names `label` (word-bounded,
        or a custom `match(text)` predicate), takes the cursor glyph in
        that menu's COLUMN (the party list keeps its own glyph painted
        behind the submenu box), and steps the signed row delta one
        press at a time, re-reading the screen after every press. A
        label scrolled off-screen is searched for by walking the window
        DOWN, then UP once the list pins. Returns True only when the
        cursor verifiably sits on the matching row (A pressed when
        `confirm`; `confirm_seq` lets callers use a longer press for
        menus that swallow the short one)."""
        up = label.strip().upper()
        pat = re.compile(r"(?<![A-Z0-9])" + re.escape(up) + r"(?![A-Z0-9])")
        if match is None:
            def match(text):
                return bool(pat.search(text.upper()))

        def target(rows):
            """(row, column) where the matched label's text starts."""
            for y, row in enumerate(rows):
                gx = _cursor_x(row)
                text = row[gx + 1:] if gx >= 0 else row
                if match(text):
                    m = pat.search(text.upper())
                    off = m.start() if m else \
                        len(text) - len(text.lstrip(" "))
                    return y, (gx + 1 if gx >= 0 else 0) + off
            return None

        presses = stuck = 0
        prev = last_rows = None
        search_dir, flipped = "D", False
        while True:
            rows = self.screen()
            curs = [(y, _cursor_x(r)) for y, r in enumerate(rows)
                    if _cursor_x(r) >= 0]
            tgt = target(rows)
            if tgt is not None:
                y_tgt, x_tgt = tgt
                # cursors of THIS menu sit immediately left of its
                # label column; glyphs elsewhere (party list behind a
                # submenu, stale leftovers) are ignored
                band = [c for c in curs if x_tgt - 2 <= c[1] < x_tgt]
                if any(y == y_tgt for y, _ in band):
                    if confirm:
                        self.press(confirm_seq)
                    return True
            if presses >= max_presses:
                return False
            if tgt is None:
                # off-screen (scrolled list): identical rows after a
                # press mean the window pinned -- reverse once
                if last_rows is not None and rows == last_rows:
                    if flipped:
                        return False
                    search_dir, flipped = "U", True
                last_rows = rows
                self.press(f"{search_dir}:6 .:8")
                presses += 1
                continue
            ref = band[0] if band else (curs[0] if curs else None)
            if ref is None:
                stuck += 1
                if stuck >= 4:
                    return False   # no cursor painted: wrong screen
                self.press(".:6")  # tilemap paint lag: poll, don't press
                continue
            key = (ref[0], ref[1], y_tgt)
            stuck = stuck + 1 if key == prev else 0
            if stuck >= 3:
                return False       # cursor pinned: wrong menu or edge
            prev = key
            self.press("D:6 .:8" if y_tgt > ref[0] else "U:6 .:8")
            presses += 1

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


def _cursor_outside_box(rows):
    """Any menu-cursor glyph above the textbox rows: a choice/menu is
    open and blind A presses would pick something."""
    for r in rows[:13]:
        if _cursor_x(r) >= 0:
            return True
    return False


def plain_dialog_up(rows):
    """Strict gate for the A-mash lane: a bottom textbox with text in it
    AND nothing at all on the rest of the screen (no HUD, no menus)."""
    if len(rows) < 18 or not textbox_up(rows):
        return False
    outside = "".join(rows[:13])
    return not any(ch not in " ─│┌┐└┘" for ch in outside)


def dialog_press_safe(rows):
    """A-press safety for flush_dialog: textbox up, and no menu cursor
    sitting outside the box. Battle screens (HUD text above) still pass
    -- the battle path handles its own text."""
    return len(rows) >= 18 and textbox_up(rows) \
        and not _cursor_outside_box(rows)
