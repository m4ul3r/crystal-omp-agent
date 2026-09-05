"""Drive the RSE Pokemon Storage System deliberately, by reading its cursor.

Why this exists: 22 of the missing dex species are evolutions of Pokemon
sitting in the PC boxes, and there is no way to get a boxed mon into the party
except through this UI. The harness had never driven it -- `deposit`/`withdraw`
existed only in the Crystal tree -- so every one of those 22 slots was
unreachable.

Why it is safe to drive: the storage system keeps its selection in two plain
EWRAM bytes, so nothing here reads pixels or counts button presses.

    sBoxCursorArea      0 = the box grid, 1 = the party
                        (src/pokemon_storage_system_4.c:948-958)
    sBoxCursorPosition  index within that area
    gPokemonStorage.currentBox   which of 14 boxes is shown
                        (include/pokemon.h:325; boxes[14][30] at 0x0004)

That is the Gen-3 answer to Crystal's gotcha 18. A box list draws its
selection with an OAM sprite and has no cursor glyph to read, so "press A
until the text stops changing" is a repeat-action loop -- in Crystal it
deposited five of six party members, including the run's only real fighter.
Here the index is readable, so the cursor is moved by FEEDBACK (press, re-read,
compare) rather than by assuming a grid geometry, and every mutation is
verified against `state.party()` afterwards.

The measured flow, screenshotted while establishing it:

    A   "<PLAYER> booted up the PC."
    A   multichoice: SOMEONE'S PC / <PLAYER>'s PC / LOG OFF   (option 0)
    A   "Accessed someone's PC."
    A   "POKEMON Storage System opened."
    ->  WITHDRAW POKEMON / DEPOSIT POKEMON / MOVE POKEMON / SEE YA!
        (src/pokemon_storage_system.c:28-33, in that order)

WITHDRAW with six party members answers "Your party is full!" and refuses, so
a deposit has to come first. That refusal is clean -- nothing is corrupted --
but it is why `make_room` exists.
"""

import logging

log = logging.getLogger("storage")

#: include/constants/metatile_behaviors.h:135. `behaviors.kind()` has no "pc"
#: case, so a PC counter classifies as "blocked" like any other solid tile and
#: `find_tiles("pc")` cannot find one.
MB_PC = 0x83

AREA_BOX = 0
AREA_PARTY = 1
#: The BOX TITLE row, above the grid. `sub_809C85C`
#: (pret/src/pokemon_storage_system_4.c:2078-2116) is the handler that runs
#: while the cursor is here, and it is the ONLY one that pages boxes:
#:     if (JOY_HELD(DPAD_LEFT))  return 10;   // previous box
#:     if (JOY_HELD(DPAD_RIGHT)) return 9;    // next box
#: The grid handler reaches it with DPAD_UP from the top row (`cursorArea = 2`
#: at :2178).
AREA_TITLE = 2

#: Storage main-menu row order (src/pokemon_storage_system.c:28-33).
MENU_WITHDRAW = 0
MENU_DEPOSIT = 1
MENU_MOVE = 2
MENU_SEE_YA = 3

BOX_SLOTS = 30          # boxes[14][30]
BOX_COUNT = 14


class Storage:
    """The PC box UI, driven by its own cursor."""

    def __init__(self, driver):
        self.d = driver
        self.last_reason = None

    # ---- reading -------------------------------------------------------

    def cursor(self):
        """``(area, position)``, or ``(None, None)`` if unreadable."""
        try:
            return (self.d.emu.u8("sBoxCursorArea"),
                    self.d.emu.u8("sBoxCursorPosition"))
        except Exception:  # noqa: BLE001 - a read must not end a run
            return (None, None)

    def _cursor_area(self) -> int:
        """`sBoxCursorArea`: 0 grid, 1 party, 2 box title, 3 the button row."""
        try:
            return int(self.d.emu.u8("sBoxCursorArea"))
        except Exception:  # noqa: BLE001
            return -1

    def current_box(self):
        try:
            return self.d.emu.u8(("gPokemonStorage", 0))
        except Exception:  # noqa: BLE001
            return None

    def pc_cells(self, map_name=None):
        """Every PC tile on a map, by metatile BEHAVIOUR."""
        map_name = map_name or self.d.map_name()
        grid = self.d.nav.grid(map_name)
        return [(x, y)
                for y, row in enumerate(grid)
                for x, c in enumerate(row)
                if c.behavior == MB_PC]

    def party_names(self):
        return [m.nickname for m in self.d.state.party() if m.species]

    def _fail(self, why) -> bool:
        self.last_reason = why
        log.info("[storage] %s", why)
        return False

    # ---- opening -------------------------------------------------------

    def box_counts(self) -> list[int]:
        """How many mons are in each of the 14 boxes.

        Read straight out of `gPokemonStorage.boxes`, because the deposit flow
        ends in a box PICKER and choosing a full box gets refused. Box 1 of
        this save is 30/30, which is exactly how the first working deposit
        failed -- the screenshot said "Deposit in which BOX? BOX1 30/30".
        """
        from pokeagent import pokemon
        base = self.d.emu.resolve("gPokemonStorage") + 4      # boxes[] at 0x4
        span = BOX_COUNT * BOX_SLOTS * pokemon.BOX_SIZE
        blob = self.d.emu.read(base, span)
        out = []
        for b in range(BOX_COUNT):
            n = 0
            for s in range(BOX_SLOTS):
                off = (b * BOX_SLOTS + s) * pokemon.BOX_SIZE
                mon = pokemon.parse_mon(blob[off:off + pokemon.BOX_SIZE])
                if mon is not None and mon.checksum_ok and mon.species:
                    n += 1
            out.append(n)
        return out

    def first_free_box(self) -> int | None:
        for i, n in enumerate(self.box_counts()):
            if n < BOX_SLOTS:
                return i
        return None

    #: A presses from facing the PC to the storage MAIN MENU, and no further.
    #: COUNTED off screenshots, not guessed:
    #:   1 "<PLAYER> booted up the PC."
    #:   2 multichoice SOMEONE'S PC / <PLAYER>'s PC / LOG OFF  -> option 0
    #:   3 "Accessed someone's PC."
    #:   4 "POKEMON Storage System opened."
    #:   5 the menu itself, WITHDRAW highlighted
    #: Five.
    #:
    #: This was 6, and 6 only worked by accident. With a FULL party the extra
    #: press selected WITHDRAW and got "Your party is full!", which bounced
    #: straight back to the menu -- so the overshoot was invisible. The moment
    #: a deposit freed a slot, that same press opened the box grid and picked
    #: a mon, and the next caller found itself two menus deep with a cursor
    #: that would not move.
    OPEN_PRESSES = 5

    def _ensure_overworld(self) -> None:
        """Back out of any UI so a press count means what it says."""
        d = self.d
        for _ in range(12):
            if not d.scene_active():
                break
            d.emu.run_sequence("B:6 .:60")
            d.settle(300)
        d.advance_scene(40_000)

    def open(self, max_presses: int = OPEN_PRESSES) -> bool:
        """Stand at the PC and get as far as the storage main menu.

        Stops AT the menu. Anything further is `_enter`'s job, because how
        far a press gets depends on state the caller knows and this does not.

        ALWAYS RETURNS TO THE OVERWORLD FIRST. A completed deposit leaves the
        storage UI OPEN on the box view, so calling this again fired five more
        A presses straight INTO the box grid -- which grabbed and deposited
        whatever the cursor was sitting on. Measured cost: a party of six
        became a party of three, and the two it swallowed were the run's
        level-100 lead and the LOMBRE this errand existed to evolve.
        
        That is the re-arming-menu hazard again (Crystal gotcha 18): any menu
        that survives its own confirmation turns a fixed press count into a
        repeat-action loop. `deposit` and `withdraw` are now self-contained --
        each opens, acts and closes -- so no caller has to know.
        """
        self._ensure_overworld()
        d = self.d
        cells = self.pc_cells()
        if not cells:
            return self._fail(f"no PC tile on {d.map_name()}")
        x, y = cells[0]
        # Stand below and face up. A PC is a solid counter, so the player
        # never stands ON it.
        if d.pos() != (x, y + 1) and not d.goto(x, y + 1):
            return self._fail(
                f"could not reach the cell below the PC at {(x, y)}: "
                f"{getattr(d, 'last_goto_reason', '?')}")
        d.emu.run_sequence("U:8 .:30")
        d.settle(120)
        for _ in range(max_presses):
            d.emu.run_sequence("A:8 .:240")
            d.settle(1200)
        return True

    def _enter(self, row: int) -> bool:
        """Choose a storage main-menu row and confirm the mode really opened.

        `row` indexes WITHDRAW/DEPOSIT/MOVE/SEE YA; the menu starts on
        WITHDRAW, so the move is `row` presses of DOWN.

        Entry is confirmed by RESPONSIVENESS, not by the area byte. Checking
        `area == 0` for withdraw is a vacuous test: 0 is also the value the
        byte holds when nothing is open, so the guard passed while the grid
        was not up and the failure surfaced later as "cursor stuck at 0". A
        cursor that answers the D-pad is up; one that ignores it is not.
        """
        d = self.d
        for _ in range(row):
            d.emu.run_sequence("DOWN:6 .:40")
            d.settle(200)
        d.emu.run_sequence("A:8 .:240")
        d.settle(1200)
        area, pos = self.cursor()
        if row == MENU_DEPOSIT and area == AREA_PARTY:
            return True                     # unambiguous: 1 is never a default
        for key, back in (("RIGHT", "LEFT"), ("DOWN", "UP")):
            d.emu.run_sequence(f"{key}:6 .:40")
            d.settle(150)
            _a, moved = self.cursor()
            if moved != pos:
                d.emu.run_sequence(f"{back}:6 .:40")
                d.settle(150)
                return True
        return self._fail(
            f"selected menu row {row} but the cursor does not answer the "
            f"D-pad (area={area} pos={pos}) -- the mode did not open")

    # ---- moving the cursor ---------------------------------------------

    #: Frames to wait after a D-pad press before the cursor byte is worth
    #: reading. 150 was NOT enough: the read came back with the old index, the
    #: mover concluded the axis was exhausted, tried the other one, read stale
    #: again and reported "cursor stuck at 0" on a grid that was working
    #: perfectly. Measured good at 400.
    CURSOR_SETTLE = 400

    #: Box grid is 6 wide (boxes[14][30] laid out 6x5), so DOWN is +6 and
    #: RIGHT is +1. Measured, not assumed:
    #:   RIGHT (0,0)->(0,1)  RIGHT ->(0,2)  DOWN ->(0,8)  DOWN ->(0,14)
    #:   LEFT  ->(0,13)      UP    ->(0,7)
    GRID_WIDTH = 6

    def _step_cursor(self, key: str) -> int | None:
        self.d.emu.run_sequence(f"{key}:8 .:90")
        self.d.settle(self.CURSOR_SETTLE)
        return self.cursor()[1]

    def _move_to(self, target: int, max_steps: int = 40) -> bool:
        """Walk the cursor to `target` using each area's REAL geometry.

        The old docstring claimed "the party view is a single column (RIGHT
        and DOWN both step by one there)". That is false, and it is why every
        deposit on this save was a silent no-op. `sub_809C664`
        (pret/src/pokemon_storage_system_4.c:1958-2016) is the party-column
        handler and its LEFT/RIGHT do not step at all:

            DPAD_UP    : if (--pos < 0) pos = 6            // wraps
            DPAD_DOWN  : if (++pos > 6) pos = 0            // wraps
            DPAD_LEFT  : if (pos != 0) pos = 0             // JUMP to the top
            DPAD_RIGHT : if (pos == 0) restore saved
                         else { retVal = 6; cursorArea = 0; }  // LEAVES!

        So a RIGHT press anywhere below the first slot abandons the party
        column for the box grid -- and `_move_to` used RIGHT as its primary
        key here. The cursor left the party, the loop kept pressing, and the
        deposit's confirm landed on a box cell instead of a party member.
        That is one root cause for BOTH "deposit did not land" (every index,
        party identical before and after) and `give_to_mon` equipping the
        neighbouring mon.

        In the party area, therefore: UP/DOWN only. They wrap, so there is no
        edge to stall on and the shorter direction is always available. The
        box grid keeps its 6-wide arithmetic, where RIGHT/LEFT are genuine
        single steps.
        """
        for _ in range(max_steps):
            area, pos = self.cursor()
            if pos is None:
                return self._fail("cursor is unreadable")
            if pos == target:
                return True
            if area == AREA_PARTY:
                # 7 positions (6 slots + CANCEL) on a wrapping ring: go the
                # short way round and never touch LEFT/RIGHT.
                span = 7
                forward = (target - pos) % span
                key = "DOWN" if forward <= span - forward else "UP"
                if self._step_cursor(key) == pos:
                    return self._fail(
                        f"party cursor stuck at {pos} trying to reach "
                        f"{target} (pressed {key})")
                continue
            delta = target - pos
            wide = abs(delta) >= self.GRID_WIDTH
            if wide:
                key = "DOWN" if delta > 0 else "UP"
            else:
                key = "RIGHT" if delta > 0 else "LEFT"
            after = self._step_cursor(key)
            if after == pos:
                # Edge of the grid: the other axis is the only way on.
                alt = ("RIGHT" if delta > 0 else "LEFT") if wide else \
                      ("DOWN" if delta > 0 else "UP")
                if self._step_cursor(alt) == pos:
                    return self._fail(
                        f"cursor stuck at {pos} trying to reach {target} "
                        f"(area {area}, tried {key} then {alt})")
        return self._fail(f"cursor never reached {target}")

    # ---- the two operations --------------------------------------------

    def deposit(self, index: int) -> bool:
        """Send party member `index` to a box, verified against the party.

        Refuses the last mon: a party of one cannot be emptied, and the
        engine's own refusal ("That's your last POKeMON!") would leave the UI
        in a state this driver would then have to unpick.
        """
        d = self.d
        before = self.party_names()
        if index >= len(before):
            return self._fail(
                f"party slot {index} is empty (party has {len(before)})")
        if len(before) <= 1:
            return self._fail("refusing to deposit the last party member")
        name = before[index]
        if not self.open():
            return False
        if not self._enter(MENU_DEPOSIT):
            self.close()
            return False
        if not self._move_to(index):
            self.close()
            return False
        # THREE STEPS, NOT ONE. Screenshotted: A opens a per-mon menu
        # (DEPOSIT / SUMMARY / MARK / RELEASE / CANCEL, DEPOSIT first), the
        # next A says "<NAME> is selected." and raises a BOX PICKER --
        # "Deposit in which BOX?" -- and only choosing a box with room
        # completes it. The first working attempt failed silently here
        # because it landed on BOX1 at 30/30.
        target_box = self.first_free_box()
        if target_box is None:
            return self._fail("every one of the 14 boxes is full")
        d.emu.run_sequence("A:8 .:240")          # open the mon menu
        d.settle(1200)
        d.emu.run_sequence("A:8 .:240")          # DEPOSIT -> box picker
        d.settle(1200)
        # THE PICKER DOES NOT OPEN ON `currentBox`. Screenshotted: with
        # `gPokemonStorage.currentBox == 2` the picker still came up on
        # "BOX1 30/30" and confirming it answered "The BOX is full." The old
        # arithmetic was `(first_free - current_box) % 14`, which evaluated to
        # ZERO presses and confirmed the full box every time -- the silent
        # "deposit did not land" that cost hours and four wrong theories.
        #
        # So do not compute a press count from state that does not describe
        # this widget. PAGE AND CONFIRM UNTIL THE PARTY ACTUALLY SHRINKS:
        # the outcome is the only thing here worth trusting.
        landed = False
        for _ in range(BOX_COUNT):
            d.emu.run_sequence("A:8 .:300")      # confirm the shown box
            d.settle(1500)
            if len(self.party_names()) < len(before):
                landed = True
                break
            # "The BOX is full." -- clear it and step to the next box.
            d.emu.run_sequence("B:6 .:90")
            d.settle(400)
            d.emu.run_sequence("RIGHT:6 .:90")
            d.settle(400)
        if not landed:
            log.info("[storage] every box refused the deposit")
        self.close()
        after = self.party_names()
        # COUNT the name, do not test membership. Two party members can share
        # a nickname -- every mon this run caught through the naming prompt is
        # called "A" -- so `name not in after` reports failure on a perfectly
        # good deposit whenever a namesake stays behind. The length check is
        # the real signal; the count makes the name check correct too.
        if (len(after) == len(before) - 1
                and after.count(name) == before.count(name) - 1):
            log.info("[storage] deposited %s (party %d -> %d)",
                     name, len(before), len(after))
            return True
        if len(after) < len(before) - 1:
            raise RuntimeError(
                f"storage LOST party members: asked to deposit {name} only, "
                f"party went {before} -> {after}. Refusing to continue so the "
                f"state is not saved over."
            )
        return self._fail(
            f"deposit of {name} did not land: party was {before}, now {after}")

    def withdraw(self, box: int, slot: int) -> bool:
        """Bring `boxes[box][slot]` into the party, verified against the party.

        Needs a free party slot; WITHDRAW with six answers "Your party is
        full!" and refuses.
        """
        d = self.d
        before = self.party_names()
        if len(before) >= 6:
            return self._fail(
                "party is full -- deposit something before withdrawing")
        if not self.open():
            return False
        if not self._enter(MENU_WITHDRAW):
            self.close()
            return False
        # Boxes are switched with L/R, and currentBox is readable, so this is
        # the same press-and-verify loop as the cursor.
        # PAGING BOXES IS A D-PAD ACTION FROM THE TITLE ROW, NOT L/R.
        #
        # This pressed R from wherever the cursor happened to be and gave up
        # on the first no-op: "box stuck on 0 trying to reach 2", which locked
        # the run out of boxes 1-13 -- where nearly every evolution target and
        # every boxed fighter lives. Two separate things were wrong, and the
        # ROM says both:
        #
        #   * L/R only page at all when the game's BUTTON MODE option is LR
        #     (`gSaveBlock2.optionsButtonMode == OPTIONS_BUTTON_MODE_LR`,
        #     pokemon_storage_system_4.c:1939-1945 and :2111-2117). It is not
        #     LR by default, so L and R did literally nothing.
        #   * The always-available control is DPAD LEFT/RIGHT, and only while
        #     the cursor is on the BOX TITLE (`sub_809C85C`, :2107-2109).
        #
        # `currentBox` is also not written on the press: cases 9/10 stage the
        # target in `unk_08b2` and commit at `case 2` only after the scroll
        # animation finishes (pokemon_storage_system_2.c:491-505, :570-573).
        # So poll for the value to CHANGE rather than reading it once.
        for _ in range(BOX_COUNT * 2):
            here = self.current_box()
            if here == box:
                break
            # Get onto the title row; UP from the grid's top row lands there.
            for _ in range(6):
                if self._cursor_area() == AREA_TITLE:
                    break
                d.emu.run_sequence("UP:6 .:20")
            if self._cursor_area() != AREA_TITLE:
                self.close()
                return self._fail(
                    f"could not reach the box title row (area "
                    f"{self._cursor_area()}) to page from {here} to {box}")
            # HELD, not tapped: the handler tests JOY_HELD.
            d.emu.run_sequence("RIGHT:12 .:40")
            moved = False
            for _ in range(12):
                d.settle(200)
                if self.current_box() != here:
                    moved = True
                    break
            if not moved:
                self.close()
                return self._fail(
                    f"box stuck on {here} trying to reach {box}")
        # Back into the grid before anything selects a slot.
        for _ in range(4):
            if self._cursor_area() == AREA_BOX:
                break
            d.emu.run_sequence("DOWN:6 .:20")
        if not self._move_to(slot):
            self.close()
            return False
        d.emu.run_sequence("A:8 .:240")          # open the mon menu
        d.settle(1200)
        d.emu.run_sequence("A:8 .:240")          # WITHDRAW
        d.settle(1200)
        # VERIFY AFTER CLOSING. A withdraw shows up in the box immediately
        # (screenshotted: slot 25 empties and the info panel blanks) but the
        # party sidebar is not committed to gPlayerParty until the UI is
        # dismissed. Checking too early read "box 30 -> 29, party still 5",
        # which looks exactly like a Pokemon lost in transit and is not.
        self.close()
        after = self.party_names()
        if len(after) == len(before) + 1:
            log.info("[storage] withdrew box %d slot %d -> %s (party %d -> %d)",
                     box, slot, [n for n in after if n not in before],
                     len(before), len(after))
            return True
        if len(after) < len(before):
            raise RuntimeError(
                f"storage LOST party members during a WITHDRAW: party went "
                f"{before} -> {after}. Refusing to continue so the state is "
                f"not saved over."
            )
        return self._fail(
            f"withdraw of box {box} slot {slot} did not land: party was "
            f"{before}, now {after}")

    def close(self) -> None:
        """Back out to the overworld. An open menu eats all movement input."""
        d = self.d
        for _ in range(10):
            if not d.scene_active():
                break
            d.emu.run_sequence("B:6 .:60")
            d.settle(300)
        d.advance_scene(40_000)
