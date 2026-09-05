"""Driving the naming keyboard deliberately.

Mashing A on this screen types the first key under the cursor over and over.
A first attempt at the intro produced the player name ``AAAAAAA`` -- the same
failure Crystal's journal records for its own intro script ("the known
vega_intro stray-A naming bug", PROGRESS.md session claude-wren). It is the
canonical instance of the harness's #1 recurring bug class: a blind A-loop
into a menu whose state nobody read.

So nothing here is blind. The keyboard's own tables are read out of ROM:

* ``sKeyboardCharacters[page][row][col]`` -- 3 pages x 4 rows x 20 charmap
  bytes (src/naming_screen.c:1772 and the table at :1500ish)
* ``sKeyboardSymbolPositions[page][cursorX]`` -- which of those 20 columns a
  cursor column actually lands on (src/naming_screen.c:997-1009)

and the live cursor comes from the sprite the game draws it with:
``gSprites[namingScreenData.cursorSpriteId].data[0..1]``
(src/naming_screen.c:GetCursorPos). That is the same "cursor is an OAM sprite,
not a glyph" situation that made Crystal's PC box unreadable (its journal
#72/#73); here the sprite is simply read.

Every keystroke is verified against ``namingScreenData.textBuffer`` before the
next one is sent, so a swallowed press is retried instead of silently
shifting the whole name.
"""

import logging

from . import cstruct

log = logging.getLogger("pokeagent.naming")

#: include/ewram.h:71 -- namingScreenData is an overlay on gSharedMem + 0.
NAMING_DATA_OFFSET = 0x0
#: src/naming_screen.c:24 (ENGLISH)
COLUMN_COUNT = 9
ROW_COUNT = 4
#: The keyboard row strings are 20 bytes wide.
KEYBOARD_ROW_WIDTH = 20
PAGE_COUNT = 3
PAGE_NAMES = ("UPPER", "LOWER", "OTHERS")

#: include/sprite.h:207 -- s16 data[8] inside struct Sprite.
SPRITE_DATA_OFFSET = 0x2E
MAX_SPRITES = 64

#: Terminator/backspace sentinels in the keyboard table.
KEY_BACKSPACE = 0xFE
KEY_OK = 0xFF


class NamingScreen:
    """A live view of the naming keyboard, and a way to type into it."""

    def __init__(self, emu, state):
        self.emu = emu
        self.state = state
        self.nsd = cstruct.layout("NamingScreenData", "naming_screen.h")
        # gSprites holds MAX_SPRITES + 1 entries (pret/include/sprite.h), so
        # dividing by MAX_SPRITES gives 4420 // 64 = 69 and every sprite after
        # the first is read at the wrong offset -- which is exactly what
        # `cursor()` does for any cursorSpriteId > 0. The correct stride is
        # 4420 // 65 = 68, and `pokeagent/battle.py` already derives it that
        # way. Found by a sibling agent reading this while driving the Name
        # Rater's keyboard; it did not bite that run only because the cursor
        # sprite happened to be id 0.
        self._sprite_size = emu.sym.size("gSprites") // (MAX_SPRITES + 1)
        self._layout = None

    # ---- reading the screen ------------------------------------------

    @property
    def base(self):
        return self.emu.resolve("gSharedMem") + NAMING_DATA_OFFSET

    def is_open(self) -> bool:
        """True when the keyboard owns input.

        Asks the task list, not the screen: Crystal's equivalent predicate is
        still `"DEL" in screen and "END" in screen` (its journal #6).
        """
        return "Task_NamingScreenMain" in self.state.tasks()

    def page(self) -> int:
        return self.emu.u8(self.base + self.nsd["currentPage"])

    def text(self) -> str:
        raw = self.emu.read(self.base + self.nsd["textBuffer"], 0x10)
        return self.emu.charmap.decode(raw)

    def cursor(self) -> tuple[int, int]:
        """``(x, y)`` of the selection, read off the cursor sprite."""
        sprite_id = self.emu.u8(self.base + self.nsd["cursorSpriteId"])
        addr = (
            self.emu.resolve("gSprites")
            + sprite_id * self._sprite_size
            + SPRITE_DATA_OFFSET
        )
        raw = self.emu.read(addr, 4)
        x = int.from_bytes(raw[0:2], "little", signed=True)
        y = int.from_bytes(raw[2:4], "little", signed=True)
        return x, y

    # ---- the keyboard's own layout, from ROM --------------------------

    def keyboard(self) -> dict:
        """``{page: {(x, y): character}}``, decoded from the ROM tables."""
        if self._layout is not None:
            return self._layout
        chars = self.emu.read(
            "sKeyboardCharacters", PAGE_COUNT * ROW_COUNT * KEYBOARD_ROW_WIDTH
        )
        cols = self.emu.read("sKeyboardSymbolPositions", PAGE_COUNT * COLUMN_COUNT)
        cm = self.emu.charmap
        out = {}
        for page in range(PAGE_COUNT):
            grid = {}
            for y in range(ROW_COUNT):
                row = chars[
                    (page * ROW_COUNT + y) * KEYBOARD_ROW_WIDTH :
                    (page * ROW_COUNT + y + 1) * KEYBOARD_ROW_WIDTH
                ]
                for x in range(COLUMN_COUNT):
                    kb_col = cols[page * COLUMN_COUNT + x]
                    byte = row[kb_col] if kb_col < len(row) else KEY_OK
                    if byte in (KEY_OK, KEY_BACKSPACE):
                        continue
                    ch = cm.decode(bytes([byte]), stop_at_eos=False)
                    if ch and ch != "\ufffd":
                        grid[(x, y)] = ch
            out[page] = grid
        self._layout = out
        return out

    def find(self, ch) -> tuple[int, int, int] | None:
        """``(page, x, y)`` for a character, preferring the current page."""
        kb = self.keyboard()
        here = self.page()
        for page in sorted(kb, key=lambda p: p != here):
            for (x, y), c in kb[page].items():
                if c == ch:
                    return page, x, y
        return None

    # ---- typing --------------------------------------------------------

    def _swap_page(self, want):
        """SELECT cycles pages (src/naming_screen.c InputState_Enabled)."""
        for _ in range(PAGE_COUNT):
            if self.page() == want:
                return True
            self.emu.run_sequence("SELECT:4 .:20")
            self._settle_open()      # the swap animation is states 4 and 5
        return self.page() == want

    #: Letter columns are 0..7; column 8 is the OK / BACK strip beside the
    #: grid, which has fewer rows than the letters do. A cursor parked there
    #: cannot reach every row by pressing DOWN, so it is walked back into the
    #: grid first. The nickname keyboard opens with the cursor on that strip,
    #: which is why typing a catch's name failed while the player-name
    #: keyboard (cursor at 0,0) always worked.
    GRID_COLUMNS = 8

    #: `namingScreenData.state` indexes `sMainStateFuncs`
    #: (src/naming_screen.c:365-377), and the D-pad is only read by
    #: `MainState_HandleInput` -- index 2. Everything before it is the fade-in
    #: (`MainState_BeginFadeIn`, `MainState_WaitFadeIn`, which is what calls
    #: `SetInputState(INPUT_STATE_ENABLED)`), and 4/5 are the page-swap
    #: animation. Pressing outside state 2 is pressing at nothing.
    INPUT_STATE = 2

    def main_state(self) -> int:
        """Which `sMainStateFuncs` entry is running (2 == input enabled)."""
        return self.emu.u8(self.base + self.nsd["state"])

    def _settle_open(self, frames: int = 240) -> bool:
        """Wait until the keyboard's input handler is actually running.

        This used to tick a flat 30 frames and hope. That was measured on the
        PLAYER-name keyboard, which is entered from a quiet screen; the CATCH
        nickname keyboard arrives straight out of a battle with its fade still
        running, so 30 frames left it in `MainState_WaitFadeIn` and every press
        of the walk went nowhere. The cursor then never reached the first
        letter and naming failed on character one, every time -- "could not
        move the cursor to 'G' at (0,1)" for a GOLDEEN, and the same for
        WAILMER, LINOONE and MARILL before it, all of which took default names.

        Waits on the engine's own state rather than a frame count, so it costs
        only what the fade actually costs.
        """
        for _ in range(max(1, frames // 4)):
            if self.main_state() == self.INPUT_STATE:
                return True
            self.emu.tick(4)
        return self.main_state() == self.INPUT_STATE

    def _move_to(self, x, y, tries=40):
        """Walk the cursor to (x, y). Columns wrap, rows do not always, so
        step one press at a time and re-read rather than computing a path."""
        for _ in range(tries):
            if self.main_state() != self.INPUT_STATE:
                # A page swap (states 4/5) or a fade is running; a press now is
                # swallowed, so wait it out instead of spending a try on it.
                self.emu.tick(8)
                continue
            cx, cy = self.cursor()
            if (cx, cy) == (x, y):
                return True
            if cx >= self.GRID_COLUMNS:
                self.emu.run_sequence("LEFT:4 .:8")
                continue
            if cy != y:
                self.emu.run_sequence("DOWN:4 .:12" if (y - cy) % ROW_COUNT <= ROW_COUNT // 2 else "UP:4 .:12")
                continue
            if cx != x:
                # Walk columns DIRECTLY, never around the ring. Wrapping is
                # shorter on paper and routes through column 8, the OK strip,
                # where the guard above pushes the cursor back into the grid
                # -- the two then fight each other and burn the whole budget.
                # Measured: typing "BLAZE" got as far as "BL" and gave up.
                self.emu.run_sequence("RIGHT:4 .:12" if x > cx else "LEFT:4 .:12")
        return self.cursor() == (x, y)

    def type(self, name, confirm=True) -> str:
        """Type `name` and (by default) press OK. Returns what actually landed.

        Raises if a character is not on the keyboard or if the buffer stops
        advancing -- a silently truncated name is worse than a loud failure,
        because the run then carries the wrong player name forever.
        """
        if not self.is_open():
            raise RuntimeError("the naming keyboard is not open")
        self._settle_open()

        for ch in name:
            target = self.find(ch)
            if target is None:
                raise ValueError(f"{ch!r} is not on the Sapphire naming keyboard")
            page, x, y = target
            if not self._swap_page(page):
                raise RuntimeError(f"could not reach keyboard page {PAGE_NAMES[page]}")
            if not self._move_to(x, y):
                raise RuntimeError(f"could not move the cursor to {ch!r} at ({x},{y})")

            before = self.text()
            for attempt in range(4):
                self.emu.run_sequence("A:4 .:14")
                if self.text() != before:
                    break
            else:
                raise RuntimeError(
                    f"typing {ch!r} did not change the name buffer "
                    f"(still {before!r}) -- the keyboard swallowed 4 presses"
                )
            log.debug("typed %r -> %r", ch, self.text())

        typed = self.text()
        if typed != name:
            raise RuntimeError(f"wanted to type {name!r} but the buffer holds {typed!r}")

        if confirm:
            # START jumps the cursor to OK (InputState_Enabled), then A takes it.
            self.emu.run_sequence("START:4 .:20 A:4 .:30")
        return typed

    def accept(self) -> str:
        """Confirm whatever is in the buffer, WITHOUT typing into it.

        START moves the cursor to OK (`naming_screen.c:681-685` calls
        `MoveCursorToOKButton`) and A takes it. The old one-shot
        `START:4 .:20 A:4 .:30` was wrong in a way that produced real damage:
        the first press after a menu is drawn gets swallowed, and when START
        is the one lost, the A that follows lands on the KEYBOARD GRID and
        types the character under it -- which starts on 'A'. That is where
        every mon named "A" in this save file came from, and the log said
        "accepted the default name 'A'" while doing it.

        So this settles first, and then verifies by outcome: if the keyboard
        did not close, the buffer is compared to what it was, and a character
        we accidentally typed is removed with B (KBEVENT_PRESSED_B is
        backspace) before trying again.

        An empty buffer is not an error. For a catch the engine has already
        loaded the species name as the destination, and `sub_80B74B0`
        (`naming_screen.c:1577-1589`) only copies the buffer over it when the
        buffer holds a real character -- so confirming empty gives exactly the
        species name, which is what declining the prompt would have produced.
        """
        for _ in range(8):
            before = self.text()
            # SETTLE FIRST: the frame a menu is drawn, its input loop is not
            # running yet, and the press is discarded.
            self.emu.run_sequence(".:24")
            self.emu.run_sequence("START:6 .:30")
            self.emu.run_sequence("A:6 .:30")
            if not self.is_open():
                return before
            if self.text() != before:
                # START was swallowed; the A typed. Take it back out.
                self.emu.run_sequence("B:6 .:24")
        return self.text()


def type_name(emu, state, name):
    """Convenience wrapper used by the intro script."""
    return NamingScreen(emu, state).type(name)
