"""Fishing: the overworld BAG, a rod, and the reel window.

Roughly thirty Hoenn species are reachable only from the end of a rod, so
this is the largest acquisition method the harness was missing. It is also
the most *timing* sensitive thing in the game, which is why none of it is
driven by press counts.

Three engine facts shape everything below.

**The rod is a KEY ITEM used from the field bag.** ``ItemUseOutOfBattle_Rod``
(src/item_use.c:252-261) checks ``CanFish()`` and then hands
``ItemId_GetSecondaryId(itemId)`` to ``StartFishing``. So casting is a bag
drive, not a special verb -- and if the tile is wrong the engine answers with
Dad's advice instead of a cast, silently, from inside the bag.

**The bag's cursor is in memory, twice over.** ``sCurrentBagPocket`` says which
pocket is showing and ``gBagPocketScrollStates[pocket]`` is
``{cursorPos, scrollTop, numSlots, cursorMax}`` (include/item_menu.h:15-21).
The selected row is ``scrollTop + cursorPos`` -- the engine computes it that
way itself (src/item_menu.c:446), and it matters: the KEY ITEMS pocket holds
twenty slots and only seven are on screen, so ``cursorPos`` alone names the
wrong item the moment the list scrolls. Every press here is followed by a
re-read, per the harness rule that no cursor is ever moved by arithmetic.

**The reel window is thirty frames wide.** ``Fishing8``
(src/field_player_avatar.c:1641-1652) is commented "We have a bite. Now, wait
for the player to press A, or the timer to expire", and its
``reelTimeouts[3] = {36, 33, 30}`` is indexed by the rod. Worse, the state
*before* it -- the dot game, ``Fishing5`` (:1583-1616) -- treats A as "give
up": pressing it there sets ``FISHING_NO_BITE`` on the first round and
``FISHING_GOT_AWAY`` on any later one. So mashing A does not merely waste a
cast, it is the one input that guarantees failure. The reel loop therefore
advances in two-frame slices, reads ``tStep`` every slice, and presses A on
exactly one value of it.

Nothing here is transcribed. The step constants, the ``data[]`` slot indices
and the reel timeouts are parsed out of the decompilation's own ``#define``s,
the pocket index is resolved by POINTER identity against the save block, and
the row is resolved from the live bag rather than from any table of item
order.

Three things the source does NOT say, measured live and each one the
difference between a rod that works and a rod that does nothing:

* **"It is water" is not the question; "it is water you could surf onto" is.**
  ``CanFish`` requires the faced tile's COLLISION bits to be clear. Surfing on
  Route 119 at (28,47), west is MB_OCEAN_WATER with collision 1 and the cast
  is refused; north is the same behaviour with collision 0 and it works. See
  :meth:`Fishing.faces_fishable_water`.
* **``FISHING_NO_BITE`` and ``FISHING_GOT_AWAY`` are not observable states.**
  ``Task_Fishing`` runs every state that returns TRUE in the same frame, so a
  missed bite polls as ``[..., 5, 14, 15]`` and never shows 11. See
  :meth:`Fishing.reel`.
* **The result messages pause for a button.** ``Fishing11`` sits at ``tStep``
  10 forever waiting on its own text printer, and ``flush_dialog`` cannot see
  it. One A press finishes it -- and an UNBOUNDED one fights the battle it
  just started. See :meth:`Fishing._clear_result`.
"""

from __future__ import annotations

import logging
import re

from . import cconst, cstruct, paths
from .state import ITEM_SLOT_SIZE

log = logging.getLogger("pokeagent.fishing")

# ---- engine screens, by their own symbol names --------------------------
#
# Compiler-generated names, so they are listed literally -- but they are
# still exact, because the symbol table resolves the live function pointer in
# gTasks back to them. See pokeagent.state.tasks().

#: The START menu's input handler (src/start_menu.c:387 sub_80712B4 runs
#: StartMenu_InputProcessCallback). Its presence means the list is drawn and
#: `sCurrentStartMenuActions` has been rebuilt for the current progress.
START_INPUT_TASK = "sub_80712B4"
#: The multistep that BUILDS that list (src/start_menu.c:369).
START_BUILD_TASK = "Task_StartMenu"

#: The bag list's own input handler (src/item_menu.c:1609 sub_80A50C8).
#: Presence + no fade is the only honest "the bag will accept a press".
BAG_INPUT_TASK = "sub_80A50C8"
#: The pocket-change slide animation (src/item_menu.c:1546 sub_80A4F68). The
#: list task hands control here and takes it back, so a press during the
#: slide is simply lost.
BAG_SLIDE_TASK = "sub_80A4F68"
#: The USE/REGISTER/CANCEL popup for a field bag item (src/item_menu.c:1782
#: sub_80A5414). It is a 2x2 grid: UP/DOWN flip bit 0, LEFT/RIGHT step by 2.
BAG_POPUP_TASK = "sub_80A5414"

#: ``DisplayItemMessageOnField``'s waiting task (src/menu_helpers.c:146-159
#: sub_80F9090). It runs the text printer and calls a callback when the
#: printer finishes -- and the printer PAUSES for a button, so this task sits
#: there forever if nothing presses one. After a rod USE its appearance means
#: exactly one thing: ``CanFish()`` returned FALSE and the engine answered
#: with Dad's advice.
DENIAL_TASK = "sub_80F9090"

#: src/field_player_avatar.c:1522.
FISHING_TASK = "Task_Fishing"

#: `struct SaveBlock1` field per bag pocket. Used ONLY to identify the
#: engine's pocket INDEX by pointer identity against `gBagPockets[i].itemSlots`
#: (src/item_menu.c:152-159) -- no pocket ORDER is assumed anywhere here,
#: because the order is an engine detail and getting it wrong would drive the
#: cursor through the wrong list.
POCKET_FIELDS = {
    "items": "bagPocket_Items",
    "poke_balls": "bagPocket_PokeBalls",
    "tms_hms": "bagPocket_TMHM",
    "berries": "bagPocket_Berries",
    "key_items": "bagPocket_KeyItems",
}

#: Rods, best first. The ids come from constants/items.h; the preference order
#: is the game's own progression -- a Super Rod reaches everything a Good Rod
#: does and more (src/wild_encounter.c indexes the fishing table by rod).
ROD_PREFERENCE = ("ITEM_SUPER_ROD", "ITEM_GOOD_ROD", "ITEM_OLD_ROD")

_ENUM = re.compile(r"enum\s*(?:\w+\s*)?\{([^}]*)\}", re.S)
_ENUM_MEMBER = re.compile(r"^([A-Za-z_]\w*)$")
_TASK_FIELD = re.compile(r"^#define\s+(t\w+)\s+data\[(\d+)\]\s*$", re.M)
_REEL_TIMEOUTS = re.compile(
    r"const\s+s16\s+reelTimeouts\s*\[\s*3\s*\]\s*=\s*\{([^}]*)\}"
)


def _source(rel):
    return paths.require(
        paths.PRET / rel, rel, "is the pret/ submodule checked out?"
    ).read_text(encoding="utf-8", errors="replace")


def enum_values(rel, anchor) -> dict[str, int]:
    """Members of the anonymous C enum in `rel` that declares `anchor`.

    ``MENU_ACTION_BAG`` and ``ITEM_ACTION_USE_0`` are plain anonymous enums in
    .c files, so neither the symbol table nor :mod:`pokeagent.cconst` (which
    reads ``#define``) can supply them. They are still IN the source, which is
    where they come from: nothing in this module writes a menu row number down.
    """
    for body in _ENUM.findall(_source(rel)):
        clean = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)
        members = [m.strip() for m in clean.split(",")]
        values = {}
        for i, name in enumerate(members):
            if not name:
                continue
            m = _ENUM_MEMBER.match(name)
            if not m:                      # an explicit `= N` form; not ours
                values = {}
                break
            values[m.group(1)] = i
        if anchor in values:
            return values
    raise KeyError(f"no anonymous enum declaring {anchor} in {rel}")


#: `data[]` slot per fishing field, parsed from the macros the state machine
#: itself uses (src/field_player_avatar.c:1498-1505). They are the only
#: `#define t* data[N]` macros in that file, and they are `#undef`d at the end
#: of the fishing block, so a whole-file scan cannot pick up a neighbour's.
def fishing_fields() -> dict[str, int]:
    text = _source("src/field_player_avatar.c")
    return {name: int(slot) for name, slot in _TASK_FIELD.findall(text)}


def reel_timeouts() -> tuple[int, ...]:
    """``{36, 33, 30}`` from Fishing8, indexed by rod. Parsed, because the
    reel budget is the one number this module cannot afford to guess."""
    m = _REEL_TIMEOUTS.search(_source("src/field_player_avatar.c"))
    if not m:
        raise KeyError("reelTimeouts[3] not found in src/field_player_avatar.c")
    return tuple(int(v.strip()) for v in m.group(1).split(","))


def advance_until(emu, predicate, frames=180, step=4) -> bool:
    """Run the emulator in `step`-frame slices until `predicate` holds.

    Presses NOTHING. Every wait in this module goes through here, because the
    one thing that must never happen while waiting on the fishing machine is a
    speculative button press.
    """
    spent = 0
    while spent < frames:
        if predicate():
            return True
        emu.tick(step)
        spent += step
    return bool(predicate())


class Bag:
    """The overworld BAG, driven by the engine's own cursors.

    Deliberately separate from :class:`pokeagent.battle.Battle`'s bag driver:
    that one reaches the bag through the battle action menu and returns to a
    battle, this one goes through START and returns to the field. They share
    the reading vocabulary (``sCurrentBagPocket``, ``gBagPocketScrollStates``)
    and nothing else.
    """

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.state = driver.state
        self.last_reason = None

        self.scroll = cstruct.layout_sequential(
            "PocketScrollState", "include/item_menu.h"
        )
        self.num_pockets = cconst.parse_defines(
            str(paths.INCLUDE / "item.h")
        )["NUM_BAG_POCKETS"]
        # Gotcha 12: strides come from the symbol size, never from a declared
        # sizeof, and a non-integral division is refused rather than rounded.
        self.scroll_stride = self._stride("gBagPocketScrollStates")
        self.pocket_stride = self._stride("gBagPockets")

        actions = enum_values("src/item_menu.c", "ITEM_ACTION_USE_0")
        self.action_use = actions["ITEM_ACTION_USE_0"]
        self.action_blank = actions["ITEM_ACTION_NONE"]
        self.menu_action_bag = enum_values(
            "src/start_menu.c", "MENU_ACTION_BAG"
        )["MENU_ACTION_BAG"]

    def _stride(self, symbol):
        size = self.emu.sym.size(symbol)
        if not size or size % self.num_pockets:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, not {self.num_pockets} whole "
                "pockets -- the symbol table and the ROM disagree"
            )
        return size // self.num_pockets

    def _fail(self, why) -> bool:
        self.last_reason = why
        log.info("[bag] %s", why)
        return False

    # ---- reading -------------------------------------------------------

    def fading(self) -> bool:
        """``gPaletteFade.active``. Every D-pad press that lands during a fade
        is DISCARDED -- ``sub_80A50C8`` does nothing at all while it runs."""
        return bool(self.emu.u8(self.emu.resolve("gPaletteFade") + 7) & 0x80)

    def tasks(self) -> list[str]:
        try:
            return self.state.tasks()
        except Exception:  # noqa: BLE001 - a torn read must not raise here
            return []

    def at_bag(self) -> bool:
        """The bag list is up AND will accept a press."""
        return not self.fading() and BAG_INPUT_TASK in self.tasks()

    def at_popup(self) -> bool:
        return not self.fading() and BAG_POPUP_TASK in self.tasks()

    def showing(self) -> int:
        """``sCurrentBagPocket`` -- which pocket the UI has open."""
        return self.emu.u8("sCurrentBagPocket")

    def pocket_index(self, name) -> int | None:
        """The engine's index for a named pocket, by POINTER identity.

        ``gBagPockets[i].itemSlots`` points into the save block, so the pocket
        the UI numbers as `i` can be matched to a save-block field without
        assuming any pocket order (src/item_menu.c:152-159).
        """
        try:
            field = POCKET_FIELDS[name]
        except KeyError:
            raise KeyError(
                f"unknown pocket {name!r}; known: {sorted(POCKET_FIELDS)}"
            ) from None
        want = self.emu.resolve("gSaveBlock1") + self.state.sb1[field]
        base = self.emu.resolve("gBagPockets")
        for i in range(self.num_pockets):
            if self.emu.u32(base + i * self.pocket_stride) == want:
                return i
        return None

    def slots(self, pocket) -> list[tuple[int, int]]:
        """``[(item_id, quantity), ...]`` for a pocket, in list order.

        This IS the live bag: the same ``struct ItemSlot`` array the bag list
        draws from, read through the engine's own pointer. Empty slots are
        compacted away by ``RemoveEmptyItemSlots`` while the bag is open
        (src/item_menu.c:868), so the rows are the contiguous non-empty prefix
        and the index into this list is the row number.
        """
        base = self.emu.resolve("gBagPockets") + pocket * self.pocket_stride
        addr = self.emu.u32(base)
        capacity = self.emu.u8(base + 4)
        raw = self.emu.read(addr, capacity * ITEM_SLOT_SIZE)
        out = []
        for i in range(capacity):
            item = int.from_bytes(raw[i * 4 : i * 4 + 2], "little")
            qty = int.from_bytes(raw[i * 4 + 2 : i * 4 + 4], "little")
            if item == 0:
                break
            out.append((item, qty))
        return out

    def scroll_state(self, pocket) -> dict:
        base = self.emu.resolve("gBagPocketScrollStates") + pocket * self.scroll_stride
        return {k: self.emu.u8(base + off) for k, off in self.scroll.items()}

    def row(self, pocket) -> int:
        """The selected row: ``scrollTop + cursorPos`` (src/item_menu.c:446).

        `cursorPos` is a SCREEN row, capped at `cursorMax` (7 visible rows),
        so on a long pocket it stops moving while the list scrolls underneath
        it. Reading it alone would name the seventh item forever.
        """
        s = self.scroll_state(pocket)
        return s["scrollTop"] + s["cursorPos"]

    def selected_item(self) -> int:
        """The item id the engine would act on if A were pressed right now.

        ``gCurrentBagPocketItemSlots`` is the pointer the bag itself indexes
        (src/item_menu.c:1670-1671), so this is the engine's answer, not ours.
        """
        slots = self.emu.u32("gCurrentBagPocketItemSlots")
        return self.emu.u16(slots + self.row(self.showing()) * ITEM_SLOT_SIZE)

    # ---- waiting -------------------------------------------------------

    def _wait(self, predicate, frames=180, step=4) -> bool:
        return advance_until(self.emu, predicate, frames, step)

    def _tap(self, key) -> bool:
        """One press, then wait for the list to accept input again.

        A pocket change hands the task to the slide animation; pressing again
        before it hands back is a lost press, which is indistinguishable from
        a stuck cursor unless you wait.
        """
        self.emu.run_sequence(f"{key}:4 .:8")
        return self._wait(lambda: self.at_bag() and BAG_SLIDE_TASK not in self.tasks(),
                          frames=90)

    # ---- opening -------------------------------------------------------

    def start_menu_rows(self) -> list[int]:
        """The LIVE START menu, as MENU_ACTION values.

        ``BuildStartMenuActions`` (src/start_menu.c:244-276) adds POKEDEX only
        once you own one and POKENAV only once you own that, so BAG's row moves
        with story progress. It is discovered here rather than guessed:
        ``sCurrentStartMenuActions[0 .. sNumStartMenuActions)`` is the array
        the engine itself indexes at :436.
        """
        count = self.emu.u8("sNumStartMenuActions")
        cap = self.emu.sym.size("sCurrentStartMenuActions")
        count = min(count, cap)
        return list(self.emu.read("sCurrentStartMenuActions", count))

    def open(self) -> bool:
        """START -> BAG, verified by the bag's own input task appearing."""
        self.last_reason = None
        if self.at_bag():
            return True
        if self.d.in_battle():
            return self._fail("cannot open the field bag during a battle")
        if self.d.scene_active():
            return self._fail(
                "a script owns input (sLockFieldControls/preventStep); "
                "START would be swallowed"
            )

        from .menus import Menus

        self.emu.run_sequence("START:4 .:24")
        if not self._wait(lambda: START_INPUT_TASK in self.tasks(), frames=120):
            return self._fail(
                f"START did not open the menu ({START_INPUT_TASK} never "
                f"started; tasks {self.tasks()})"
            )
        rows = self.start_menu_rows()
        if self.menu_action_bag not in rows:
            self._close_start()
            return self._fail(
                f"the live START menu {rows} has no BAG entry "
                f"(MENU_ACTION_BAG={self.menu_action_bag})"
            )
        row = rows.index(self.menu_action_bag)
        menus = Menus(self.emu, self.state)
        if not menus.select_index(row):
            self._close_start()
            return self._fail(
                f"could not put the START cursor on BAG (row {row}): "
                f"{menus.last_reason}"
            )
        if not self._wait(self.at_bag, frames=300):
            return self._fail(
                f"BAG (row {row} of {rows}) was confirmed but the bag never "
                f"became ready for input (tasks {self.tasks()}, "
                f"fading={self.fading()})"
            )
        return True

    def _close_start(self) -> None:
        for _ in range(6):
            if START_INPUT_TASK not in self.tasks() and not self.d.scene_active():
                return
            self.emu.run_sequence("B:4 .:20")

    def close(self, tries=12) -> bool:
        """Back out to the field with B, never A.

        Leaving the bag is a palette fade into a different main callback, so
        "the task is gone" is not enough -- the field has to have taken input
        back, or the next movement press vanishes.
        """
        for _ in range(tries):
            tasks = self.tasks()
            open_screens = [
                t for t in (BAG_INPUT_TASK, BAG_POPUP_TASK, START_INPUT_TASK,
                            START_BUILD_TASK)
                if t in tasks
            ]
            if not open_screens and not self.fading() and not self.d.scene_active():
                return True
            self.emu.run_sequence("B:4 .:20")
        if self.d.scene_active():
            log.warning("[bag] still locked after %d B presses; handing it to "
                        "advance_scene", tries)
            self.d.advance_scene(20000)
        return not self.d.scene_active()

    # ---- driving -------------------------------------------------------

    def select_pocket(self, pocket, tries=8) -> bool:
        """LEFT/RIGHT to a pocket index, confirmed by the engine.

        Pockets move on LEFT/RIGHT, not the shoulder buttons: the bag's D-pad
        handler routes them through ``sub_80A4F0C`` (src/item_menu.c:1529).
        """
        for _ in range(tries):
            before = self.showing()
            if before == pocket:
                break
            key = "RIGHT" if before < pocket else "LEFT"
            self._tap(key)
            if self.showing() == before:
                return self._fail(
                    f"bag pocket stuck on {before} (wanted {pocket}): "
                    f"{key} did not change it"
                )
        if self.showing() != pocket:
            return self._fail(
                f"never reached bag pocket {pocket} (still {self.showing()})"
            )
        # The engine's own row count for this pocket must agree with the slot
        # array we resolved the row from. If it does not, one of the two is
        # stale and driving the cursor would land somewhere else entirely.
        held = len(self.slots(pocket))
        counted = self.scroll_state(pocket)["numSlots"]
        if counted != held:
            return self._fail(
                f"pocket {pocket} shows numSlots={counted} but its slot array "
                f"holds {held} items -- refusing to drive a stale list"
            )
        return True

    def drive_row(self, target, tries=40) -> bool:
        """Walk the selected row to `target`, re-reading after every press.

        The list swallows its first input while it draws, so a press that does
        not move is retried ONCE -- but only once, because mashing a genuinely
        stuck menu is how a predecessor spent 90k frames on one screen.
        """
        pocket = self.showing()
        stuck = 0
        for _ in range(tries):
            cur = self.row(pocket)
            if cur == target:
                return True
            self._tap("DOWN" if target > cur else "UP")
            if self.row(pocket) == cur:
                stuck += 1
                if stuck > 1:
                    return self._fail(
                        f"bag row stuck on {cur} (wanted {target}) in pocket "
                        f"{pocket}: {self.scroll_state(pocket)}"
                    )
            else:
                stuck = 0
        return self._fail(
            f"bag row never reached {target} (still {self.row(pocket)})"
        )

    def popup_rows(self) -> list[int]:
        """The open popup's action list, as ITEM_ACTION values.

        ``sPopupMenuActionList`` is the engine's pointer to the row array it
        will index (src/item_menu.c:1829), and ``gUnknown_02038564`` is the
        row count. Reading both means USE is found rather than assumed -- the
        KEY ITEMS list is ``{USE, NONE, REGISTER, CANCEL}`` today, and a row
        number written down here would be a silent TOSS the day it is not.
        """
        ptr = self.emu.u32("sPopupMenuActionList")
        rows = self.emu.u8("gUnknown_02038564")
        return list(self.emu.read(ptr, max(0, rows)))

    def popup_cursor(self) -> int:
        return self.emu.u8("sPopupMenuSelection")

    def choose_use(self, tries=8) -> bool:
        """Pick USE out of the item popup, verified before pressing A."""
        if not self._wait(self.at_popup, frames=180):
            return self._fail(
                f"the item popup never opened ({BAG_POPUP_TASK} absent; "
                f"tasks {self.tasks()})"
            )
        rows = self.popup_rows()
        if self.action_use not in rows:
            return self._fail(
                f"the popup {rows} offers no USE action "
                f"(ITEM_ACTION_USE_0={self.action_use})"
            )
        target = rows.index(self.action_use)
        # sub_80A5414's 2x2 grid: UP/DOWN flip bit 0, LEFT/RIGHT step by two.
        for _ in range(tries):
            cur = self.popup_cursor()
            if cur == target:
                self.emu.run_sequence("A:4 .:16")
                return True
            if (cur & 1) != (target & 1):
                key = "DOWN" if target & 1 else "UP"
            else:
                key = "RIGHT" if target > cur else "LEFT"
            self.emu.run_sequence(f"{key}:4 .:12")
            if self.popup_cursor() == cur:
                return self._fail(
                    f"popup cursor stuck on {cur} (wanted USE at {target}): "
                    f"{key} did not move it"
                )
        return self._fail(
            f"popup cursor never reached USE at {target} "
            f"(still {self.popup_cursor()})"
        )

    def use(self, item_id, pocket="key_items", item_name="") -> bool:
        """Open the bag, select `item_id` in `pocket`, and confirm USE.

        Returns as soon as USE is confirmed and does NOT close the bag: what
        happens next belongs to the item. A rod fades back to the field by
        itself (``SetUpItemUseOnFieldCallback``), and a refusal keeps the bag
        up with a message the caller needs to see. Every FAILURE path closes
        it, because an open menu eats every movement press that follows.
        """
        self.last_reason = None
        label = item_name or f"item {item_id}"
        if not self.open():
            self.close()
            return False
        index = self.pocket_index(pocket)
        if index is None:
            self.close()
            return self._fail(
                f"no bag pocket matches the save block's {POCKET_FIELDS[pocket]}"
            )
        if not self.select_pocket(index):
            self.close()
            return False
        rows = [item for item, _qty in self.slots(index)]
        if item_id not in rows:
            self.close()
            return self._fail(f"{label} is not in the {pocket} pocket")
        target = rows.index(item_id)
        if not self.drive_row(target):
            self.close()
            return False
        got = self.selected_item()
        if got != item_id:
            self.close()
            return self._fail(
                f"row {target} was reached but the engine has item {got} "
                f"selected, not {label} ({item_id}) -- refusing to press A"
            )
        self.emu.run_sequence("A:4 .:20")
        if not self.choose_use():
            self.close()
            return False
        return True


class Fishing:
    """Cast a rod and play the reel game, reading the engine's own state."""

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.state = driver.state
        self.bag = Bag(driver)

        self.last_reason = None
        self.last_detail = ""
        #: `tStep` values seen during the last reel, in order, with no repeats.
        #: The honest record of what the state machine actually did.
        self.last_steps: list[int] = []
        self.last_rod = None

        consts = cconst.parse_defines(str(paths.PRET / "src/field_player_avatar.c"))
        self.START_ROUND = consts["FISHING_START_ROUND"]
        self.GOT_BITE = consts["FISHING_GOT_BITE"]
        self.ON_HOOK = consts["FISHING_ON_HOOK"]
        self.NO_BITE = consts["FISHING_NO_BITE"]
        self.GOT_AWAY = consts["FISHING_GOT_AWAY"]
        self.SHOW_RESULT = consts["FISHING_SHOW_RESULT"]

        #: The reel window. `FISHING_GOT_BITE` is "Oh! A Bite!" (Fishing7),
        #: which does `task->tStep++` and returns FALSE, so the state that
        #: actually waits for A is the NEXT one -- Fishing8, the only place in
        #: the machine where A means "reel in". Derived from the named
        #: constant rather than written as 7, and cross-checked live by
        #: `tFrameCounter` counting up against `reelTimeouts`.
        self.REEL = self.GOT_BITE + 1
        #: The dot game (Fishing5). A here is "give up" -- the one input that
        #: cannot be recovered from. Never pressed.
        self.DOT_GAME = self.START_ROUND + 1

        self.fields = fishing_fields()
        self.reel_timeouts = reel_timeouts()

        self.rod_ids = {}
        for name in ROD_PREFERENCE:
            try:
                self.rod_ids[name] = self.d.consts.items[name]
            except KeyError:  # pragma: no cover - a torn items.h
                log.warning("[fish] %s is not in constants/items.h", name)

    # ---- reading -------------------------------------------------------

    def _fail(self, code, detail) -> bool:
        self.last_reason = code
        self.last_detail = detail
        log.info("[fish] %s: %s", code, detail)
        return False

    def rod_name(self, item_id) -> str:
        return self.d.names.item(item_id)

    def held_rods(self) -> list[tuple[str, int]]:
        """``[(ITEM_* name, id), ...]`` for the rods actually in the bag,
        best first, resolved against the live bag contents."""
        pocket = self.state.bag().get("key_items", {})
        out = []
        for name in ROD_PREFERENCE:
            item_id = self.rod_ids.get(name)
            if item_id is None:
                continue
            if self.rod_name(item_id) in pocket:
                out.append((name, item_id))
        return out

    def best_rod(self) -> int | None:
        held = self.held_rods()
        return held[0][1] if held else None

    def resolve_rod(self, rod=None) -> int | None:
        """`rod` may be None (pick the best held), an item id, or a name."""
        held = dict((name, item) for name, item in self.held_rods())
        if rod is None:
            return self.best_rod()
        if isinstance(rod, int):
            return rod if rod in held.values() else None
        want = str(rod).strip().upper().replace("_", " ")
        for name, item_id in held.items():
            if want in (name.replace("ITEM_", "").replace("_", " "),
                        self.rod_name(item_id).upper()):
                return item_id
        return None

    def task(self) -> list[int] | None:
        """``Task_Fishing``'s ``data[0..15]``, or None when it is not running.

        ``state.task_data`` is the harness's existing gTasks scan -- the same
        "walk the 16 slots, resolve ``func`` through the symbol table" shape
        as ``Battle._party_cursor`` -- so the fishing state is read by the
        function pointer the engine installed, not by a task id we remembered.
        """
        return self.state.task_data(FISHING_TASK)

    def step(self) -> int | None:
        data = self.task()
        return None if data is None else data[self.fields["tStep"]]

    def snapshot(self) -> dict | None:
        data = self.task()
        if data is None:
            return None
        return {k: data[i] for k, i in self.fields.items()}

    def faces_fishable_water(self) -> tuple[bool, str]:
        """Would ``CanFish()`` (src/item_use.c:222-250) accept this tile?

        Reproduced condition by condition, because the engine's refusal is
        silent from the harness's side: it prints "DAD's advice..." from inside
        the bag and never starts a fishing task, which looks exactly like a
        cast that failed for some other reason.

        * Not underwater, and never a waterfall (both refused outright).
        * The tile ahead is surfable. Surfability comes from nav's own water
          helper, which reads ``sTileBitAttributes`` -- the same bit table
          ``MetatileBehavior_IsSurfableWaterOrUnderwater`` is built on
          (src/metatile_behavior.c:402-408) -- so this agrees with the engine
          by construction rather than by a copied list of metatile ids.
        * **Its collision bits are zero.** MEASURED, and the reason the first
          live cast was refused: surfing on Route 119 at (28,47) facing WEST,
          (27,47) is MB_OCEAN_WATER with collision 1 and elevation 0 -- water
          you may not swim into. The surfing branch of ``CanFish`` demands
          ``MapGridGetCollisionAt(x, y) == 0`` outright, and the on-foot
          branch demands ``GetCollisionAtCoords(...) == 3``, which is the
          elevation-mismatch code and therefore only reachable when the
          collision bits are clear (src/event_object_movement.c:4463-4478).
          Facing NORTH from the same tile, (28,46) is the same behaviour with
          collision 0, and the cast works. So "it is water" is not the
          question; "it is water you could surf onto" is.
        * On foot, the player must be at elevation 3 --
          ``IsPlayerFacingSurfableFishableWater`` checks ``PlayerGetZCoord()``
          explicitly (src/field_player_avatar.c:1121-1133).
        """
        if self.d.underwater():
            return False, "the player is underwater; a rod cannot be used there"
        facing = self.d.facing()
        if facing not in ("U", "D", "L", "R"):
            return False, f"the player has no facing direction ({facing!r})"
        x, y = self.d._ahead(facing)
        cell = self.d.nav.cell(self.d.map_name(), x, y)
        if cell is None:
            return False, f"({x},{y}) facing {facing} is off the map"
        # nav.WATERFALL is MB_WATERFALL; CanFish rejects it outright.
        from . import nav as nav_mod

        where = f"({x},{y}) facing {facing}"
        if cell.behavior == nav_mod.WATERFALL:
            return False, f"{where} is a waterfall, which CanFish refuses"
        if not self.d.nav._is_water(cell):
            return False, (
                f"{where} is {cell.kind} (behavior {cell.behavior:#02x}), "
                "not surfable water"
            )
        if cell.collision:
            return False, (
                f"{where} is water (behavior {cell.behavior:#02x}) but its "
                f"collision bits are {cell.collision} -- unswimmable water, "
                "which CanFish refuses"
            )
        if not self.d.is_surfing() and self.d.elevation() != 3:
            return False, (
                f"{where} is fishable water but the player is at elevation "
                f"{self.d.elevation()}, and on foot the engine requires 3"
            )
        return True, (
            f"{where} is water (behavior {cell.behavior:#02x}, collision 0, "
            f"elevation {cell.elevation})"
        )

    # ---- the reel game -------------------------------------------------

    def reel(self, poll=2, max_frames=4000) -> str:
        """Play out a live ``Task_Fishing``. Returns an outcome word:
        ``'hooked'``, ``'got-away'``, ``'no-bite'``, ``'vanished'`` or
        ``'timeout'``.

        The whole point is the two-frame slice. ``Fishing8``'s window is
        ``reelTimeouts[rod]`` frames -- thirty for the Super Rod -- so a
        thirty-frame advance can step straight over it, and A pressed one state
        earlier (the dot game) sets ``FISHING_GOT_AWAY`` on purpose. So: read
        ``tStep``, press A on exactly the reel state, press nothing on any
        other, and keep going, because ``Fishing9`` (:1655-1679) can send the
        machine back to ``FISHING_START_ROUND`` for another round.

        **``FISHING_NO_BITE`` and ``FISHING_GOT_AWAY`` are not observable
        states.** MEASURED over twenty-odd live casts: ``Task_Fishing`` is
        ``while (sFishingStateFuncs[tStep](task));`` (:1522-1526), so every
        state that returns TRUE runs its successor in the SAME frame.
        ``Fishing6`` returns TRUE after setting ``FISHING_NO_BITE``, and
        ``Fishing12`` and ``Fishing13`` both return TRUE too -- so a missed
        bite goes ``5 -> 11 -> 13 -> 14`` inside one frame and the sequence a
        poller actually sees is ``[..., 5, 14, 15]``. Waiting to observe
        ``FISHING_NO_BITE`` reported every failed cast as a timeout.

        So the outcome is inferred from the last state seen BEFORE the result
        message, which is unambiguous: the only route to the message that
        passes through the reel window is the window expiring (``Fishing8``,
        because we never press A in the dot game), and the only route that
        passes through the bite roll without reaching "Oh! A Bite!" is
        ``Fishing6`` deciding nothing bit. Direct observation of 11 or 12 is
        still preferred when the poll happens to catch it.

        The loop also STOPS at the result message rather than waiting the task
        out: ``Fishing16`` blocks on ``Menu_UpdateWindowText``, whose printer
        pauses for a button press, so the task sits there forever. Clearing
        that is ``flush_dialog``'s job, outside this loop, which keeps the "A
        on exactly one tStep" rule intact.
        """
        #: The bite roll, ``Fishing6``: the state before "Oh! A Bite!".
        bite_roll = self.GOT_BITE - 1
        self.last_steps = []
        spent = 0
        outcome = None
        previous = None
        while spent < max_frames:
            data = self.task()
            if data is None:
                if self.d.in_battle():
                    return "hooked"
                return outcome or "vanished"
            step = data[self.fields["tStep"]]
            if not self.last_steps or self.last_steps[-1] != step:
                self.last_steps.append(step)
                log.debug("[fish] tStep %d (frame counter %d, round %d)",
                          step, data[self.fields["tFrameCounter"]],
                          data[self.fields["tRoundsPlayed"]])
            if step == self.REEL:
                # The bite. Three frames of A: JOY_NEW fires on the first, and
                # the two after it are harmless because the states that follow
                # (Fishing9, Fishing10) read no input at all.
                self.emu.run_sequence("A:3 .:1")
                spent += 4
                previous = step
                continue
            if self.d.in_battle():
                return "hooked"
            if step == self.NO_BITE:
                return "no-bite"
            if step == self.GOT_AWAY:
                return "got-away"
            if step >= self.SHOW_RESULT:
                # The result message. Which message it is follows from how we
                # got here; see the docstring.
                if previous == self.REEL:
                    return "got-away"
                if previous == bite_roll:
                    return "no-bite"
                return outcome or "result"
            if step >= self.ON_HOOK:
                # Fishing10/Fishing11: "POKEMON on the hook", then the
                # encounter. in_battle() lags this by a few frames, so
                # remember it.
                outcome = "hooked"
            previous = step
            self.emu.tick(poll)
            spent += poll
        return outcome or "timeout"

    # ---- the verb ------------------------------------------------------

    def fish(self, rod=None) -> bool:
        """Cast, reel, and return True only if a wild encounter started."""
        self.last_reason = None
        self.last_detail = ""
        self.last_steps = []
        self.last_rod = None

        if self.d.in_battle():
            return self._fail("cast-failed", "already in a battle")

        item_id = self.resolve_rod(rod)
        if item_id is None:
            held = [n for n, _ in self.held_rods()]
            return self._fail(
                "no-rod",
                f"no usable rod: asked for {rod!r}, bag holds {held or 'none'}",
            )
        self.last_rod = item_id
        name = self.rod_name(item_id)

        ok, why = self.faces_fishable_water()
        if not ok:
            return self._fail("wrong-tile", why)

        if not self.bag.use(item_id, "key_items", name):
            self.bag.close()
            self.d.flush_dialog()
            return self._fail(
                "cast-failed", f"the bag would not USE {name}: {self.bag.last_reason}"
            )

        # USE fades out of the bag, runs `Task_CallItemUseOnFieldCallback`
        # (src/item_menu.c) and only THEN calls StartFishing. Nothing is
        # pressed while that happens: a press here lands in the bag's exit
        # fade or, worse, on the refusal message.
        if not self.bag._wait(lambda: self.task() is not None, frames=420):
            denied = DENIAL_TASK in self.bag.tasks()
            tasks = self.bag.tasks()
            # Dad's advice waits on its own text printer, not on
            # Task_FieldMessageBox, so clear it before backing out -- B does
            # nothing to it and the field stays locked.
            self.d.flush_dialog()
            self.bag.close()
            self.d.settle(180)
            if denied:
                # `CanFish()` said no after all, and the only honest thing to
                # call that is the tile. Reaching here means the pre-check
                # above disagreed with the engine, so the DETAIL is the whole
                # value: it names the tile that was refused.
                return self._fail(
                    "wrong-tile",
                    f"the engine refused {name} on this tile: "
                    f"{DENIAL_TASK} (DisplayItemMessageOnField, Dad's advice) "
                    f"came up instead of {FISHING_TASK}, for {why}",
                )
            return self._fail(
                "cast-failed",
                f"{name} was USEd but {FISHING_TASK} never started; "
                f"tasks {tasks}",
            )

        data = self.task()
        rod_index = data[self.fields["tFishingRod"]]
        window = (self.reel_timeouts[rod_index]
                  if 0 <= rod_index < len(self.reel_timeouts) else None)
        log.info("[fish] casting %s (rod index %d, reel window %s frames)",
                 name, rod_index, window)

        outcome = self.reel()
        presses = self._clear_result()
        if outcome == "hooked" or self.task() is None:
            # `FishingWildEncounter` starts a battle TRANSITION. `in_battle()`
            # went true 80 frames after the task vanished, measured -- and
            # nothing may be pressed in between.
            advance_until(self.emu, self.d.in_battle, frames=600)

        if not self.d.in_battle():
            # Only now is `flush_dialog` safe. See `_clear_result`: it presses
            # A until nothing is locked, and if it does that while a battle is
            # starting it fights the battle -- which is what happened. Seven
            # casts in one run reported `cast-failed` after reaching the hook
            # state because the mashing had already picked FIGHT, run the
            # turn, and won: `in_battle()` was false again by the time it was
            # read, and the lead came back a level higher and 72 HP down.
            self.d.flush_dialog()
            self.d.settle(180)

        if self.d.in_battle():
            self.last_reason = None
            self.last_detail = (
                f"{name} hooked something (steps {self.last_steps}, "
                f"{presses} press(es) to clear the hook message)"
            )
            return True
        if outcome == "got-away":
            return self._fail("got-away",
                              f"the bite escaped the reel window ({window} "
                              f"frames); steps {self.last_steps}")
        if outcome == "no-bite":
            return self._fail("no-bite",
                              f"not even a nibble; steps {self.last_steps}")
        return self._fail(
            "cast-failed",
            f"{FISHING_TASK} ended as {outcome!r} with no encounter; "
            f"steps {self.last_steps}",
        )

    #: Presses allowed to clear the fishing machine's own text printer.
    RESULT_PRESSES = 8

    def _clear_result(self) -> int:
        """Unstick the fishing task's own paused text printer.

        MEASURED, and the last thing standing between a bite and a battle:
        ``Fishing11`` blocks on ``Menu_UpdateWindowText()`` for "POKeMON on
        the hook!" and that printer PAUSES for a button. Left alone the task
        sat at ``tStep`` 10 for 320 idle frames with no sign of moving --
        ``scene_active`` true, ``Task_FieldMessageBox`` absent, so
        ``flush_dialog`` had nothing it recognises to press against.
        Exactly one A press destroyed the task, and the battle started 80
        frames later. ``Fishing16`` pauses the same way on "It got away" and
        "Not even a nibble".

        A is safe here in a way it is emphatically not two states earlier:
        every state above the reel window -- ``Fishing9`` onward
        (src/field_player_avatar.c:1655-1784) -- reads NO input at all, so A
        can only advance the printer. It cannot change what the machine
        decided.

        Bounded, and it stops the instant the task is gone. That bound is the
        point: an unbounded A press is what turned seven hooked casts into
        auto-fought battles.
        """
        presses = 0
        while presses < self.RESULT_PRESSES:
            data = self.task()
            if data is None:
                break
            if data[self.fields["tStep"]] <= self.REEL:
                break
            self.emu.run_sequence("A:4 .:16")
            presses += 1
        return presses
