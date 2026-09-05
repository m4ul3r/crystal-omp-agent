"""Teaching a TM or HM, from the overworld bag.

Two HMs sat in the bag for three badges -- CUT collected in Rustboro, ROCK
SMASH collected in Mauville -- and `field_moves()` reported all-None the whole
time, because nothing here could teach them. That is the Crystal harness's
gotcha 16 repeating in a new generation: "the machine is in the bag" is not
"someone can use it", and the difference was a road north that stayed shut.

The flow is START -> BAG -> the TMs&HMs pocket -> the machine -> USE -> a party
member, and every step of it is verified against memory rather than assumed
from a screen:

* the pocket is identified by WHICH pocket's cursor responds to a d-pad press,
  because `gCurrentBagPocketItemSlots` lags the display and reported the wrong
  pocket during development;
* the machine under the cursor is read out of the pocket's own item slots and
  checked before A is pressed, so a mis-positioned cursor refuses instead of
  teaching the wrong move to the wrong mon;
* success is the target's MOVESET containing the move afterwards. Nothing else
  counts -- not a dialog, not a bag count, since a machine is not consumed.

Refusals happen BEFORE any button is pressed, the way `teach_tm` does in the
predecessor: an unknown machine, one that is not in the bag, a species that
cannot learn it, or a mon that already knows it are all answered from data.
"""

from __future__ import annotations

import logging
import struct

log = logging.getLogger("pokeagent.teach")

#: `struct ItemSlot { u16 itemId; u16 quantity; }` (include/global.h) and
#: `struct BagPocket { struct ItemSlot *itemSlots; u8 capacity; }`
#: (include/item.h:16-20), padded to 8 bytes on the GBA. Sizes are taken from
#: the linker map where possible rather than counted off the header -- reading
#: a stride out of a C declaration has cost this project three separate bugs.
SLOT_SIZE = 4
POCKET_STRIDE = 8

#: `enum { POCKET_ITEMS = 1, ... }` is 1-based, but `gBagPockets` is a plain
#: array, so TMs&HMs is index 2 and general ITEMS is index 0.
TMHM_POCKET = 2
ITEMS_POCKET = 0
#: `POCKET_KEY_ITEMS = 5` in the same 1-based enum (include/item.h:11), so the
#: plain array index is 4. The bike lives here, and it is the only pocket whose
#: USE acts on the WORLD rather than on a Pokemon -- no party list follows.
KEY_ITEMS_POCKET = 4

#: `struct PocketScrollState { u8 cursorPos, scrollTop, numSlots, cursorMax; }`
#: (include/item_menu.h:15-21). FOUR bytes, not two -- assuming two put the
#: cursor arithmetic on the POKE BALLS pocket while the code believed it was
#: reading TMs&HMs, and the selected item came back as a Great Ball.
SCROLL_STRIDE = 4


class Teacher:
    """Teach machines to party members, refusing what cannot work."""

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.names = driver.names
        self.consts = driver.consts
        self.last_reason: str | None = None
        #: Who ended up with the move, which is not always who was asked for.
        self.taught_to: str | None = None
        #: What was given up to make room, when anything was.
        self.forgot: str | None = None
        self._before_moves: list = []

    def _fail(self, why: str) -> bool:
        self.last_reason = why
        log.info("teach: %s", why)
        return False

    # ---- data, no buttons -------------------------------------------------

    def machine_move(self, item_id: int) -> tuple[int, str] | None:
        """The move a machine teaches, from the ROM's own `TMHMMoves`."""
        first = self.consts.items["ITEM_TM01_FOCUS_PUNCH"]
        slot = item_id - first
        count = (self.emu.sym.size("TMHMMoves") or 0) // 2
        if not 0 <= slot < count:
            return None
        move_id = self.emu.u16(("TMHMMoves", slot * 2))
        return move_id, self.names.move(move_id)

    def pocket_items(self, pocket: int = TMHM_POCKET) -> list[tuple[int, int, int]]:
        """`[(slot_index, item_id, quantity)]` for one bag pocket."""
        base = self.emu.resolve("gBagPockets")
        raw = bytes(self.emu.read(base + pocket * POCKET_STRIDE, POCKET_STRIDE))
        ptr, capacity = struct.unpack_from("<IB3x", raw, 0)
        if not ptr or not capacity:
            return []
        slots = bytes(self.emu.read(ptr, capacity * SLOT_SIZE))
        out = []
        for i in range(capacity):
            item_id, qty = struct.unpack_from("<HH", slots, i * SLOT_SIZE)
            if item_id:
                out.append((i, item_id, qty))
        return out

    def machine_slot(self, item_id: int) -> int | None:
        """Where a machine sits in the TMs&HMs pocket, by LIST position.

        The list is compacted -- the engine keeps used slots contiguous -- so
        the cursor index is the position among present items, not the raw
        slot number.
        """
        for position, (_slot, present, _qty) in enumerate(self.pocket_items()):
            if present == item_id:
                return position
        return None

    def candidates(self, item_id: int) -> list:
        """Party members that could learn this machine and do not know it."""
        move = self.machine_move(item_id)
        if move is None:
            return []
        move_id, _name = move
        out = []
        for index, mon in enumerate(self.d.state.party()):
            if mon.is_egg or not mon.level:
                continue
            if move_id in mon.moves:
                continue
            if not self.names.learns_tm(mon.species, item_id):
                continue
            out.append((index, mon))
        # Rank by how expendable the mon's SLOT 0 move is, because slot 0 is
        # what the engine takes. Driving the "which move to forget" list is not
        # possible from here -- it is drawn with sprites like the party list,
        # its cursor does not respond to counted presses, and every attempt
        # took the first move regardless. Fighting that cost a BLAZIKEN its
        # BLAZE KICK. Choosing a mon whose first move is a status move gets the
        # same road opened at no cost.
        out.sort(key=lambda pair: self._slot0_cost(pair[1]))
        return out

    #: Moves whose LISTED power is unreachable without setup this run never
    #: does. SPIT UP reads 100 in the ROM's table and deals literally nothing
    #: without Stockpile -- measured, ten consecutive turns of a level-100
    #: PELIPPER hitting a WALREIN for 0 while the Elite Four ran the clock
    #: out. Pricing it at 100 made the teacher protect the single most useless
    #: move in the party and refuse every TM.
    CONDITIONAL_POWER = frozenset({"SPIT UP", "SWALLOW", "DREAM EATER",
                                   "FOCUS PUNCH", "SNORE", "FAKE OUT"})

    def effective_power(self, move_id) -> int:
        """Base power, except for moves that cannot deliver it unaided."""
        try:
            name = self.names.move(move_id)
        except Exception:  # noqa: BLE001
            name = ""
        if name and name.upper() in self.CONDITIONAL_POWER:
            return 0
        try:
            return self.names.move_data(move_id).power
        except Exception:  # noqa: BLE001
            return 0

    def _slot0_cost(self, mon) -> tuple:
        """How much it hurts to overwrite this mon's first move.

        A free slot costs nothing, a status move almost nothing, and a strong
        damaging move a great deal. Sorted ascending, so the cheapest mon to
        teach comes first.
        """
        moves = list(mon.moves)
        if len(moves) < 4 or not moves[0] or not all(moves[:4]):
            return (0, 0)                      # a free slot: nothing is lost
        return (1, self.effective_power(moves[0]))

    def hm_move_ids(self) -> frozenset:
        """Move ids that come from an HM, so they are never traded away.

        Derived from the machines themselves rather than a hardcoded list of
        names: every ITEM_HM* in the constants, mapped through the same
        `machine_move` the teach path uses.
        """
        out = set()
        for const, value in self.consts.items.items():
            if "_HM" not in const:
                continue
            got = self.machine_move(value)
            if got:
                out.add(got[0])
        return frozenset(out)

    def forced_loss(self, mon):
        """The move that would be given up teaching this mon, or None.

        This used to answer `moves[0]` on the theory that "the engine always
        takes slot 0" -- but Gen 3 ASKS which move to forget, and
        `_settle_learn` already answers that with a policy which protects HM
        moves and prefers dropping the weakest thing. So the refusal was
        pricing a sacrifice that would never happen.

        It cost the run FLY. SEA BIRD the PELIPPER is the party's only Fly
        learner and its slot 0 is SURF (95 power), so teaching was refused as
        "would overwrite SURF" -- while slot 2 held SWALLOW, power 0, useless
        without Stockpile. Badge 6 was already won and the rods, the Pokeblock
        Case and the whole Safari Zone were waiting on a hop.

        Now it names what the prompt would actually drop: the weakest move
        that is not an HM.
        """
        moves = [m for m in list(mon.moves)[:4] if m]
        if len(moves) < 4:
            return None
        protected = self.hm_move_ids()

        def power(mid):
            # EFFECTIVE, not listed: a move that cannot deliver its power
            # unaided is the cheapest thing in the moveset, not the dearest.
            return self.effective_power(mid)

        keepable = [m for m in moves if m not in protected]
        # Nothing but HMs: the sacrifice really is an HM, so say so and let the
        # caller refuse on price.
        pool = keepable or moves
        return self.names.move(min(pool, key=power))

    def knows(self, item_id: int):
        """The first party member that already knows this machine's move."""
        move = self.machine_move(item_id)
        if move is None:
            return None
        move_id, _ = move
        for mon in self.d.state.party():
            if move_id in mon.moves:
                return mon
        return None

    # ---- the bag ----------------------------------------------------------

    def _scroll_states(self) -> list[int]:
        return list(bytes(self.emu.read("gBagPocketScrollStates", 5 * SCROLL_STRIDE)))

    def _pocket_cursor(self, pocket: int = TMHM_POCKET) -> tuple[int, int, int]:
        """`(cursorPos, scrollTop, numSlots)` for one pocket."""
        s = self._scroll_states()
        base = pocket * SCROLL_STRIDE
        return s[base], s[base + 1], s[base + 2]

    def _displayed_pocket(self) -> int:
        """Which pocket the bag is showing.

        `sCurrentBagPocket` is the engine's own answer (item_menu.c:446 indexes
        the scroll states with it), so it is read rather than inferred. An
        earlier version nudged the cursor and watched which scroll state
        moved, which was wrong twice over: it mutates the thing it measures,
        and with the stride mis-read as two bytes it named the wrong pocket
        with total confidence.
        """
        return self.emu.u8("sCurrentBagPocket")

    def _open_bag(self) -> bool:
        """START -> BAG, using the engine's own menu order.

        `sCurrentStartMenuActions` is the list the engine BUILT for this save
        (start_menu.c:258-260), so BAG's index is read rather than assumed --
        the menu loses entries before the Pokedex and PokeNav are received.
        """
        from pokeagent.menus import Menus

        menus = Menus(self.emu, self.d.state)
        self.emu.run_sequence("START:4 .:24")
        count = self.emu.u8("sNumStartMenuActions")
        actions = list(bytes(self.emu.read("sCurrentStartMenuActions", 10)))[:count]
        bag = self.consts.start_menu_bag if hasattr(self.consts, "start_menu_bag") else 2
        if bag not in actions:
            return self._fail("the START menu has no BAG entry")
        if not menus.select_index(actions.index(bag)):
            return self._fail(f"could not reach BAG: {menus.last_reason}")
        self.emu.run_sequence(".:40")
        return True

    def _reach_tm_pocket(self, tries: int = 5) -> bool:
        return self._reach_pocket(TMHM_POCKET, tries)

    def _reach_pocket(self, pocket: int, tries: int = 6) -> bool:
        """Page the bag to `pocket`, reading the engine's own answer each time.

        LEFT as well as RIGHT: the pockets are a ring but the ITEMS pocket sits
        BEFORE TMs&HMs, so a right-only walk from a bag that opened on TMs&HMs
        took the long way round and ran out of tries.
        """
        for _ in range(tries):
            here = self._displayed_pocket()
            if here == pocket:
                return True
            self.emu.run_sequence(
                "RIGHT:4 .:24" if here < pocket else "LEFT:4 .:24"
            )
        return self._displayed_pocket() == pocket

    def _cursor_to(self, position: int, tries: int = 24,
                   pocket: int = TMHM_POCKET) -> bool:
        """Move the TM pocket's cursor to a list position.

        Cursor and scroll are separate: the visible cursor is
        `gBagPocketScrollStates[4]` and the list offset is `[5]`, so the
        selected item is the sum. Driving one without the other walks off the
        end of a long pocket.
        """
        for _ in range(tries):
            cursor, scroll, _slots = self._pocket_cursor(pocket)
            here = cursor + scroll
            if here == position:
                return True
            self.emu.run_sequence("DOWN:4 .:12" if here < position else "UP:4 .:12")
        return False

    def _selected_item(self, pocket: int = TMHM_POCKET) -> int | None:
        """The item under the cursor: `scrollTop + cursorPos` into the
        pocket, exactly as item_menu.c:446 computes it."""
        cursor, scroll, _slots = self._pocket_cursor(pocket)
        index = cursor + scroll
        items = self.pocket_items(pocket)
        return items[index][1] if 0 <= index < len(items) else None

    # ---- the whole thing --------------------------------------------------

    def teach(self, machine: str, mon=None, max_frames: int = 40_000) -> bool:
        """Teach `machine` (e.g. ``"HM06"``) to a party member.

        `mon` is a nickname or species name; omitted, the first member that
        can learn it and does not already know it is chosen. Returns True only
        when that member's moveset actually contains the move afterwards.
        """
        self.last_reason = None
        item_id = self.names.item_id(machine) if hasattr(self.names, "item_id") else None
        if item_id is None:
            item_id = self._item_id(machine)
        if not item_id:
            return self._fail(f"{machine!r} is not an item this ROM knows about")

        move = self.machine_move(item_id)
        if move is None:
            return self._fail(f"{machine} is not a TM or HM")
        move_id, move_name = move

        if self.machine_slot(item_id) is None:
            return self._fail(f"{machine} ({move_name}) is not in the bag")

        options = self.candidates(item_id)
        if mon is not None:
            wanted = str(mon).upper()
            options = [
                (i, m) for i, m in options
                if wanted in ((m.nickname or "").upper(),
                              self.names.species(m.species).upper())
            ]
            if not options:
                already = self.knows(item_id)
                if already and wanted in ((already.nickname or "").upper(),
                                          self.names.species(already.species).upper()):
                    return self._fail(f"{mon} already knows {move_name}")
                return self._fail(f"{mon} cannot learn {move_name}")
        if not options:
            already = self.knows(item_id)
            if already:
                return self._fail(
                    f"{already.nickname or self.names.species(already.species)} "
                    f"already knows {move_name}")
            return self._fail(f"no party member can learn {move_name}")

        target_index, target = options[0]
        label = target.nickname or self.names.species(target.species)

        # Refuse to trade away something good. An HM opens a road, but not at
        # the price of a 100-power STAB move -- and since the engine always
        # takes slot 0, that price is knowable BEFORE anything is pressed.
        losing = self.forced_loss(target)
        if losing is not None:
            try:
                # The cost of the move that would ACTUALLY go, not slot 0's.
                lost_id = next(
                    (m for m in target.moves
                     if m and self.names.move(m) == losing), None
                )
                cost = self.effective_power(lost_id) if lost_id else 0
            except Exception:  # noqa: BLE001
                cost = 0
            if cost > self.MAX_SACRIFICE:
                return self._fail(
                    f"teaching {move_name} to {label} would overwrite "
                    f"{losing} ({cost} power); no party member has a spare or "
                    f"cheap slot to spare")

        if not self._open_bag():
            self.d.close_menus() if hasattr(self.d, "close_menus") else None
            return False
        if not self._reach_tm_pocket():
            self._back_out()
            return self._fail("could not reach the TMs&HMs pocket")
        position = self.machine_slot(item_id)
        if position is None or not self._cursor_to(position):
            self._back_out()
            return self._fail(f"could not put the cursor on {machine}")
        if self._selected_item() != item_id:
            self._back_out()
            return self._fail(
                f"cursor is on item {self._selected_item()}, not {machine} -- "
                "refusing to press A on the wrong machine")

        if not self._on_pocket(TMHM_POCKET):
            self._back_out()
            return self._fail(
                f"the bag is showing pocket {self._displayed_pocket()}, not "
                f"TMs&HMs ({TMHM_POCKET}) -- refusing to press A"
            )
        # A opens the item's popup, and WHICH ROW that popup offers is read
        # rather than assumed (see `choose_use`). What follows is NOT the party
        # list: there is a TM/HM info screen and a "Teach CUT to a POKeMON?"
        # YES/NO in between. Pressing twice and then navigating meant the d-pad
        # went into a description screen, the selection never moved, and slot 0
        # was offered every time -- which is how a LOMBRE kept being asked to
        # learn CUT.
        self.emu.run_sequence("A:4 .:24")
        if not self.choose_use():
            self._back_out()
            return False
        if not self._wait_for_party_list():
            self._back_out()
            return self._fail("the party list never opened")
        if not self._pick_party_member(target_index):
            self._back_out()
            return self._fail(f"could not select {label} in the party menu")

        self._before_moves = list(target.moves)
        return self._settle_learn(target_index, move_id, move_name, label, max_frames)

    # ---- the popup, read rather than assumed --------------------------

    def popup_rows(self) -> list[int]:
        """The open item popup's action list, as ITEM_ACTION values.

        `sPopupMenuActionList` is the engine's own pointer to the row array it
        indexes (src/item_menu.c:1829) and `gUnknown_02038564` is the count.
        Reading both is the difference between choosing USE and choosing
        whatever happens to sit in row 0 -- which for a BALL is GIVE.
        """
        ptr = self.emu.u32("sPopupMenuActionList")
        rows = self.emu.u8("gUnknown_02038564")
        return list(self.emu.read(ptr, max(0, rows)))

    def _use_action(self) -> int:
        if getattr(self, "_action_use", None) is None:
            from pokeagent.fishing import enum_values

            self._action_use = enum_values(
                "src/item_menu.c", "ITEM_ACTION_USE_0"
            )["ITEM_ACTION_USE_0"]
        return self._action_use

    def _popup_cursor_to(self, target: int, tries: int = 12) -> bool:
        """Drive the item popup's cursor to a linear index.

        THE POPUP IS A 2x2 GRID, NOT A LIST, and its own handler says so
        (src/item_menu.c:1791-1824):

            UP    : if (sel & 1)   sel -= 1
            DOWN  : if (!(sel & 1)) sel += 1
            LEFT  : if (sel >= 2)  sel -= 2
            RIGHT : if (sel < 2)   sel += 2

        So `row = sel & 1` and `col = sel >> 1`, and a target two places down
        the ACTION LIST is one press RIGHT -- not two presses DOWN. Pressing
        DOWN at it walks to the bottom of the column and stops there forever,
        which is exactly what happened the first time anything wanted GIVE:
        `[USE, TOSS, GIVE, CANCEL]`, target row 2, cursor stuck at 1 through
        every retry. `choose_use` never noticed because USE is index 0 and the
        cursor is already on it -- the movement code had never once run.
        """
        for _ in range(tries):
            cur = self.emu.u8("sPopupMenuSelection")
            if cur == target:
                return True
            if (cur & 1) != (target & 1):
                self.emu.run_sequence("UP:4 .:14" if (cur & 1)
                                      else "DOWN:4 .:14")
                continue
            self.emu.run_sequence("LEFT:4 .:14" if cur >= 2
                                  else "RIGHT:4 .:14")
        return self.emu.u8("sPopupMenuSelection") == target

    def _give_action(self) -> int:
        """`ITEM_ACTION_GIVE` (src/item_menu.c:111), read rather than hardcoded
        for the same reason USE is."""
        if getattr(self, "_action_give", None) is None:
            from pokeagent.fishing import enum_values

            self._action_give = enum_values(
                "src/item_menu.c", "ITEM_ACTION_GIVE"
            )["ITEM_ACTION_GIVE"]
        return self._action_give

    def choose_give(self, tries=8) -> bool:
        """Pick GIVE out of the item popup, verified before pressing A.

        Same discipline as `choose_use`: read the engine's own row list and
        refuse if GIVE is not in it. A blind press here is how a GREAT BALL
        got handed to the lead and wedged the run on an unanswered "switch
        the two items?" box for fourteen minutes.
        """
        want = self._give_action()
        rows = self.popup_rows()
        if want not in rows:
            return self._fail(
                f"the item popup {rows} offers no GIVE action ({want}) -- "
                f"refusing to press A on it")
        target = rows.index(want)
        if not self._popup_cursor_to(target, tries=max(tries, 12)):
            return self._fail(
                f"could not put the popup cursor on GIVE (row {target}, "
                f"stuck at {self.emu.u8('sPopupMenuSelection')})")
        self.emu.run_sequence("A:4 .:16")
        return True

    def take_from_mon(self, index: int, tries: int = 3) -> bool:
        """Unequip party slot `index`'s held item, back into the bag.

        The missing half of `give_to_mon`, and the reason the EXP. SHARE spent
        a whole session welded to one mon: a deposited mon keeps its item and
        empties it from the bag, so nothing could ever hand it to a grind
        target.

        Three facts make it work, all measured rather than assumed:

        * `_pick_party_member` ALREADY presses A and opens the per-mon popup.
          An extra A here selects row 0 -- which for a CUT-knower is CUT, and
          the screen answered "There's nothing to CUT." while every cursor read
          looked fine. That off-by-one press is what made this look impossible.
        * The popup and its GIVE/TAKE submenu are both ordinary `InitMenu`
          menus (`ShowPartyPopupMenu` -> `InitMenu`, party_menu.c:2847-2856),
          so `Menus.select_index` drives them off `gMenu.cursorPos`. They do
          NOT draw a `>` cursor glyph, which is why `select_label` pressed A
          blindly on whatever row it started on.
        * Row order is data-driven: field moves come first, so ITEM is always
          `max - 1` and CANCEL is `max`. Read the bounds, never count rows.
          The submenu is GIVE/TAKE/CANCEL, so TAKE is index 1.

        Judged on `held_item`, never on the presses.
        """
        from pokeagent.menus import Menus

        d = self.d
        party = [m for m in d.state.party()]
        if not 0 <= index < len(party):
            return self._fail(f"party slot {index} does not exist")
        item_id = party[index].held_item
        if not item_id:
            return True                      # nothing to take
        menus = Menus(d.emu, d.state)
        # THE SUBMENU'S OWN LENGTH SAYS WHETHER THE RIGHT MON WAS PICKED.
        # A mon that holds something offers GIVE/TAKE/CANCEL (bounds 0..2); a
        # mon holding nothing offers GIVE/CANCEL (bounds 0..1), so pressing
        # index 1 there hits CANCEL and the take silently does nothing. That
        # is exactly what happened on MASQUERAIN: the picker landed on the
        # empty-handed lead and every attempt reported "still holds 182".
        # So try each party slot and only commit where TAKE actually exists.
        order = [index] + [i for i in range(len(party)) if i != index]
        for _ in range(tries):
            for slot in order:
                d.close_menus()
                d.settle(300)
                d.emu.run_sequence("START:6 .:90")
                d.settle(600)
                if not menus.select_index(1):     # POKeMON
                    continue
                d.settle(900)
                if not self._pick_party_member(slot):
                    continue
                d.settle(700)
                lo, hi = menus.bounds()
                if hi - lo < 2 or not menus.select_index(hi - 1):   # ITEM
                    d.close_menus()
                    continue
                d.settle(900)
                lo2, hi2 = menus.bounds()
                if hi2 - lo2 < 2:
                    # GIVE/CANCEL only: this mon is not holding anything.
                    d.close_menus()
                    continue
                if not menus.select_index(1):                        # TAKE
                    d.close_menus()
                    continue
                d.settle(900)
                for _ in range(3):
                    d.emu.run_sequence("A:6 .:120")
                    d.settle(500)
                d.close_menus()
                d.settle(400)
                # JUDGE ON THE ITEM LEAVING THE PARTY, NOT ON THE SLOT.
                # The picker index does not map to the party index -- RSE
                # draws slot 0 as a tall box with the rest in a two-column
                # grid -- so `_pick_party_member(1)` returned True and the
                # game acted on slot 2, answering "EMBER isn't holding
                # anything." while slot 1 kept the share. Whichever row the
                # picker really lands on, the only question that matters is
                # whether the item is now in the bag.
                if not any(m.held_item == item_id for m in d.state.party()):
                    return True
        return self._fail(
            f"slot {index} still holds item "
            f"{d.state.party()[index].held_item}"
        )

    def give_from_field(self, index: int, item: str, tries: int = 3) -> bool:
        """Make party slot `index` hold `item`, driven from the FIELD menu.

        `give_to_mon` picks its party slot inside the BAG's give flow, which is
        a different screen from the field party list and whose cursor this
        project has never read correctly -- asking for one slot lands on
        another. Live consequence: the EXP. SHARE meant for LOUDRED went to
        the level-100 PELIPPER, and a level-100 earns nothing
        (`battle_script_commands.c:3420-3424`), so four Elite Four laps in a
        row paid absolutely nobody.

        This takes the route that is proven instead -- the same one
        `take_from_mon` uses: START -> POKeMON -> the mon -> ITEM -> GIVE, and
        only THEN the bag. The submenu is GIVE/TAKE/CANCEL, so GIVE is index 0.

        Judged on `held_item`.
        """
        from pokeagent.menus import Menus

        d = self.d
        item_id = self._item_id(item)
        if not item_id:
            return self._fail(f"{item!r} is not an item this ROM knows about")
        party = d.state.party()
        if not 0 <= index < len(party):
            return self._fail(f"party slot {index} does not exist")
        if party[index].held_item == item_id:
            return True
        # CHECK THE POCKET BEFORE ANY MENU IS OPEN. `gBagPockets` is
        # re-pointed while the bag UI is up, so the same read that answers
        # [14,15,16,24,93,182] on the overworld answers "not in the ITEMS
        # pocket" once the give flow has opened it -- which failed three
        # staging runs in a row with the share sitting plainly in the bag.
        if not any(iid == item_id
                   for _s, iid, _q in self.pocket_items(ITEMS_POCKET)):
            return self._fail(f"{item!r} is not in the ITEMS pocket")
        menus = Menus(d.emu, d.state)
        for _ in range(tries):
            d.close_menus()
            d.settle(300)
            d.emu.run_sequence("START:6 .:90")
            d.settle(600)
            if not menus.select_index(1):                 # POKeMON
                continue
            d.settle(900)
            if not self._pick_party_member(index):        # opens the popup
                continue
            d.settle(700)
            lo, hi = menus.bounds()
            if hi - lo < 2 or not menus.select_index(hi - 1):   # ITEM
                d.close_menus()
                continue
            d.settle(900)
            if not menus.select_index(0):                        # GIVE
                d.close_menus()
                continue
            d.settle(900)
            # the bag opens on the ITEMS pocket; walk to the item and confirm
            if not self._reach_pocket(ITEMS_POCKET):
                d.close_menus()
                continue
            # PRESS AND VERIFY, ROW BY ROW. Every read of the bag list is
            # untrustworthy while the bag UI is open -- `gBagPockets` is
            # re-pointed, so both `pocket_items` and `_selected_item` describe
            # a list that is not the one on screen, and `_cursor_to` dragged
            # the cursor OFF the share it had already landed on. The only
            # reliable signal is the party's own `held_item`.
            #
            # So: press A, look at what the mon is holding, and if it is the
            # wrong item hand it straight back and try the next row. Bounded,
            # self-correcting, and it cannot silently give away a Master Ball.
            for _ in range(14):
                d.emu.run_sequence("A:6 .:120")
                d.settle(600)
                held = d.state.party()[index].held_item
                if held == item_id:
                    break
                if held:
                    d.close_menus()
                    d.settle(300)
                    self.take_from_mon(index)
                    break
                d.emu.run_sequence("DOWN:6 .:40")
                d.settle(250)
            d.close_menus()
            d.settle(400)
            if d.state.party()[index].held_item == item_id:
                return True
        held = d.state.party()[index].held_item
        return self._fail(
            f"slot {index} holds {held} after the give, not {item_id}"
        )

    def give_to_mon(self, item: str, mon) -> bool:
        """Make a party member HOLD `item`, proved by its held_item field.

        Written for the EXP. SHARE, which is the whole evolution plan: a
        BENCHED holder is paid `calculatedExp / 2` from every kill
        (battle_script_commands.c:3375-3392) and that payout still sets
        `gLeveledUpInBattle` (:3527), so the mon EVOLVES after the battle
        (battle_main.c:5091-5113). Without it a low-level target has to be
        switched in against each of Steven's six mons and gets one-shot.

        Judged on `held_item`, never on the dialog: the bag says "switch the
        two items?" when the slot is occupied, and that box has to be answered
        rather than assumed away.
        """
        self.last_reason = None
        item_id = self._item_id(item)
        if not item_id:
            return self._fail(f"{item!r} is not an item this ROM knows about")

        # ALREADY HELD IS SUCCESS, and it has to be asked BEFORE the pocket.
        # Giving the item REMOVES it from the bag, so checking the pocket
        # first makes every call after the first one fail with "not in the
        # ITEMS pocket" -- which is exactly what stopped the first grind, one
        # run after the give it was reporting on had worked.
        party = self.d.state.party()
        want = str(mon).upper()
        idx, target = next(
            ((i, m) for i, m in enumerate(party)
             if want in ((m.nickname or "").upper(),
                         self.names.species(m.species).upper())),
            (None, None))
        if target is None:
            return self._fail(f"no party member matches {mon!r}")
        if target.held_item == item_id:
            return True

        slots = self.pocket_items(ITEMS_POCKET)
        position = next(
            (i for i, (_s, iid, _q) in enumerate(slots) if iid == item_id),
            None)
        if position is None:
            return self._fail(f"{item} is not in the ITEMS pocket")
        label = target.nickname or self.names.species(target.species)

        if not self._open_bag():
            return False
        if not self._reach_pocket(ITEMS_POCKET):
            self._back_out()
            return self._fail("could not reach the ITEMS pocket")
        if not self._cursor_to(position, pocket=ITEMS_POCKET):
            self._back_out()
            return self._fail(f"could not put the cursor on {item}")
        if self._selected_item(ITEMS_POCKET) != item_id:
            self._back_out()
            return self._fail(
                f"cursor is on {self._selected_item(ITEMS_POCKET)}, not "
                f"{item} -- refusing to press A")

        self.emu.run_sequence("A:4 .:24")
        if not self.choose_give():
            self._back_out()
            return False
        if not self._wait_for_party_list():
            self._back_out()
            return self._fail("the party list never opened")
        if not self._pick_party_member(idx):
            self._back_out()
            return self._fail(f"could not select {label}")

        # The slot may already hold something, which opens a YES/NO swap box.
        for _ in range(10):
            self.emu.run_sequence("A:4 .:30")
            now = self.d.state.party()
            if idx < len(now) and now[idx].held_item == item_id:
                self._back_out()
                return True
        self._back_out()
        now = self.d.state.party()
        got = now[idx].held_item if idx < len(now) else None
        return self._fail(
            f"{label} holds {got}, not {item} ({item_id})")

    def choose_use(self, tries=8) -> bool:
        """Pick USE out of the item popup, verified before pressing A.

        `teach` used to press A once and trust that "its first row is USE for a
        machine". For a machine it is. The bug was never the assumption's
        content -- it was making one at all: when the bag was DISPLAYING the
        POKE BALLS pocket, that same blind A opened a GREAT BALL's popup, whose
        first row is GIVE, and GIVE also opens the party list. So the flow
        sailed through `_wait_for_party_list`, picked the lead, and handed it
        the ball. The game said so plainly -- "SEA BIRD is already holding one
        GREAT BALL. Would you like to switch the two items?" -- and that
        unanswered YES/NO box wedged the run at Route 110 (6,38) for fourteen
        minutes. Reported from the couch as "still trying to give seabird a
        pokeball to hold", which is exactly what it was doing.

        Same shape as gotcha 13 and 18: any menu whose rows you do not read is
        a menu that will eventually do something else.
        """
        want = self._use_action()
        rows = self.popup_rows()
        if want not in rows:
            return self._fail(
                f"the item popup {rows} offers no USE action ({want}) -- "
                f"refusing to press A on it"
            )
        target = rows.index(want)
        for _ in range(tries):
            cur = self.emu.u8("sPopupMenuSelection")
            if cur == target:
                self.emu.run_sequence("A:4 .:16")
                return True
            # sub_80A5414's 2x2 grid: UP/DOWN flip bit 0, LEFT/RIGHT step by 2.
            if (cur & 1) != (target & 1):
                key = "DOWN" if target & 1 else "UP"
            else:
                key = "RIGHT" if target > cur else "LEFT"
            self.emu.run_sequence(f"{key}:4 .:12")
            if self.emu.u8("sPopupMenuSelection") == cur:
                return self._fail(
                    f"popup cursor stuck on {cur} (wanted USE at {target})"
                )
        return self._fail(f"popup cursor never reached USE at {target}")

    def _on_pocket(self, pocket: int) -> bool:
        """Is the bag actually SHOWING the pocket we are driving?

        `_selected_item(pocket)` reads that pocket's own scroll state, which is
        correct data about the wrong thing when the bag is displaying a
        different pocket: the verification passed while the visible cursor sat
        on a GREAT BALL. Read `sCurrentBagPocket` too.
        """
        return self._displayed_pocket() == pocket

    def use_key_item(self, item: str) -> bool:
        """Press USE on a KEY ITEM, and let the caller judge the world.

        The same bag chain as `use_on_mon` minus the party list: a key item
        acts on the overworld, so nothing to target and nothing to confirm.
        The bike closes the bag by itself when it mounts, which is why success
        is NOT judged here -- the caller reads the avatar.

        Refuses before pressing anything when the item is not in the pocket,
        because a half-driven bag leaves a modal box open and an open box eats
        every movement input afterwards (gotcha 7).
        """
        self.last_reason = None
        item_id = self._item_id(item)
        if not item_id:
            return self._fail(f"{item!r} is not an item this ROM knows about")

        slots = self.pocket_items(KEY_ITEMS_POCKET)
        position = next(
            (i for i, (_slot, iid, _qty) in enumerate(slots) if iid == item_id),
            None,
        )
        if position is None:
            return self._fail(f"{item} is not in the KEY ITEMS pocket")

        if not self._open_bag():
            return False
        if not self._reach_pocket(KEY_ITEMS_POCKET):
            self._back_out()
            return self._fail("could not reach the KEY ITEMS pocket")
        if not self._cursor_to(position, pocket=KEY_ITEMS_POCKET):
            self._back_out()
            return self._fail(f"could not put the cursor on {item}")
        if self._selected_item(KEY_ITEMS_POCKET) != item_id:
            self._back_out()
            return self._fail(
                f"cursor is on item {self._selected_item(KEY_ITEMS_POCKET)}, "
                f"not {item} -- refusing to press A on the wrong item")
        if not self._on_pocket(KEY_ITEMS_POCKET):
            self._back_out()
            return self._fail(
                f"the bag is showing pocket {self._displayed_pocket()}, not "
                f"KEY ITEMS ({KEY_ITEMS_POCKET}) -- refusing to press A")

        # A opens the popup; USE is found in it, never assumed to be row 0.
        self.emu.run_sequence("A:4 .:24")
        if not self.choose_use():
            self._back_out()
            return False
        # The bike dismisses the bag itself. Anything that does not gets
        # backed out so we never leave a modal box eating movement.
        self.emu.run_sequence(".:40")
        return True

    def use_on_mon(self, item: str, mon=None, max_frames: int = 40_000) -> bool:
        """Use a bag ITEM on a party member, and prove it landed.

        Written for evolution stones, which are the cheapest dex entries in the
        game: all six sit on ONE counter in Lilycove's department store
        (pret/data/maps/LilycoveCity_DepartmentStore_5F/scripts.inc:22-27), and
        the ROM's own table is what says who they work on -- e.g.
        `[SPECIES_LOMBRE] = {{EVO_ITEM, ITEM_WATER_STONE, SPECIES_LUDICOLO}}`
        (pret/src/data/pokemon/evolution.h:141).

        Refuses BEFORE pressing anything when the item is not in the bag or the
        named mon is not in the party, because a half-driven bag leaves a modal
        box open and an open box eats every movement input afterwards
        (gotcha 7). Success is judged on the mon's SPECIES changing, or failing
        that on the bag count dropping -- never on the dialog text.

        Proved end to end on the live save with a potion, because the stone
        counter is five floors up and `travel` cannot route a multi-floor
        interior: `use_on_mon("SUPER POTION", "SEA BIRD")` returned True with
        SEA BIRD at 99 -> 107/107 and the pocket count 10 -> 9. The chain that
        proves is the whole one -- START, BAG, the ITEMS pocket, the cursor, A,
        USE, the party list, the named mon -- and a stone differs from a potion
        only in which item id the cursor lands on.
        """
        self.last_reason = None
        item_id = self._item_id(item)
        if not item_id:
            return self._fail(f"{item!r} is not an item this ROM knows about")

        slots = self.pocket_items(ITEMS_POCKET)
        position = next(
            (i for i, (_slot, iid, _qty) in enumerate(slots) if iid == item_id),
            None,
        )
        if position is None:
            return self._fail(f"{item} is not in the ITEMS pocket")
        held_before = slots[position][2]

        party = self.d.state.party()
        if mon is None:
            return self._fail("use_on_mon needs a target; refusing to guess")
        want = str(mon).upper()
        target_index, target = next(
            (
                (i, m) for i, m in enumerate(party)
                if want in ((m.nickname or "").upper(),
                            self.names.species(m.species).upper())
            ),
            (None, None),
        )
        if target is None:
            return self._fail(f"no party member matches {mon!r}")
        label = target.nickname or self.names.species(target.species)
        species_before = target.species

        if not self._open_bag():
            return False
        if not self._reach_pocket(ITEMS_POCKET):
            self._back_out()
            return self._fail("could not reach the ITEMS pocket")
        if not self._cursor_to(position, pocket=ITEMS_POCKET):
            self._back_out()
            return self._fail(f"could not put the cursor on {item}")
        if self._selected_item(ITEMS_POCKET) != item_id:
            self._back_out()
            return self._fail(
                f"cursor is on item {self._selected_item(ITEMS_POCKET)}, not "
                f"{item} -- refusing to press A on the wrong item")

        if not self._on_pocket(ITEMS_POCKET):
            self._back_out()
            return self._fail(
                f"the bag is showing pocket {self._displayed_pocket()}, not "
                f"ITEMS ({ITEMS_POCKET}) -- refusing to press A"
            )
        # A opens the item's popup; USE is FOUND in it, never assumed to be
        # row 0 -- row 0 is GIVE for a ball, and GIVE opens the party list too.
        self.emu.run_sequence("A:4 .:24")
        if not self.choose_use():
            self._back_out()
            return False
        if not self._wait_for_party_list():
            self._back_out()
            return self._fail("the party list never opened")
        if not self._pick_party_member(target_index):
            self._back_out()
            return self._fail(f"could not select {label} in the party menu")

        # The evolution scene is long and takes A presses to page through.
        start = self.emu.frame
        while self.emu.frame - start < max_frames:
            self.emu.run_sequence("A:4 .:30")
            party_now = self.d.state.party()
            if target_index < len(party_now):
                if party_now[target_index].species != species_before:
                    break
            if not self.d.scene_active() and not self._on_a_menu():
                break
        self.d.advance_scene(40_000)

        party_now = self.d.state.party()
        after = (party_now[target_index].species
                 if target_index < len(party_now) else species_before)
        if after != species_before:
            log.info("[item] %s used on %s: %s -> %s", item, label,
                     self.names.species(species_before),
                     self.names.species(after))
            return True

        slots_now = self.pocket_items(ITEMS_POCKET)
        held_after = next(
            (qty for _slot, iid, qty in slots_now if iid == item_id), 0
        )
        if held_after < held_before:
            log.info("[item] %s consumed on %s with no species change",
                     item, label)
            return True
        return self._fail(
            f"{item} left {label} unchanged and the bag count did not move "
            f"({held_before} still held)")

    def _on_a_menu(self) -> bool:
        """Is a bag or party menu still up? Used only to stop pressing A."""
        try:
            return self._displayed_pocket() is not None and bool(
                self.emu.u8("sNumStartMenuActions")
            ) and self._wait_for_party_list(max_presses=0)
        except Exception:  # noqa: BLE001
            return False

    def _item_id(self, name: str) -> int | None:
        """Resolve an item name to its id, whole name first.

        This used to compare only the FIRST underscore segment of the
        constant, which is fine for the single-word names it was written for
        (`ITEM_TM23` -> "TM23", `ITEM_HM06` -> "HM06") and wrong for every
        other item in the game: `ITEM_WATER_STONE` reduced to "WATER", so
        asking for a WATER STONE answered "not an item this ROM knows about".
        The whole-name match comes first; the first-segment match stays as a
        fallback so machine lookups behave exactly as before.
        """
        want = str(name).upper().replace(" ", "").replace("_", "")
        items = self.consts.items
        for const, value in items.items():
            full = const.replace("ITEM_", "").replace("_", "")
            if full == want:
                return value
        for const, value in items.items():
            if const.replace("ITEM_", "").split("_")[0] == want:
                return value
        return None

    #: The party list's selection lives in an OAM sprite, not a named
    #: variable -- the same wall `PartyOrder` hit. Found by diffing EWRAM
    #: across d-pad presses: this offset into `gSprites` counts 0,1,2,3,4
    #: exactly. It is used as a READING only, always sanity-checked, and the
    #: press count is still what moves the cursor.
    PARTY_CURSOR = 0x35E

    #: The most base power worth giving up for an HM. A status move (0) or a
    #: weak filler is fine; a real attack is not.
    MAX_SACRIFICE = 40

    #: The task that owns the party list in the item-use flow.
    PARTY_LIST_TASK = "sub_808B0C0"

    def _wait_for_party_list(self, max_presses: int = 10) -> bool:
        """Press through the info screen and the confirm to reach the list.

        Safe to press A here for the same reason as the choice box: the list
        does not exist yet, so there is nothing to select by accident.
        """
        for _ in range(max_presses):
            if self.PARTY_LIST_TASK in self.d.state.tasks():
                return True
            self.emu.run_sequence("A:4 .:36")
        return self.PARTY_LIST_TASK in self.d.state.tasks()

    def _party_cursor(self) -> int | None:
        """Where the party selection is, or None if the read looks wrong."""
        try:
            value = self.emu.u8(("gSprites", self.PARTY_CURSOR))
        except Exception:  # noqa: BLE001
            return None
        size = len([m for m in self.d.state.party() if m.level])
        return value if 0 <= value < max(1, size) else None

    def _pick_party_member(self, index: int, tries: int = 12) -> bool:
        """Select a party slot, closing the loop on the cursor.

        `gLastFieldPokeMenuOpened` is written on CONFIRM, not during
        navigation, so an earlier version read it, believed it was already in
        the right place, and pressed A on slot 0 every single time. That gave
        ROCK SMASH to MIGHTYENA by luck -- slot 0 could learn it -- and then
        offered CUT to a LOMBRE, which cannot, forever.

        The list is sprite-drawn so there is no official cursor to read, but
        the OAM field above tracks it exactly. Steer by that when it reads
        sanely and fall back to counted presses when it does not, because a
        raw sprite offset is precisely the sort of thing a ROM rebuild moves.
        """
        # Settle first: the frame a menu is drawn its input loop is not
        # running, and the opening presses vanish (gotcha 2).
        self.emu.run_sequence(".:40")
        # WAIT FOR THE CURSOR BYTE, DO NOT ASSUME IT. 18 frames was not
        # enough: the read came back with the PREVIOUS index, the loop pressed
        # DOWN again and overshot by one. Asking for NATU (party index 1) put
        # the EXP. SHARE on GOLDEEN (index 2) -- verified live, item landed on
        # the wrong mon while the function reported failure.
        #
        # The storage driver learned this same lesson on the same class of
        # byte and measured ~400 frames; poll for the CHANGE instead of
        # guessing a number, and give up only after a real wait.
        def _settled(previous):
            for _ in range(10):
                self.emu.run_sequence(".:40")
                now = self._party_cursor()
                if now is not None and now != previous:
                    return now
            return self._party_cursor()

        for _ in range(tries):
            cursor = self._party_cursor()
            if cursor is None:
                break
            if cursor == index:
                self.emu.run_sequence("A:4 .:30")
                return True
            self.emu.run_sequence("DOWN:6" if cursor < index else "UP:6")
            moved = _settled(cursor)
            if moved is None:
                break
        # Blind fallback: home to the top, then count down.
        for _ in range(6):
            self.emu.run_sequence("UP:6 .:18")
        for _ in range(max(0, index)):
            self.emu.run_sequence("DOWN:6 .:18")
        self.emu.run_sequence("A:4 .:30")
        return True

    #: The prompt that asks which move to give up. Matched on the game's own
    #: wording rather than a task name, because the task is an unnamed
    #: `sub_806F44C` and the sentence is stable.
    FORGET_MARKERS = ("already knows", "wants to learn")

    def _at_forget_prompt(self) -> bool:
        message = (self.d.state.message() or "").lower()
        return any(m in message for m in self.FORGET_MARKERS)

    def slot_to_forget(self, mon, new_move_id) -> int:
        """Which of four moves to give up, and never a good one.

        Blind A presses take the FIRST slot, and the first time this ran it
        made a BLAZIKEN forget BLAZE KICK -- its strongest move, 85 power --
        to make room for CUT. That is worse than not teaching CUT at all.

        Rules, the same ones the level-up path uses: never an HM move, since
        those cannot be deleted later and the road they open may still be
        needed; prefer a status move over any damaging one; otherwise the
        weakest by ROM base power, ties broken by the earliest slot so the
        choice is stable.
        """
        hm_moves = set()
        try:
            from pokeagent.missables import hm_moves as read_hms

            hm_moves = set(read_hms(self.emu, self.names, self.consts).values())
        except Exception:  # noqa: BLE001 - a missing table must not pick an HM
            pass

        def power(move_id):
            # EFFECTIVE power, so a move that cannot deliver its listed number
            # unaided is spent first. SPIT UP reads 100 and does nothing
            # without Stockpile; ranking it by 100 kept it on SEA BIRD through
            # two TMs, each of which overwrote the previous one instead.
            return self.effective_power(move_id)

        def mtype(move_id):
            try:
                return self.names.move_data(move_id).type
            except Exception:  # noqa: BLE001
                return None

        # COVERAGE BEFORE RAW POWER. Ranking on power alone made LOTTAD forget
        # ABSORB (20) to learn THIEF (40) -- and ABSORB was the party's only
        # Grass move, the one thing it had that was super-effective against the
        # Water gym it was walking into. FAKE OUT was sitting right there,
        # duplicating STRENGTH's type.
        #
        # So a move whose type this mon carries twice is spent first; a move
        # holding a type on its own is kept unless nothing else can go.
        types = [mtype(m) for m in mon.moves if m]
        duplicated = {t for t in types if t is not None and types.count(t) > 1}

        candidates = []
        for slot, move_id in enumerate(mon.moves):
            if not move_id or move_id in hm_moves:
                continue
            sole = mtype(move_id) not in duplicated
            candidates.append((0 if power(move_id) == 0 else 1,
                               1 if sole else 0, power(move_id), slot))
        if not candidates:
            return 0
        candidates.sort()
        return candidates[0][3]

    def _answer_forget_prompt(self, index, move_id) -> bool:
        """Pick the slot deliberately, then confirm."""
        party = self.d.state.party()
        if index >= len(party):
            return False
        mon = party[index]
        slot = self.slot_to_forget(mon, move_id)
        losing = self.names.move(mon.moves[slot]) if mon.moves[slot] else "?"
        log.info("forgetting %s (slot %d) to learn %s",
                 losing, slot, self.names.move(move_id))
        self.forgot = losing
        # A:16, and this is measured, not guessed. "<MON> wants to learn the
        # move FLY. However, <MON> already..." does NOT advance on a short tap:
        # A:2, A:6 and A:10 were each pressed eight times and the message never
        # moved, while A:16 walked it on the first press. The old A:2 comment
        # was right about a DIFFERENT box; this one wants a real press.
        for _ in range(10):
            if not self._at_forget_prompt():
                break
            self.emu.run_sequence("A:16 .:30")
        if self._at_forget_prompt():
            return False
        # READ the cursor, never count presses. `pssData.selectedMoveIndex` is
        # the move list's own selection and it tracks exactly -- measured
        # 0,0,1,2,3,4 over five DOWN presses, the first swallowed while the
        # list draws (gotcha 2). Counting presses is what made a BLAZIKEN
        # forget BLAZE KICK while the log said SAND-ATTACK.
        if not self._drive_forget_cursor(slot):
            return False
        self.emu.run_sequence("A:16 .:30")
        self.emu.run_sequence("A:16 .:30")     # confirm "should it forget?"
        return True

    def _drive_forget_cursor(self, slot, max_steps=12) -> bool:
        """Put the move list's cursor on `slot`, verifying every press."""
        read = self.d.battle._summary_move_cursor
        stuck = 0
        for _ in range(max_steps):
            cur = read()
            if cur == slot:
                return True
            self.emu.run_sequence("DOWN:8 .:20" if slot > cur else "UP:8 .:20")
            if read() == cur:
                stuck += 1
                if stuck > 1:
                    log.warning("the forget cursor would not leave %d "
                                "(wanted %d)", cur, slot)
                    return False
            else:
                stuck = 0
        log.warning("the forget cursor never reached %d (still %d)",
                    slot, read())
        return False

    def _settle_learn(self, index, move_id, move_name, label, max_frames) -> bool:
        """Answer whatever the learn flow asks, then check the moveset.

        A mon with four moves is asked which to forget; the answer comes from
        the same policy the level-up path uses, so an HM move is never
        overwritten and a damaging move is never traded for a status one.
        """
        start = self.emu.frame
        while self.emu.frame - start < max_frames:
            party = self.d.state.party()
            # Existing knowers elsewhere in the party are not evidence that
            # the requested recipient passed the learn/forget prompts.
            learner = party[index] if 0 <= index < len(party) else None
            if learner is not None and move_id in learner.moves:
                got = learner.nickname or self.names.species(learner.species)
                # The moveset is written BEFORE "X learned ROCK SMASH!"
                # finishes drawing, so backing out immediately fires B presses
                # into a message that swallows them -- the teach worked and the
                # run still could not walk afterwards. Let the message run
                # first, then leave the menus.
                self.d.settle()
                self.d.advance_scene(40_000)
                self._back_out()
                # What was ACTUALLY given up, not what was chosen. The move
                # list is as unreadable as the party list, so the only honest
                # answer is the moveset before and after.
                lost = [m for m in self._before_moves if m not in learner.moves]
                if lost:
                    actually = self.names.move(lost[0])
                    if self.forgot and actually != self.forgot:
                        log.warning("meant to forget %s but %s went instead",
                                    self.forgot, actually)
                    self.forgot = actually
                if got != label:
                    return self._fail(f"taught {move_name} to {got}, not {label}")
                log.info("taught %s to %s (forgot %s)", move_name, got,
                         self.forgot or "nothing")
                self.taught_to = got
                return True
            # The OVERWORLD prompt, not the battle one. `battle.at_learn_prompt`
            # reads battle-script opcodes and is blind here, so the flow ran on
            # blind A presses and took whatever slot came first.
            if self._at_forget_prompt():
                self._answer_forget_prompt(index, move_id)
                continue
            self.emu.run_sequence("A:2 .:12")
        self._back_out()
        return self._fail(
            f"pressed through the {move_name} flow but {label}'s moveset never "
            f"changed")

    def _back_out(self) -> None:
        """Get control back. A menu left open eats every movement input.

        B first, because it is the safe key in a list; then advance_scene,
        which knows how to leave a full-screen menu and is the same backstop
        PartyOrder settled on after ten B presses proved not to be enough.
        """
        # 24 frames, not 16: the "learned ROCK SMASH!" message is still
        # closing when the first B lands, and a short settle reads the scene
        # as still active and burns the whole budget on a menu that was
        # already going away. Measured: it clears on the third press.
        # LEAVING THIS LIST IS A SEQUENCE, NOT A BUTTON -- and the presses
        # have to be long. B on the move list asks "give up trying to learn
        # X?", A answers YES, and a further B leaves the bag; pressing only
        # one of them, however many times, gets nowhere. Measured on a wedged
        # teach: 24 B presses across three hold lengths left
        # sLockFieldControls at 1, 20 A presses likewise, and directly pressing
        # DOWN/LEFT/RIGHT never moved the player -- while B/A/B with 16-frame
        # holds cleared it in seven rounds and the learned move persisted.
        #
        # The screenshot is what settled it: the stuck frame is the BATTLE
        # MOVES list with the cursor on SURF and "HM moves can't be forgotten
        # now." in the description box. The flow was not waiting for a menu to
        # close, it was waiting to be told to give up.
        for _ in range(10):
            for seq in ("B:16 .:40", "A:16 .:40", "B:16 .:40"):
                self.emu.run_sequence(seq)
                if not self.d.scene_active():
                    break
            if not self.d.scene_active():
                break
        # ALWAYS, not only when B failed. Backing out of the bag leaves the
        # overworld mid-fade, and a caller that immediately tries to walk gets
        # its input eaten -- which is how a teach that worked still stalled the
        # run. advance_scene is the one thing that knows a menu from a fade.
        self.d.advance_scene(40_000)
        if self.d.scene_active():
            log.warning("teach: still on a menu after backing out")
