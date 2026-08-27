"""Menu navigation driven by the decoded text layer.

The engine draws its menu cursor (charmap "▶", tile $ed) on the selected
row of every standard menu -- battle menu, move list, START menu, party
lists, pack submenus -- so "which entry is highlighted?" is readable from
the same screen text we already decode. Scrolling lists additionally track
their absolute position in WRAM (wMenuScrollPosition + wMenuCursorY).
"""

import logging
import re

from .emu import parse_sequence

log = logging.getLogger("trek")


CURSOR = "▶"
CURSORS = "▷▶"   # $ec (1D static menus) / $ed (2D + scrolling menus)


def _cursor_x(row):
    """Index of the leftmost cursor glyph in a decoded row, or -1."""
    return min((row.find(c) for c in CURSORS if c in row), default=-1)


def _cursor_xs(row):
    """EVERY cursor-glyph position in a decoded row, left to right.
    Nested battle menus paint two at once -- the battle party list keeps
    its own ▷ while the SWITCH/STATS/CANCEL box draws ▶ over it (AGENTS.md
    gotcha 1) -- so a leftmost-only read reports the wrong list."""
    return [i for i, ch in enumerate(row) if ch in CURSORS]


class Menus:
    # Why the last falsy return was falsy. A menu primitive that answers
    # False without saying what it was looking for is the failure mode
    # that cost a live session an hour: use_item kept "succeeding" into a
    # closed pack because nothing downstream could say what went wrong.
    last_reason = None

    def __init__(self, emu):
        self.emu = emu

    def _fail(self, reason):
        """Record why a primitive is returning False, and say so once."""
        self.last_reason = reason
        log.info("  menu: %s", reason)
        return False

    def _expect_state(self, pred, what, tries=3, seq="A:2 .:10",
                      settle=".:12"):
        """Confirm until the screen actually REACHES the expected state.

        The frame a menu is drawn its input loop is not running yet, so the
        first A is swallowed (AGENTS.md gotcha 2). A single press plus a
        cursor-glyph check is how ``select_label('PACK')`` reported success
        with the pack never opened; the answer is to press, settle, and
        re-read until the target screen is really up."""
        if pred(self.screen()):
            return True
        for _ in range(tries):
            self.press(seq)
            if settle:
                self.press(settle)
            if pred(self.screen()):
                return True
        return self._fail(f"{what}: state not reached after {tries} confirms")

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
        """Text right of EVERY highlighted possibility (the BG shadow
        tilemap can lag a frame behind; callers poll). Nested menus paint
        two glyphs at once, so every glyph on a row counts."""
        seen = []
        for row in self.screen():
            for x in _cursor_xs(row):
                seen.append(row[x + 1:].lstrip(" "))
        return seen

    def scroll_abs(self):
        """Absolute 0-based index of the selection in a ScrollingMenu list."""
        return self.emu.read_u8("wMenuScrollPosition") + \
            self.emu.read_u8("wMenuCursorY") - 1

    # -- glyph-less PC lists ----------------------------------------------
    # Bill's PC paints its list selection with an OAM SPRITE cursor
    # (engine/pokemon/bills_pc.asm BillsPC_UpdateSelectionCursor, drawn
    # right after ClearSprites), so NEITHER ▷ nor ▶ ever reaches the
    # tilemap: cursor_row/cursor_labels/select_label are BLIND on the
    # DEPOSIT and WITHDRAW lists. A live session read that as "the A
    # press did nothing", mashed A, and deposited five of six party
    # members (FUCK_I_MESSED_UP.md #72, #73).
    #
    # What does track the selection is the info panel PCMonInfo redraws
    # on every cursor move (engine/pokemon/bills_pc.asm:1076-1090):
    #   hlcoord 1, 14  species name (GetBasePokemonName -- NOT the nickname)
    #   hlcoord 1, 12  PrintLevel, then gender at col 5, item icon at col 7
    # The list itself lives in a textbox at cols 8-18, rows 4-12 stepping
    # two (hlcoord 9, 4 + 2*SCREEN_WIDTH per entry), so row 14 is the
    # panel alone while row 12 must be sliced to the panel's columns.
    PC_INFO_NAME_ROW = 14
    PC_INFO_LEVEL_ROW = 12
    PC_PANEL_COLS = 8

    def pc_info(self, rows=None):
        """``{'name': SPECIES, 'level': int|None}`` for the mon under a PC
        list's sprite cursor, read off the info panel.

        `name` is the SPECIES, never the nickname, and is '' when no panel
        is up (not a PC list, or the pic is still cascading in). The
        species alone is ambiguous when a box holds duplicates -- target
        by index (wBillsPC_CursorPosition + wBillsPC_ScrollPosition) and
        use this to CONFIRM."""
        rows = self.screen() if rows is None else rows

        def row(i):
            return rows[i] if len(rows) > i else ""
        name = row(self.PC_INFO_NAME_ROW).strip()
        m = re.search(r"(\d+)", row(self.PC_INFO_LEVEL_ROW)[:self.PC_PANEL_COLS])
        return {"name": name, "level": int(m.group(1)) if m else None}

    def select_pc_mon(self, name, max_presses=24, confirm=False):
        """Move a PC list's selection until its info panel names `name`
        (species). The glyph-less counterpart of select_label: presses
        DOWN, re-reading the panel after every press, and reverses once
        when the panel stops changing (the list has pinned at an end).
        `confirm=False` by default -- an A press on a PC list opens the
        DEPOSIT/WITHDRAW submenu, and blind confirmation is exactly the
        #72 wound."""
        self.last_reason = None
        want = name.strip().upper()
        prev, flipped, direction = None, False, "D"
        for _ in range(max_presses + 1):
            info = self.pc_info()
            if info["name"].upper() == want:
                if confirm:
                    self.press("A:2 .:20")
                return True
            if info == prev:            # panel unchanged: the list pinned
                if flipped:
                    return self._fail(
                        f"select_pc_mon({name}): not in the list -- the "
                        f"panel pinned scrolling both ways (last: "
                        f"{info['name']!r})")
                direction, flipped = "U", True
            prev = info
            self.press(f"{direction}:4 .:16")
        return self._fail(f"select_pc_mon({name}): panel never named it in "
                          f"{max_presses} presses")

    # -- actions -----------------------------------------------------------

    def wait_for(self, predicate, timeout_frames=600):
        """Tick until predicate(screen_text) is true; returns success."""
        self.last_reason = None
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            if predicate(self.screen()):
                return True
            self.press(".:4")
        return self._fail(f"wait_for: predicate never true in "
                          f"{timeout_frames} frames")

    @staticmethod
    def has_label(rows, label):
        """Is a cursor sitting immediately before `label` on any row?
        EVERY glyph on the row is checked: a battle party list keeps its
        own ▷ painted while a submenu draws ▶ over it, so a leftmost-only
        read answers about the wrong list (AGENTS.md gotcha 1).

        Static on purpose: it reads nothing but `rows`, so helpers can
        call `Menus.has_label(rows, 'YES')` without an instance (a plain
        `self.menu.has_label(...)` still works)."""
        for row in rows:
            for x in _cursor_xs(row):
                if row[x + 1:].strip().startswith(label):
                    return True
        return False

    def select_label(self, label, max_presses=14, confirm=True,
                     timeout_frames=400, expect=None, expect_tries=3):
        """In the current vertical menu, press DOWN until the arrow sits on
        `label`, then press A. Works across menus because every static menu
        paints ▶ next to its entries.

        `expect` is a predicate on the screen rows describing where the
        confirm is supposed to LAND (`lambda rows: pack_open(rows)`). With
        it, the return value means "that screen is up"; without it, it only
        means "A was pressed on the right row" -- which is exactly how a
        swallowed A (gotcha 2) got reported as a successful PACK open.
        Failures leave `last_reason` set."""
        self.last_reason = None
        pressed = 0
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            rows = self.screen()
            if self.has_label(rows, label):
                if not confirm:
                    return True
                if expect is None:
                    self.press("A:2 .:10")
                    return True
                return self._expect_state(expect, f"select_label({label})",
                                          tries=expect_tries)
            if pressed >= max_presses:
                return self._fail(
                    f"select_label({label}): cursor never reached it in "
                    f"{max_presses} DOWN presses")
            self.press("D:6 .:4")
            pressed += 1
        return self._fail(f"select_label({label}): timed out after "
                          f"{timeout_frames} frames")

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
        menus that swallow the short one). Failures leave `last_reason`
        set."""
        self.last_reason = None
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
            # EVERY glyph, not the leftmost one: a submenu box painted over
            # the party list puts its ▶ to the RIGHT of the list's ▷, and
            # the band filter below is what picks the right menu's column.
            curs = [(y, x) for y, r in enumerate(rows)
                    for x in _cursor_xs(r)]
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
                return self._fail(
                    f"select_row_text({label}): not selected in "
                    f"{max_presses} presses")
            if tgt is None:
                # off-screen (scrolled list): identical rows after a
                # press mean the window pinned -- reverse once
                if last_rows is not None and rows == last_rows:
                    if flipped:
                        return self._fail(
                            f"select_row_text({label}): not on any row and "
                            f"the list pinned scrolling both ways")
                    search_dir, flipped = "U", True
                last_rows = rows
                self.press(f"{search_dir}:6 .:8")
                presses += 1
                continue
            ref = band[0] if band else (curs[0] if curs else None)
            if ref is None:
                stuck += 1
                if stuck >= 4:
                    return self._fail(
                        f"select_row_text({label}): row found but no cursor "
                        f"glyph painted -- wrong screen")
                self.press(".:6")  # tilemap paint lag: poll, don't press
                continue
            key = (ref[0], ref[1], y_tgt)
            stuck = stuck + 1 if key == prev else 0
            if stuck >= 3:
                return self._fail(
                    f"select_row_text({label}): cursor pinned at row "
                    f"{ref[0]} short of row {y_tgt} -- wrong menu or edge")
            prev = key
            self.press("D:6 .:8" if y_tgt > ref[0] else "U:6 .:8")
            presses += 1

    def select_abs(self, target, max_steps=30, confirm=True):
        """Navigate a scrolling list until the absolute selection index is
        `target`, then A. Uses WRAM position, not the text layer, so it
        works for entries scrolled off-screen."""
        self.last_reason = None
        steps = 0
        cur = None
        while steps < max_steps:
            cur = self.scroll_abs()
            if cur == target:
                if confirm:
                    self.press("A:6 .:18")
                return True
            self.press("D:6 .:4")
            steps += 1
        return self._fail(f"select_abs({target}): stopped at index {cur} "
                          f"after {max_steps} steps")

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
