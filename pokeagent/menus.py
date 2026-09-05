"""Menu navigation: the analog of ``crystalagent/menus.py``.

Crystal drove menus by finding a cursor glyph in the decoded text screen --
two different glyphs, in fact, and its journal is full of the consequences
(#1 two cursor shapes, #67 an elevator that picked CANCEL twice, #72 a PC
list with no glyph at all that deposited five party members).

Sapphire needs none of that. The engine keeps the selection in memory:

* ``gMenu.cursorPos`` (src/menu.c:14-25) for the generic overworld menu,
  bounded by ``minCursorPos``/``maxCursorPos``
* ``gActionSelectionCursor[battler]`` / ``gMoveSelectionCursor[battler]``
  for the two battle menus (fixed EWRAM addresses)

so selecting an entry is: read where the cursor is, step it to where you
want it, verify it arrived, THEN press A. Never count presses, never assume
a default.
"""

import logging

from . import cstruct

log = logging.getLogger("pokeagent.menus")

#: src/menu.c -- Menu_ProcessInput's YES/NO list is index 0 = YES, 1 = NO.
YES, NO = 0, 1


class Menus:
    """Cursor-accurate menu driving."""

    def __init__(self, emu, state):
        self.emu = emu
        self.state = state
        self.menu = cstruct.layout_sequential("Menu", "src/menu.c")
        self.last_reason = None

    def _fail(self, why):
        self.last_reason = why
        log.debug("menu: %s", why)
        return False

    # ---- the generic overworld menu -----------------------------------

    @property
    def _base(self):
        return self.emu.resolve("gMenu")

    def cursor(self) -> int:
        return self.emu.u8(self._base + self.menu["cursorPos"])

    def bounds(self) -> tuple[int, int]:
        return (
            self.emu.u8(self._base + self.menu["minCursorPos"]),
            self.emu.u8(self._base + self.menu["maxCursorPos"]),
        )

    def select_index(self, index, confirm=True, tries=12) -> bool:
        """Move the cursor to `index` and press A.

        Verifies the cursor landed before confirming. A menu that ignores the
        d-pad (because it is mid-draw) would otherwise silently take whatever
        was under the cursor -- that is exactly how the predecessor bought
        items it never meant to (its gotcha 13).
        """
        self.last_reason = None
        lo, hi = self.bounds()
        if not lo <= index <= hi:
            return self._fail(f"index {index} outside menu bounds {lo}..{hi}")
        for _ in range(tries):
            cur = self.cursor()
            if cur == index:
                break
            self.emu.run_sequence("DOWN:4 .:8" if cur < index else "UP:4 .:8")
        else:
            return self._fail(
                f"cursor stuck at {self.cursor()} trying to reach {index}"
            )
        if confirm:
            self.emu.run_sequence("A:4 .:16")
        return True

    def resolve_choice(self, choice="YES") -> bool:
        """Answer an open YES/NO box deliberately.

        Nothing answered these in the predecessor's mart flow, so purchases
        silently never happened while the code reported success (its journal
        #88). Reading the cursor makes that impossible.
        """
        want = YES if str(choice).upper() == "YES" else NO
        return self.select_index(want)

    #: `struct MultichoiceListStruct { const struct MenuAction *list; u8 count; }`
    #: and `struct MenuAction { const u8 *text; void (*func)(void); }`
    #: (src/script_menu.c). Both pad to 8 bytes on the GBA -- confirmed
    #: against the linker map rather than counted off the header, which is the
    #: mistake that has cost this project three separate struct readers.
    _MULTI_ENTRY = 8
    _ACTION_ENTRY = 8

    def multichoice_labels(self, count=None) -> list:
        """Every ROM multichoice list, decoded, optionally of a given length.

        `gMultichoiceLists` is the game's own table of the option lists its
        scripts can put on screen, so the labels come from the cartridge
        rather than from a screen scrape or a guess.
        """
        import struct

        base = self.emu.resolve("gMultichoiceLists")
        size = self.emu.sym.size("gMultichoiceLists") or 0
        out = []
        for i in range(size // self._MULTI_ENTRY):
            ptr, n = struct.unpack(
                "<II", bytes(self.emu.read(base + i * self._MULTI_ENTRY, 8)))
            if not ptr or not 0 < n <= 16:
                continue
            if count is not None and n != count:
                continue
            labels = []
            raw = bytes(self.emu.read(ptr, n * self._ACTION_ENTRY))
            for j in range(n):
                text_ptr = struct.unpack_from("<I", raw, j * self._ACTION_ENTRY)[0]
                if not text_ptr:
                    labels.append("")
                    continue
                labels.append(
                    self.emu.charmap.decode(bytes(self.emu.read(text_ptr, 16))).strip())
            out.append(labels)
        return out

    #: The task that owns a script multichoice box (script_menu.c:670). While
    #: it is absent the box is not on screen, whatever `gMenu` still holds.
    MULTICHOICE_TASK = "Task_HandleMultichoiceInput"

    def choice_is_up(self) -> bool:
        """Is the option box on screen RIGHT NOW, without touching anything?

        The passive question. `wait_for_choice` answers the active one and
        presses A to get there, which is only safe when a box is genuinely
        expected.
        """
        return self.MULTICHOICE_TASK in self.state.tasks()

    def wait_for_choice(self, max_presses=16) -> bool:
        """Advance the conversation until the option box is actually up.

        `gMenu`'s cursor and bounds are LEFTOVERS until the box is drawn, so
        they read as a plausible open menu while a message box is still
        printing -- and every d-pad press vanishes into the dialog. Mr.
        Briney's greeting takes EIGHT A presses before the box appears; the
        cursor sat unmoved at the default the whole time, which is
        indistinguishable from a menu that ignores input.

        Pressing A here is safe in a way it is not elsewhere: the box has not
        been drawn, so there is no option to select by accident.
        """
        for _ in range(max_presses):
            if self.MULTICHOICE_TASK in self.state.tasks():
                return True
            self.emu.run_sequence("A:4 .:24")
        return self.MULTICHOICE_TASK in self.state.tasks()

    def select_label(self, label, among=None, tries=12, press=True) -> bool:
        """Pick an open choice box's option BY NAME.

        `select_index` needs a number, and a number written into a story step
        is a magic constant that rots the moment a list changes. Mr. Briney's
        "Where are we bound?" is PETALBURG / SLATEPORT / CANCEL, so answering
        it with the generic YES takes option 0 and sails the run BACK to the
        mainland it just left.

        The index is resolved from `gMultichoiceLists`, filtered to lists as
        long as the box actually on screen. That is often enough, but not
        always: SLATEPORT alone appears at position 0 in some three-option
        lists and position 1 in others, and guessing between them would sail
        the run to the wrong town. `among` names the whole expected box
        (`("PETALBURG", "SLATEPORT", "CANCEL")`) and is matched against the
        ROM's lists, so the caller states its expectation and the cartridge
        confirms it rather than either one being trusted alone.

        Ambiguity is refused, never guessed: picking the wrong option in a
        story scene is unrecoverable without a savestate.
        """
        want = str(label).strip().upper()
        if press:
            armed = self.wait_for_choice()
        else:
            armed = self.choice_is_up()
        if not armed:
            return self._fail(f"{self.MULTICHOICE_TASK} never started")
        lo, hi = self.bounds()
        count = hi - lo + 1
        if count <= 1:
            return self._fail(f"no choice box open (cursor bounds {lo}..{hi})")
        lists = self.multichoice_labels(count)
        if among is not None:
            expected = [str(x).strip().upper() for x in among]
            if len(expected) != count:
                return self._fail(
                    f"expected a {len(expected)}-option box, screen has {count}")
            lists = [l for l in lists
                     if [t.strip().upper() for t in l] == expected]
            if not lists:
                return self._fail(f"no ROM list reads {expected}")
        found = set()
        for labels in lists:
            for i, text in enumerate(labels):
                if text.strip().upper() == want:
                    found.add(i)
        if not found:
            return self._fail(f"no {count}-option ROM list offers {want!r}")
        if len(found) > 1:
            return self._fail(
                f"{want!r} is ambiguous across {count}-option lists: {sorted(found)}")
        index = found.pop()
        if not lo <= index <= hi:
            return self._fail(f"{want!r} is option {index}, outside {lo}..{hi}")
        return self.select_index(index, tries=tries)

    def wait_for_task(self, task_name, max_frames=1200, step=10) -> bool:
        """Advance until `task_name` is running. Task names come from the
        symbol table, so this is an exact predicate."""
        spent = 0
        while spent < max_frames:
            if task_name in self.state.tasks():
                return True
            self.emu.tick(step)
            spent += step
        return self._fail(f"{task_name} never started within {max_frames} frames")

    def wait_while_task(self, task_name, max_frames=3000, step=10) -> bool:
        spent = 0
        while spent < max_frames:
            if task_name not in self.state.tasks():
                return True
            self.emu.tick(step)
            spent += step
        return self._fail(f"{task_name} still running after {max_frames} frames")

    # ---- battle menus ---------------------------------------------------

    def _battler(self) -> int:
        return self.emu.u8("gActiveBattler")

    def action_cursor(self) -> int:
        return self.emu.u8(("gActionSelectionCursor", self._battler()))

    def move_cursor(self) -> int:
        return self.emu.u8(("gMoveSelectionCursor", self._battler()))

    def select_battle_action(self, index, tries=10) -> bool:
        """FIGHT / BAG / POKEMON / RUN is a 2x2 grid: 0 1 on the top row,
        2 3 on the bottom. Navigate with the axis that actually differs."""
        self.last_reason = None
        for _ in range(tries):
            cur = self.action_cursor()
            if cur == index:
                self.emu.run_sequence("A:4 .:20")
                return True
            if (cur & 2) != (index & 2):
                self.emu.run_sequence("DOWN:4 .:10" if index & 2 else "UP:4 .:10")
            else:
                self.emu.run_sequence("RIGHT:4 .:10" if index & 1 else "LEFT:4 .:10")
        return self._fail(
            f"battle action cursor stuck at {self.action_cursor()}, wanted {index}"
        )

    def select_move(self, slot, tries=10) -> bool:
        """The move picker is also a 2x2 grid over the four move slots."""
        self.last_reason = None
        for _ in range(tries):
            cur = self.move_cursor()
            if cur == slot:
                self.emu.run_sequence("A:4 .:20")
                return True
            if (cur & 2) != (slot & 2):
                self.emu.run_sequence("DOWN:4 .:10" if slot & 2 else "UP:4 .:10")
            else:
                self.emu.run_sequence("RIGHT:4 .:10" if slot & 1 else "LEFT:4 .:10")
        return self._fail(
            f"move cursor stuck at {self.move_cursor()}, wanted slot {slot}"
        )

    def close(self, max_presses=10) -> bool:
        """Back out of whatever is open with B, never A."""
        for _ in range(max_presses):
            if not self.state.tasks() or "Task_FieldMessageBox" not in self.state.tasks():
                if not self.emu.u8("sLockFieldControls"):
                    return True
            self.emu.run_sequence("B:4 .:16")
        return self._fail("still locked after B presses")
