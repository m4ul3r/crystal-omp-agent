"""Who goes out first, and why it matters more than it looks.

Gen 3 splits experience between every Pokemon that PARTICIPATED. The mon in
party slot 0 is sent out at the start of every wild encounter, so it
participates whether you wanted it to or not -- and the training policy's
mid-battle switch happens one turn too late to prevent that.

Measured on a real run: the policy issued `("switch", 2)` on turn 0 of 1416
consecutive battles to put the level-16 laggard in, and logged "LOTTAD is the
laggard AND the sole participant, so it keeps the full exp". It was not the
sole participant. The level-27 lead had already been sent out and took its half
of all 1416 encounters, which is exactly why it was eleven levels clear of the
team it was supposedly helping.

The fix is to change slot 0 itself, out of battle, so the trainee starts the
fight. That is a permanent reorder rather than a per-battle switch, so it costs
a few menu presses per training target instead of a wasted turn per encounter.

Verified the only way that means anything: by reading the party array back and
checking the order actually changed.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: START menu index of POKéMON. The menu is built in src/start_menu.c and its
#: contents depend on progress -- POKéDEX only appears once you own it -- so
#: this is resolved by TRYING rather than trusted: `_open_party` checks that a
#: party task actually appeared and backs out if it did not.
CANDIDATE_SLOTS = (1, 0, 2)

#: Tasks that mean the overworld party screen owns input.
PARTY_TASKS = ("HandleDefaultPartyMenu", "Task_PartyMenu", "HandlePartyMenu")


class PartyOrder:
    """Reorders the party through the overworld POKéMON menu."""

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.state = driver.state
        self.last_reason = ""

    # ---- reading ---------------------------------------------------------

    def order(self) -> list[str]:
        try:
            return [
                str(m.nickname or "?") for m in self.state.party()
            ]
        except Exception:  # noqa: BLE001
            return []

    def index_of(self, who) -> int | None:
        """Party index of a nickname, or None."""
        want = str(who).upper()
        for i, m in enumerate(self.state.party()):
            if str(m.nickname or "").upper() == want:
                return i
        return None

    def at_party_screen(self) -> bool:
        try:
            tasks = self.state.tasks()
        except Exception:  # noqa: BLE001
            return False
        return any(t.startswith(PARTY_TASKS) for t in tasks)

    # ---- driving ---------------------------------------------------------

    def _fail(self, reason: str) -> bool:
        self.last_reason = reason
        log.info("[party] %s", reason)
        return False

    def _open_party(self) -> bool:
        """START, then the POKéMON entry, confirmed by a party task appearing.

        The entry's index moves with progress, so each candidate is tried and
        backed out of. Pressing A blindly on the first entry opened the
        POKEDEX, which then ate every further press.
        """
        from .menus import Menus

        if self.at_party_screen():
            return True
        menus = Menus(self.emu, self.state)
        for slot in CANDIDATE_SLOTS:
            self.emu.run_sequence("START:4 .:30")
            if not menus.select_index(slot):
                self.emu.run_sequence("B:4 .:20")
                continue
            for _ in range(6):
                if self.at_party_screen():
                    return True
                self.emu.tick(20)
            # Wrong entry: shut whatever opened and try the next one.
            for _ in range(6):
                self.emu.run_sequence("B:4 .:24")
                if not self.d.scene_active():
                    break
        return self._fail("could not open the party screen")

    def _close(self) -> None:
        """Get control back, whatever screen we ended on.

        B alone was not enough: pressing A on a party member can land on the
        SUMMARY page rather than the SWITCH submenu, and ten B presses left it
        open with sLockFieldControls still set. A run froze on Route 116 for
        fifteen minutes that way. advance_scene knows how to back out of a
        full-screen menu, so it is the backstop rather than a second
        hand-rolled loop.
        """
        for _ in range(10):
            if not self.d.scene_active() and not self.at_party_screen():
                return
            self.emu.run_sequence("B:4 .:24")
        if self.d.scene_active() or self.at_party_screen():
            log.warning("[party] still on a menu after ten B presses; "
                        "handing it to advance_scene")
            self.d.advance_scene(40000)

    def lead_with(self, who) -> bool:
        """Put `who` (nickname) in slot 0. Verified against the party array.

        The cursor IS readable, and the previous claim here that it was not
        cost the run its whole team rotation. It lives on the cursor sprite:
        `sub_806CA00(taskId)` is `gTasks[taskId].data[3] >> 8` and the slot is
        that sprite's `data[0]` (src/party_menu.c:1773-1776, and the engine
        reads it exactly this way at :1228 and :1320). The harness already had
        the reader for the BATTLE party menu; the overworld screen simply runs
        a different task handler, `HandleDefaultPartyMenu`, which was not in
        the list of names it would accept.

        Because it was believed unreadable, the press count was treated as
        unknown and SEARCHED -- press DOWN n times for n in a small range and
        check whether the party array changed. With six mons the search
        exhausted before reaching the last slot, so MIGHTYENA never became
        lead, the loop recorded a permanent failure for it, and ROCKY led every
        battle until its damaging PP was gone. That zombie lead is what
        prompted "we still keep trying to call PROTECT when there's no PP
        left": the same mon was always in front, so the same PP always ran out.

        Now the cursor is driven by reading it, one verified press at a time,
        like every other menu here. A no-op when it already leads.
        """
        self.last_reason = ""
        target = self.index_of(who)
        if target is None:
            return self._fail(f"{who} is not in the party")
        if target == 0:
            self.last_reason = f"{who} already leads"
            return True

        from .menus import Menus

        before = list(self.order())
        if not self._open_party():
            return False
        self.emu.tick(60)                     # let the list finish drawing
        if not self._drive_cursor(target):
            self._close()
            return self._fail(
                f"could not put the party cursor on {who} (slot {target}): "
                f"{self.last_reason or 'cursor unreadable'}"
            )
        self.emu.run_sequence("A:4 .:36")

        menus = Menus(self.emu, self.state)
        if not self._choose_switch(menus):
            self._close()
            return self._fail(
                f"the party popup would not open SWITCH for {who}: "
                f"{self.last_reason}"
            )
        self.emu.tick(40)
        if not self._drive_cursor(0):
            self._close()
            return self._fail(
                f"could not bring the cursor back to slot 0 to swap {who}"
            )
        self.emu.run_sequence("A:4 .:48")
        self.d.settle(90)
        self._close()

        after = list(self.order())
        if after == before:
            return self._fail(
                f"the switch was confirmed but the party did not move "
                f"({before})"
            )
        if self.index_of(who) == 0:
            log.info("[party] %s leads now, %s -> %s", who, before, after)
            return True
        log.warning("[party] swap moved the wrong mon (%s -> %s); reverting",
                    before, after)
        self._swap_back(before)
        return self._fail(
            f"a switch swapped the wrong pair trying to lead with {who}"
        )

    #: SWITCH mode is confirmed by the engine's own task, not by a row number.
    SWITCH_TASK = "HandlePartyMenuSwitchPokemonInput"

    def _choose_switch(self, menus) -> bool:
        """Pick SWITCH out of the party popup, verified by the engine.

        The row is NOT a constant. `ShowPartyPopupMenu` builds the list from
        the mon's own context (src/party_menu.c:2847-2856) and hands `InitMenu`
        a row count that varies -- measured five rows with SWITCH at index 2,
        while the code here had 1 hard-coded and opened the SUMMARY screen
        instead. Worse, `gMenu.cursorPos` is STALE when the popup appears (read
        1 with bounds 0..7 left over from the previous menu), so a select that
        "verified" its cursor was verifying the wrong menu's.

        So each row is tried and the outcome is CHECKED: SWITCH is the one that
        starts `HandlePartyMenuSwitchPokemonInput`. Anything else is backed out
        of. Bounded by the menu's own row count, and every branch is
        recoverable -- SUMMARY needs a B, CANCEL closes the popup and is
        reopened.
        """
        lo, hi = menus.bounds()
        for idx in range(lo, hi + 1):
            if not menus.select_index(idx):
                continue
            self.emu.tick(30)
            tasks = self.state.tasks()
            if self.SWITCH_TASK in tasks:
                return True
            # Wrong row. Back all the way out to the party list and reopen the
            # popup on the same mon, whose cursor slot we have not moved.
            for _ in range(4):
                if self.at_party_screen() and self.SWITCH_TASK not in tasks:
                    break
                self.emu.run_sequence("B:4 .:20")
                tasks = self.state.tasks()
            if not self.at_party_screen():
                self.last_reason = (
                    f"row {idx} left the party screen and B did not return"
                )
                return False
            self.emu.run_sequence("A:4 .:36")
        self.last_reason = f"no popup row {lo}..{hi} started a switch"
        return False

    def _drive_cursor(self, target, max_steps=14) -> bool:
        """Walk the party cursor to `target`, verifying every press.

        The list swallows its first input while it draws, so a press that does
        not move the cursor is retried once rather than treated as a refusal --
        but only once, because mashing a genuinely stuck menu is how the
        predecessor spent 90k frames on a single turn.
        """
        stuck = 0
        for _ in range(max_steps):
            cur = self.d.battle._party_cursor()
            if cur is None:
                self.last_reason = "the party cursor sprite is not readable"
                return False
            if cur == target:
                return True
            self.emu.run_sequence(
                "DOWN:4 .:18" if target > cur else "UP:4 .:18")
            if self.d.battle._party_cursor() == cur:
                stuck += 1
                if stuck > 1:
                    self.last_reason = (
                        f"the cursor would not leave slot {cur} "
                        f"(wanted {target})"
                    )
                    return False
            else:
                stuck = 0
        self.last_reason = f"the cursor never reached slot {target}"
        return False

    def _swap_back(self, wanted):
        """Best-effort restore of a known-good order after a bad swap."""
        for _ in range(4):
            if self.order() == wanted:
                return True
            first = self.order()
            try:
                bad = next(i for i, (a, b) in enumerate(zip(first, wanted))
                           if a != b)
            except StopIteration:
                return True
            who = wanted[bad]
            here = self.index_of(who)
            if here is None or here == bad:
                return False
            if not self._raw_swap(here, bad):
                return False
        return self.order() == wanted

    def _raw_swap(self, a, b) -> bool:
        """Swap two slots by press count, without the outcome search."""
        from .menus import Menus

        if not self._open_party():
            return False
        self.emu.tick(60)
        for _ in range(a):
            self.emu.run_sequence("DOWN:4 .:16")
        self.emu.run_sequence("A:4 .:36")
        if not Menus(self.emu, self.state).select_index(1):
            self._close()
            return False
        self.emu.tick(40)
        step = "UP" if b < a else "DOWN"
        for _ in range(abs(a - b)):
            self.emu.run_sequence(f"{step}:4 .:16")
        self.emu.run_sequence("A:4 .:48")
        self.d.settle(90)
        self._close()
        return True

    def _move_party_cursor(self, index, tries=10) -> bool:
        """The overworld party cursor lives in gLastFieldPokeMenuOpened.

        Driven one press at a time and re-read, like every other cursor in
        this harness: the party screen ignores input while it draws, so a
        computed press count silently lands somewhere else.
        """
        for _ in range(tries):
            here = self._cursor()
            if here is None:
                return False
            if here == index:
                return True
            self.emu.run_sequence(
                "DOWN:4 .:12" if here < index else "UP:4 .:12"
            )
        return self._cursor() == index

    def _cursor(self) -> int | None:
        try:
            return int(self.emu.u8("gLastFieldPokeMenuOpened"))
        except Exception:  # noqa: BLE001
            return None
