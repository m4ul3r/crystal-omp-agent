"""Driver inventory, field moves, PC, shopping, and healing."""

import contextlib
import heapq
import inspect
import json
import logging
import random
import re
import sys
from collections import deque
from io import BytesIO
from pathlib import Path

from .. import hookevents, missables, paths
from ..battle import (Battle, BattleData, bag_item_index, bag_quantity,
                      cheapest_heal, goto_pocket, norm_item)
from ..charmap import Charmap
from ..decide import DecisionRequired, TurnLog as _TurnLog, battle_frame as _decide_frame
from ..emu import Crystal, InputError, parse_sequence
from ..menus import Menus, battle_menu_up, dialog_press_safe, CURSORS
from ..names import Names
from ..nav import (COLL_PIT, CONN_NAME, HOPS, ICE, MapData, STEP, TrekNav,
                   WALKABLE, WARPS, WATER as _NAV_WATER, ICE as _NAV_ICE,
                   _CONN_LAND, _CONN_LETTER, _file_const, _tile_kind,
                   coord_events, mapgraph, render_map_view, scene_consts,
                   scene_vars, script_advances_scene, script_guards,
                   script_is_disruptive)
from ..schemas import validate_observe, validate_route
from ..state import (MONS_PER_BOX, SPRITE_WANDERERS, box_state, game_state,
                     live_sprites, status_line)
from ..symfile import Symbols

log = logging.getLogger("trek")

class HealError(RuntimeError):
    """heal_pokecenter(): no nurse reachable from here. Carries the map
    name so callers (registry 'heal') can report a structured failure
    instead of exploding mid-composite. Subclasses RuntimeError so old
    `except RuntimeError` guards keep working."""

    def __init__(self, map_name, detail=""):
        self.map_name = map_name
        msg = f"heal_pokecenter: not inside a Pokécenter (on {map_name})"
        if detail:
            msg += f" -- {detail}"
        super().__init__(msg)

_NUM_TMS, _NUM_HMS = 50, 7   # item_constants.asm DEF NUM_TMS / NUM_HMS

def _item_row_matches(row_text, want_norm):
    """True when a scraped pack-menu row names the wanted item (wren pt4:
    use_item('SUPER POTION') False). Both sides go through norm_item, so
    the compare is blind to case, spaces, hyphens, and the POKe glyph.
    The row may carry trailing junk (quantity digits, scroll-arrow tiles
    at the box edge) -- covered by the prefix test -- and may lose
    trailing tiles at the screen edge, so a near-complete row that is
    itself a prefix of the wanted name (>= max(4, len-2) chars) also
    matches. A different item can never pass: the row is anchored at the
    cursor arrow, and prefix containment between distinct item names
    ('POTION' in 'SUPER POTION') fails in both directions."""
    row = norm_item(row_text)
    if not row or not want_norm:
        return False
    if row.startswith(want_norm):
        return True
    return len(row) >= max(4, len(want_norm) - 2) and \
        want_norm.startswith(row)


# Pack pocket banners, data/items/pocket_names.asm (DrawPocketName paints
# one on every pocket screen).
_POCKET_BANNERS = ("ITEM POCKET", "BALL POCKET", "KEY POCKET", "TM POCKET")

# The pack's quantity column: '...  ×  4' (charmap ×), tolerating a plain
# 'x'/'X' decode and a scroll-arrow tile at the box edge.
_PACK_QTY_RE = re.compile(r"×\s*\d+|(?:^|\s)[xX]\s+\d+\s*[▼▲]?\s*$")


def _pack_pocket_banner(rows):
    """Which pocket banner the pack is drawing, or None."""
    for r in rows:
        up = r.upper()
        for name in _POCKET_BANNERS:
            if name in up:
                return name
    return None


def _pack_quantity_rows(rows):
    """True when any drawn row carries the pack's 'x N' quantity column
    -- no other field UI prints one."""
    return any(_PACK_QTY_RE.search(r) for r in rows)


# The pack's "Use on which PM?" target list prints 'hp/max' fractions; the
# item pocket's own column is '× n', so this never confuses the two.
_HP_FRACTION_RE = re.compile(r"\d\s*/\s*\d+")


def _party_target_list(rows):
    """True only when the pack's "Use on which PM?" party list is really
    drawn. "a CANCEL row is on screen" is NOT enough: the item pocket
    draws its own CANCEL row once scrolled to the bottom of the list, and
    accepting that as the target list let the party steering run while
    the POCKET's cursor was still live in wMenuCursorY (the party list is
    a 2D menu; wMenuScrollPosition there still holds the pocket's
    offset) -- an A fired at the wrong screen. Same predicate battle.py
    uses for the in-battle target list."""
    joined = "".join(rows).upper()
    if "USE ON WHICH" in joined:
        return True
    return any("CANCEL" in r for r in rows) and \
        any(_HP_FRACTION_RE.search(r) for r in rows)


def _no_effect_message(rows):
    """_ItemWontHaveEffectText ("It won't have any" / "effect.",
    data/text/common_3.asm) -- the engine refusing a LEGITIMATE no-op: a
    full-HP unstatused target, an ANTIDOTE on a clean mon, a POTION on a
    fainted one. Nothing is consumed and nothing ever will be, so this
    must be reported as its own outcome, never mashed through as if the
    A had been swallowed."""
    joined = "".join(rows).upper()
    return "HAVE ANY" in joined and "EFFECT" in joined


def _field_clear(rows):
    """No modal field UI left on screen -- no menu cursor, no pack /
    party-list / START-menu row. The postcondition every field-item flow
    has to restore: a stray START menu silently eats all movement input
    (gotcha 7)."""
    bad = ("▶", "▷", "CANCEL", "QUIT", "EXIT", "USE", "TOSS")
    return not any(b in r for r in rows for b in bad)


def _norm_name(text):
    """Canonical mon-nickname key: uppercase alphanumerics only, so
    'Brook', 'BROOK' and ' brook ' all address the same party member.
    (norm_item is the ITEM key -- it also rewrites '#' to POKE, which has
    no business happening to a nickname.)"""
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


_UNSET = object()       # use_item(target_slot=...) "argument not given"

# constants/item_data_constants.asm: ITEMATTR_STRUCT_LENGTH, with
# ITEMATTR_PRICE the first (little-endian) word of each entry.
_ITEMATTR_LENGTH = 7
# engine/items/item_effects.asm: the ItemEffects jumptable is
# `assert_table_length ITEM_B3` ("The items past ITEM_B3 do not have
# effect entries"). Every curative item sits well inside it.
_ITEM_EFFECTS_ENTRIES = 0xB3

_field_heal_table = None    # lazily read {norm item: heal/cure/price}


def _load_heal_table(rom_path, sym, names):
    """Every curative pack item, read out of the ROM's OWN tables so no
    game data is hardcoded here (AGENTS.md: "the repo is the map"):

      * HealingHPAmounts   (data/items/heal_hp.asm)     -- HP restored
      * StatusHealingActions (data/items/heal_status.asm) -- cured bits
      * the ItemEffects jumptable's ReviveEffect entries -- revives
      * ItemAttributes     (data/items/attributes.asm)  -- shop price

    Returns {normalized name: {'name', 'hp', 'cures', 'revives',
    'price'}}; 'cures' is a wPartyMon*Status bit mask, 'hp' 0 for items
    that restore none."""
    with open(rom_path, "rb") as f:
        rom = f.read()

    def off(label):
        bank, base = sym[label]
        return base if base < 0x4000 else bank * 0x4000 + (base - 0x4000)

    attrs = off("ItemAttributes")
    table = {}

    def row(item_id):
        name = names.items.get(item_id)
        if not name:
            return None
        base = attrs + (item_id - 1) * _ITEMATTR_LENGTH
        return table.setdefault(norm_item(name), {
            "name": name, "hp": 0, "cures": 0, "revives": False,
            "price": int.from_bytes(rom[base:base + 2], "little")})

    p = off("HealingHPAmounts")          # dbw item, hp restored
    while rom[p] != 0xFF:
        got = row(rom[p])
        if got is not None:
            got["hp"] = int.from_bytes(rom[p + 1:p + 3], "little")
        p += 3
    p = off("StatusHealingActions")      # db item, menu text, status mask
    while rom[p] != 0xFF:
        got = row(rom[p])
        if got is not None:
            got["cures"] = rom[p + 2]
        p += 3
    jump, revive = off("ItemEffects"), sym["ReviveEffect"][1]
    for item_id in range(1, _ITEM_EFFECTS_ENTRIES + 1):
        p = jump + (item_id - 1) * 2
        if int.from_bytes(rom[p:p + 2], "little") == revive:
            got = row(item_id)
            if got is not None:
                got["revives"] = True
    return table

def _enter_local_pokecenter(d, tries):
    """heal called outside a Pokécenter: if the CURRENT map has a routable
    Pokécenter warp in the mapgraph, walk in via the normal travel
    machinery (goto approach + held warp entry) instead of exploding.
    Bounded by `tries` travel attempts; raises HealError otherwise."""
    here = d.map_name()
    pcs = sorted({e["to_map"] for e in mapgraph()["edges"]
                  if e.get("routable") and e["from_map"] == here
                  and "POKECENTER" in e["to_map"]})
    if not pcs:
        raise HealError(here, "no Pokécenter warp on this map")
    pc = pcs[0]
    tries = max(1, int(tries))
    last = None
    for attempt in range(1, tries + 1):
        log.info(f"  heal: not in a Pokécenter (on {here}); "
                 f"entering {pc} (try {attempt}/{tries})")
        try:
            d.travel(pc, label="heal detour")
        except Exception as e:            # TravelError, LookupError, ...
            last = e
            log.info(f"  heal detour attempt {attempt} failed: {e}")
        if "POKECENTER" in d.map_name():
            return
    raise HealError(d.map_name(),
                    f"couldn't enter {pc} after {tries} "
                    f"tr{'y' if tries == 1 else 'ies'}"
                    + (f" ({last})" if last else ""))


def heal_pokecenter(d, tries=2):
    """Talk to the nurse, wait out the jingle. Verifies the location on
    entry and the actual heal on exit -- an unverified 'healed' claim once
    masked a failed goto entirely. Called outside a Pokécenter, walks in
    first when the current map has a routable Pokécenter warp in the
    mapgraph (bounded by `tries`); raises HealError when it genuinely
    cannot reach a nurse (wren pt4/pt5: the old bare RuntimeError blew up
    whole composites over a recoverable one-map detour)."""
    if "POKECENTER" not in d.map_name():
        _enter_local_pokecenter(d, tries)

    def _hp_snapshot():
        return tuple(m["hp"] for m in game_state(d.emu, d.names)["party"])

    def _wait_heal_settled(timeout=1500):
        """The jingle animates HP upward; reading before it finishes is
        the stale-HP raise class (omp-fresh: 6/7 heals). Settled = HP
        stable across polls with no textbox and no owning script."""
        f0, prev = d.emu.frame, None
        while d.emu.frame - f0 < timeout:
            cur = _hp_snapshot()
            if cur == prev and not d.textbox() \
                    and d.emu.read_u8("wScriptMode") == 0:
                return True
            prev = cur
            d.emu.tick(10)
        return False

    def _nurse_cell():
        """The nurse's own coordinates, from this map's object_events.

        (3,3) was hardcoded, which is the Johto town layout: it put
        INDIGO_PLATEAU_POKECENTER_1F -- counter on row 8, nurse behind
        (3,7) -- permanently out of reach, and heal_pokecenter raised
        'party not fully healed' after routing to a cell with no nurse in
        front of it (FUCK_I_MESSED_UP.md #78). The map declares where she
        stands; ask it."""
        try:
            cell = d.sprite_cell("SPRITE_NURSE")
        except Exception as e:               # unparsed/absent map source
            log.info(f"  heal: cannot read {d.map_name()}'s objects ({e})")
            return None
        if cell is None:
            log.info(f"  heal: no SPRITE_NURSE object_event on "
                     f"{d.map_name()}")
        return cell

    def _nurse():
        cell = _nurse_cell()
        if cell is None:
            # last resort: the Johto counter layout this used to assume
            d.goto(3, 3, "nurse counter")
            d.step_dir("U")    # face her (blocked step = turn)
            d.press("A:2 .:20")
            d.flush_dialog()
        elif not d.talk_to(*cell, label="nurse"):
            raise HealError(d.map_name(),
                            f"could not reach the nurse at {cell}")
        # intro page(s) done -- flush stops ("menu") at the heal prompt.
        # The YES/NO box is a deliberate choice: cursor defaults to YES,
        # but an extra stray A earlier can leave it on NO (omp-fresh
        # variant), so navigate explicitly.
        if d.menu.wait_for(lambda rows: any("YES" in r for r in rows),
                           260):
            d.menu.select_label("YES", max_presses=4)
        _wait_heal_settled()   # HP-keyed jingle wait, not a blind frame
        d.flush_dialog()       # "we hope to see you again"
        d.settle()
        d.flush_dialog(1500)   # sweep straggler pages before verifying

    def _hurt():
        return [m for m in game_state(d.emu, d.names)["party"]
                if not m.get("egg") and m.get("hp", 0) < m.get("max_hp", 0)]

    _nurse()
    hurt = _hurt()
    if hurt:                   # late pages can sit between us and truth
        d.flush_dialog(2000)
        hurt = _hurt()
    if hurt:
        # gotcha 2 first-call race: the A that opens the nurse dialog is
        # swallowed when the counter goto ends on an unsettled frame --
        # settle, drain, and redo the interaction ONCE before raising
        log.info("  heal not confirmed; settling and retrying once")
        d.settle()
        d.flush_dialog(1500)
        _nurse()
        hurt = _hurt()
    lead = d.lead()
    log.info(f"  healed: {lead['name']} {lead['hp']}/{lead['max_hp']}",
          )
    if hurt:
        raise RuntimeError(
            f"heal_pokecenter: party not fully healed "
            f"({[(m['species'], m['hp'], m['max_hp']) for m in hurt]})")
    # success: the player is still standing in front of the nurse facing
    # her, and the next A-bearing routine re-opens her prompt (two leg-2
    # wedges). Step AWAY from whatever direction we are facing -- not
    # blindly south, which only held for the y=3 Johto counter -- and
    # settle so no residual prompt stays armed.
    away = {"U": "D", "D": "U", "L": "R", "R": "L"}.get(d.facing(), "D")
    if d.step_dir(away) != "moved":
        d.step_dir(away)           # first press may only turn in place
    d.settle()
    # ...and if her question is STILL on screen, answer it. A live heal
    # returned with "Shall we heal your POKéMON?" open, and an open
    # choice box blocks every subsequent step: the next `travel` reported
    # "blocked by choice menu ['YES','NO']" from inside the Pokécenter
    # and the gym leg never started. Declining a heal we do not need is
    # always safe; anything else gets closed.
    # (guarded: reduced/duck-typed drivers do not model choice boxes)
    reader = getattr(d, "_choice_box", None)
    if reader and reader(d.emu.screen_text()):
        if not d.resolve_choice("NO").get("answered"):
            d.close_menus()
        d.flush_dialog(1500)
        d.settle()

_tmhm_table = None
_species_tmhm = None

class InventoryMixin:
    """Owns Driver inventory, PC, field-move, shop, and healing behavior."""
    def _bag(self):
        """{ITEM: qty} across all pockets; names normalized with
        norm_item ('# BALL' -> 'POKE BALL')."""
        e = self.emu
        bag = {}
        for count_sym, list_sym in (("wNumItems", "wItems"),
                                    ("wNumBalls", "wBalls"),
                                    ("wNumKeyItems", "wKeyItems")):
            n = min(e.read_u8(count_sym), 26)
            if not n:
                continue
            bank, addr = e.sym[list_sym]
            # key items are 1 byte each (no quantity); other pockets are
            # (id, qty) pairs -- the pair stride used to hide every other
            # key item and report garbage quantities
            step = 1 if list_sym == "wKeyItems" else 2
            raw = e.read((bank, addr), n * step)
            for i in range(n):
                name = norm_item(self.names.items.get(raw[i * step],
                                                       f"?{raw[i * step]}"))
                qty = raw[i * step + 1] if step == 2 else 1
                bag[name] = bag.get(name, 0) + qty
        bank, addr = e.sym["wTMsHMs"]          # one count byte per TM/HM
        counts = e.read((bank, addr), _NUM_TMS + _NUM_HMS)
        for i, n in enumerate(counts):
            if n:
                key = (f"TM{i + 1:02d}" if i < _NUM_TMS
                       else f"HM{i - _NUM_TMS + 1:02d}")
                bag[key] = bag.get(key, 0) + n
        return bag

    _CUT_TREE_BYTE = 0x12


    def _party_knows(self, move_name, slot=None):
        """(knows, party_index): does any party member know `move_name`?

        With `slot`, the question narrows to THAT party row. teach_tm
        names a mon, so a party-wide answer was a false success: with
        STRENGTH already on DUCK, teaching it to EMBER "succeeded"
        without pressing a single button."""
        party = self.observe()["party"]
        if slot is not None:
            if slot >= len(party):
                return False, None
            mon = party[slot]
            hit = any(m.get("name") == move_name
                      for m in mon.get("moves", []))
            return hit, (slot if hit else None)
        for idx, mon in enumerate(party):
            if any(m.get("name") == move_name for m in mon.get("moves", [])):
                return True, idx
        return False, None

    def _teach_hm01(self, forget_move=None):
        return self.teach_hm("H1", "CUT", forget_move)

    def _tmhm_pocket(self, max_presses=8):
        """START -> PACK -> the TM/HM pocket (pack.asm jumptable state 8).
        The pockets cycle on L, so at most 3 presses reach it."""
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            return self._menu_fail("tmhm_pocket: START menu never opened")
        if not self.menu.select_label("PACK"):
            why = getattr(self.menu, "last_reason", None) or "no PACK row"
            return self._menu_fail(f"tmhm_pocket: {why}")
        for _ in range(max_presses):
            if self.emu.read_u8("wJumptableIndex") == 8:
                self.press(".:35")
                return True
            self.press("L:4 .:18")
        return self._menu_fail("tmhm_pocket: TM/HM pocket never opened "
                               "(wJumptableIndex never reached 8)")

    @staticmethod
    def pocket_tag(tag):
        """The text a TM/HM pocket ROW actually shows for 'TM01'/'HM03'.

        The 'TM'/'HM' prefix is drawn in GRAPHICS tiles, so the decoded
        row is '01 DYNAMICPUNCH' for a TM and 'H1 CUT' for an HM (live
        screen dump, Olivine pack) -- matching on 'TM01' never hits.
        """
        tag = str(tag).strip().upper()
        if tag.startswith("TM") and tag[2:].isdigit():
            return tag[2:]                       # 'TM01' -> '01'
        if tag.startswith("HM") and tag[2:].isdigit():
            return f"H{int(tag[2:])}"            # 'HM03' -> 'H3'
        return tag                               # already screen-shaped

    def _tmhm_row(self, tag, move_name):
        """Put the pocket cursor on the row naming this TM/HM.

        Rows render as '<tag> <MOVE>' -- '01 DYNAMICPUNCH', 'H1 CUT' --
        and a bare move match is not enough ('FURY CUTTER' contains
        'CUT'), so both halves must be on the row. They are tested
        SEPARATELY because the cursor glyph is painted between them
        ('H3▶SURF'). The list is walked UP to the top first, because the
        pocket remembers its cursor between opens."""
        tag = self.pocket_tag(tag)

        def on_row():
            return any(tag in r and move_name in r
                       for r in self.cursor_rows())
        for _ in range(10):
            if on_row():
                return True
            self.press("U:4 .:14")
        for _ in range(60):
            if on_row():
                return True
            self.press("D:4 .:16")
        return self._menu_fail(
            f"tmhm_row: no row reading '{tag} {move_name}' came under the "
            f"cursor")

    def _tmhm_party_list_up(self, joined=None):
        if joined is None:
            joined = "".join(self.emu.screen_text()).upper()
        return "ABLE" in joined

    def _tmhm_use(self, max_steps=26):
        """Confirm the pocket row, take USE, answer the teach prompt's
        YES, and end on the party list.

        Written as a classify-then-act loop instead of a press script,
        because the press script could not finish the flow: it answered
        the teach prompt with ONE A and then only TICKED, waiting for the
        party list. Live (claude-goldeen checkpoint, HM07 -> GOLDEEN --
        FUCK_I_MESSED_UP.md #71/#68, five failed attempts) the YES/NO box
        eats the first A the frame it is drawn (gotcha 2), so the box was
        still up when the ticking started and the list never came.

        Every iteration reads the screen and acts on what is THERE, and
        the party-list test is checked BEFORE any press -- an A press on
        that list selects a mon, which is how a probe of this flow put
        'WATERFALL is not compatible' on screen by picking NOCTOWL."""
        self.press("A:4 .:60")                  # pocket row -> USE/QUIT
        for _ in range(max_steps):
            joined = "".join(self.emu.screen_text()).upper()
            if self._tmhm_party_list_up(joined):
                return True
            if Menus.has_label(self.emu.screen_text(), "YES"):
                self.press("A:5 .:45")          # teach prompt: YES
            elif "YES" in joined and "NO" in joined:
                self.press("U:4 .:16")          # cursor drifted onto NO
            elif Menus.has_label(self.emu.screen_text(), "USE"):
                self.press("A:5 .:40")
            elif self.textbox():
                self.press("A:4 .:40")          # "Booted up an HM." pages
            else:
                self.press(".:20")              # mid-repaint: poll
        return self._menu_fail(
            f"tmhm_use: party list never opened in {max_steps} steps "
            f"(row 14: {self.emu.screen_text()[14].strip()!r})")

    def _able_under_cursor(self):
        """Is the party row under the cursor ABLE to learn this TM/HM?

        Answered from wMenuCursorY, not from the cursor glyph: mon `n`
        (1-based, exactly what wMenuCursorY holds and what
        _party_cursor_to steers) has its name on screen row 2n-1 and its
        ABLE / NOT ABLE tag on row 2n, because
        PlacePartyMonTMHMCompatibility starts at hlcoord 12, 2 and adds
        2 * SCREEN_WIDTH per mon (party_menu.asm:300-330). The glyph scan
        is kept as a fallback for screens where the cursor row is painted
        but WRAM has not caught up."""
        rows = self.emu.screen_text()

        def verdict(text):
            up = text.upper()
            if "ABLE" not in up:
                return None
            return "NOT ABLE" not in up
        cur = self.emu.read_u8("wMenuCursorY")
        if 1 <= cur and 2 * cur < len(rows):
            tag = verdict(rows[2 * cur][12:])
            if tag is not None:
                return tag
        for i, r in enumerate(rows):
            if "▶" in r or "▷" in r:
                tag = verdict(rows[i + 1] if i + 1 < len(rows) else "")
                return bool(tag)
        return False

    def _forget_row(self, forget, slot):
        """0-based row of the move to delete in the forget list.

        The list mirrors the learner's four move slots, so the row is an
        INDEX, not a text search: `cursor_rows()` also carries the party
        list drawn underneath, and matching text there walked the wrong
        menu. Without a named `forget` the first non-HM move goes -- the
        list opens on slot 1, and slot 1 is very often CUT, which the
        game refuses ("HM moves can't be forgotten now") forever.
        """
        party = self.observe()["party"]
        idx = 0 if slot is None else slot
        moves = [m.get("name") for m in party[idx].get("moves", [])] \
            if idx < len(party) else []
        if forget:
            want = forget.strip().upper()
            for i, name in enumerate(moves):
                if name and name.upper() == want:
                    return i
        for i, name in enumerate(moves):
            if name and name.upper() not in self.HM_MOVES:
                return i
        return 0

    def _walk_forget_menu(self, move_name, forget=None, slot=None):
        """Drive whatever follows the party pick: an outright learn, or
        the "delete a move?" YES plus the move list.

        Shared by teach_hm and teach_tm: one implementation of the walk
        that decides which move disappears. `slot` scopes the "did it
        land?" question to one party row -- teach_tm names a mon, and a
        party-wide check called it done before pressing anything."""
        target = None
        for _ in range(24):
            if self._party_knows(move_name, slot)[0]:
                break
            rows = self.emu.screen_text()
            s = "".join(rows).upper()
            if "FORGOTTEN?" in s.replace(" ", ""):
                # the move list is up: walk DOWN by index from slot 1
                if target is None:
                    target = self._forget_row(forget, slot)
                for _ in range(target):
                    self.press("D:2 .:18")
                self.press("A:3 .:80")
                target = 0        # a re-ask reopens on the same row
            elif "YES" in s and "NO" in s:
                # "Delete an older move?" -> YES;
                # "Stop learning <MOVE>?"  -> NO (cursor starts on YES)
                if "STOPLEARNING" in s.replace(" ", ""):
                    self.press("D:2 .:20")
                self.press("A:3 .:70")
            else:
                self.press("A:4 .:45")
        for _ in range(14):                           # drain learn texts
            if not self.textbox():
                break
            self.press("A:4 .:50")
        return self._party_knows(move_name, slot)[0]

    def teach_hm(self, hm_tag, move_name, forget_move=None):
        """Teach the HM whose pocket row reads '<hm_tag> <move_name>'
        (e.g. 'H3', 'SURF') to the first ABLE party member via PACK ->
        TM/HM pocket. `forget_move` names the move to delete if the
        learner already knows four (default: whatever the cursor starts
        on, slot 1). Label/WRAM-driven throughout: menus remember their
        last cursor slot, so blind press counts are never safe.
        Raises RuntimeError (with menus closed) if the flow fails, and
        returns the NICKNAME that ended up knowing the move (the first
        ABLE member -- which is often not the one you had in mind).

        For a NAMED party member and a machine-readable failure instead
        of an exception, use teach_tm -- both drive the same steps."""
        def bail(msg):
            self.close_menus()
            raise RuntimeError(f"teach_hm {move_name}: {msg}")
        if not self._tmhm_pocket():
            bail(self.last_menu_reason or "TM/HM pocket never opened")
        if not self._tmhm_row(hm_tag, move_name):
            bail(self.last_menu_reason or "HM row never under cursor")
        if not self._tmhm_use():
            bail(self.last_menu_reason or "USE flow failed")
        # the D-scan wraps, so every row gets visited wherever it starts
        for _ in range(8):
            if self._able_under_cursor():
                break
            self.press("D:4 .:15")
        else:
            bail(f"no party member is ABLE to learn {move_name}")
        self.press("A:5 .:80")                        # choose the mon
        learned = self._walk_forget_menu(move_name, forget_move)
        # postcondition: overworld interactive again, move actually known
        if not self.close_menus():
            raise RuntimeError(f"teach_hm {move_name}: a menu is still "
                               "open after teaching")
        if not learned:
            raise RuntimeError(f"teach_hm {move_name}: teaching failed "
                               "verification")
        # Say WHO learned it. Returning None read as failure at a glance
        # ("teach HM01 to SPROUT: None" while CUT had in fact landed on
        # EMBER), and the answer matters: this teaches the first ABLE
        # member, not the one the caller had in mind.
        knower = self.field_moves().get(move_name.upper())
        log.info(f"  [teach_hm] {move_name} -> {knower}")
        return knower or True


    def tmhm_moves(self):
        """``{'TM01': 'DYNAMICPUNCH', ..., 'HM07': 'WATERFALL'}`` -- which
        move each TM/HM teaches, in TM/HM number order."""
        global _tmhm_table
        if _tmhm_table is None:
            from crystalagent.tactics import parse_tmhm_moves
            _tmhm_table = parse_tmhm_moves(paths.REPO_ROOT)
        return _tmhm_table

    def species_tmhm(self):
        """``{SPECIES: [MOVE_CONST, ...]}`` TM/HM learnsets (base stats)."""
        global _species_tmhm
        if _species_tmhm is None:
            from crystalagent.tactics import parse_species_tmhm
            _species_tmhm = parse_species_tmhm(paths.REPO_ROOT)
        return _species_tmhm

    def tmhm_stock(self):
        """``{'TM23': count}`` for every TM/HM actually held.

        TMs do not live in the item pockets _bag() reads: wTMsHMs is a
        flat count-per-TMNUM array (ram/wram.asm:3109), TM01..TM50 then
        HM01..HM07, which is also the order the pocket lists them in."""
        tags = list(self.tmhm_moves())
        bank, addr = self.emu.sym["wTMsHMs"]
        raw = self.emu.read((bank, addr), len(tags))
        return {tag: n for tag, n in zip(tags, raw) if n}

    def _tm_fail(self, reason):
        self.last_tm_reason = reason
        log.warning(f"  teach_tm: {reason}")
        return False

    def _resolve_tm(self, tm):
        """'TM23' | 'IRON TAIL' | 'IRON_TAIL' -> (tag, move display name),
        or (None, None)."""
        table = self.tmhm_moves()
        key = str(tm).strip().upper().replace(" ", "")
        tag = key if key in table else next(
            (t for t, mv in table.items()
             if norm_item(mv) == norm_item(key)), None)
        if tag is None:
            return None, None
        const = table[tag]
        # the ROM's display name for that move constant ('IRON_TAIL' ->
        # 'IRON TAIL'); compared normalised so spacing never matters
        want = norm_item(const)
        name = next((n for n in self.names.moves.values()
                     if norm_item(n) == want), const.replace("_", " "))
        return tag, name

    def _party_row(self, mon):
        """0-based party row of the member named `mon` -- NICKNAME first,
        then species, since a model may say either. ValueError on an
        unknown name: teaching the wrong mon is worse than stopping."""
        want = _norm_name(mon)
        party = game_state(self.emu, self.names)["party"]
        for slot, m in enumerate(party):
            if _norm_name(m.get("nickname") or "") == want:
                return slot
        for slot, m in enumerate(party):
            if _norm_name(m.get("name") or m.get("species") or "") == want:
                return slot
        raise ValueError(
            f"teach_tm: no party member named {mon!r} (party: "
            f"{[m.get('nickname') for m in party]})")

    def teach_tm(self, tm, mon, forget=None):
        """Teach a TM (or HM) to a NAMED party member. True only when the
        move is really on that mon afterwards.

        `tm` is a tag or the move it teaches ('TM23', 'IRON TAIL');
        `mon` is a nickname or species; `forget` names the move to delete
        when the mon already knows four (default: the move the list opens
        on, i.e. its oldest).

        Everything checkable is checked BEFORE a single button is pressed,
        because a refusal mid-flow leaves menus open (gotcha 7) and the
        game's own "not compatible" path just wastes the TM's turn:

          'unknown-tm'    no such TM/HM tag or move
          'not-in-bag'    wTMsHMs holds none of that TM
          'cannot-learn'  the species' tmhm learnset excludes the move
          'already-knows' that mon already has it

        An unknown `mon`, or a `forget` the mon does not know / an HM move
        (the game refuses to delete those), raises ValueError.
        """
        self.last_tm_reason = None
        tag, move_name = self._resolve_tm(tm)
        if tag is None:
            return self._tm_fail(f"unknown-tm: {tm!r} names no TM/HM")
        slot = self._party_row(mon)
        party = game_state(self.emu, self.names)["party"]
        entry = party[slot]
        label = entry.get("nickname") or entry.get("name")
        stock = self.tmhm_stock()
        if not stock.get(tag):
            return self._tm_fail(f"not-in-bag: no {tag} ({move_name}) held")
        const = self.tmhm_moves()[tag]
        learnset = self.species_tmhm().get(entry.get("name")) \
            or self.species_tmhm().get(entry.get("species")) or []
        if const not in learnset:
            return self._tm_fail(
                f"cannot-learn: {entry.get('name')} cannot learn "
                f"{move_name} ({tag})")
        known = [m.get("name") for m in entry.get("moves", [])]
        if move_name in known:
            return self._tm_fail(f"already-knows: {label} already has "
                                 f"{move_name}")
        if forget is not None:
            if forget.strip().upper() in self.HM_MOVES:
                raise ValueError(
                    f"teach_tm: the game refuses to delete HM move "
                    f"{forget!r}")
            if forget not in known:
                raise ValueError(
                    f"teach_tm: {label} does not know {forget!r} "
                    f"(knows: {known})")
        log.info(f"[teach_tm] {tag} {move_name} -> {label} (slot {slot})"
                 + (f", forgetting {forget}" if forget else ""))
        if not self._tmhm_pocket():
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "no TM/HM pocket")
        if not self._tmhm_row(tag, move_name):
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "no TM row")
        if not self._tmhm_use():
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "USE flow failed")
        if not self._party_cursor_to(slot + 1):
            self.close_menus()
            return self._tm_fail(
                f"target-miss: could not put the party cursor on row "
                f"{slot + 1} ({label})")
        if not self._able_under_cursor():
            self.close_menus()
            return self._tm_fail(
                f"not-able: the game reports {label} NOT ABLE to learn "
                f"{move_name}")
        self.press("A:5 .:80")                        # choose the mon
        learned = self._walk_forget_menu(move_name, forget, slot=slot)
        self.close_menus()
        if not learned:
            return self._tm_fail(f"not-learned: {label} does not know "
                                 f"{move_name} after the flow")
        self.last_tm_reason = "learned"
        log.info(f"  {label} learned {move_name}")
        return True

    def _party_cursor_to(self, row, max_steps=12):
        """Move the party-menu cursor to 1-based `row` using the live
        wMenuCursorY (the menu wraps, so press counts from an unknown
        start are meaningless). Returns True on arrival."""
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY")
            if cur == row:
                return True
            self.press("D:4 .:15" if cur < row else "U:4 .:15")
        return self.emu.read_u8("wMenuCursorY") == row

    def party_swap(self, row_a, row_b):
        """Swap two 1-based party slots via START -> POKéMON -> SWITCH.
        Verifies against wPartySpecies so a menu desync can't be mistaken
        for success. Returns True when the species really traded places."""
        from crystalagent.state import game_state
        before = [m["species"] for m in game_state(self.emu, self.names)["party"]]
        if row_a == row_b or max(row_a, row_b) > len(before):
            return False
        want = list(before)
        want[row_a - 1], want[row_b - 1] = want[row_b - 1], want[row_a - 1]

        for _ in range(3):
            self.press("START:4 .:45")
            if self.menu_open():
                break
            self.press(".:40")
        else:
            log.warning("  START menu did not open")
            return False
        # has_label() is a startswith test and POKéDEX also starts with
        # "POK", so steer by the row text instead of a label prefix.
        for _ in range(10):
            row = self.menu.cursor_row()
            if row and "MON" in row[1].upper() and "DEX" not in row[1].upper():
                self.press("A:4 .:25")
                break
            self.press("D:6 .:12")
        else:
            self.close_menus()
            log.info("  could not open the party menu")
            return False
        self.press(".:25")
        for row, label in ((row_a, "first"), (row_b, "second")):
            if not self._party_cursor_to(row):
                self.close_menus()
                log.info(f"  cursor never reached {label} row {row}")
                return False
            self.press("A:4 .:25")
            if label == "first":
                # slot menu: the mon's FIELD MOVES (CUT/SURF/STRENGTH/..)
                # list ABOVE the fixed STATS/SWITCH rows, so the row
                # count varies per mon -- steer by row TEXT, never by
                # position (wren pt6: blind counts fired Strength)
                if not self.select_menu_row("SWITCH", max_presses=8):
                    self.close_menus()
                    log.info("  SWITCH entry not found")
                    return False
                self.press(".:25")
        self.press(".:30")
        self.close_menus()
        after = [m["species"] for m in game_state(self.emu, self.names)["party"]]
        ok = after == want
        log.warning(f"  party_swap {row_a}<->{row_b}: {'ok' if ok else 'FAILED'} {after}",
              )
        return ok

    PC_LIST_STATE = 1

    PC_SUBMENU_STATE = 3

    PC_PROMPT_ROW = 16

    def _pc_fail(self, reason, exit_ui=True):
        self.last_pc_reason = reason
        if exit_ui:
            self._pc_exit()
        log.warning(f"  pc: {reason}")
        return False

    def _pc_state(self):
        return self.emu.read_u8("wJumptableIndex")

    def _pc_index(self):
        """0-based selection in the open PC list (WRAM, not the screen)."""
        return self.emu.read_u8("wBillsPC_CursorPosition") + \
            self.emu.read_u8("wBillsPC_ScrollPosition")

    def _pc_prompt(self):
        rows = self.emu.screen_text()
        return rows[self.PC_PROMPT_ROW].strip() \
            if len(rows) > self.PC_PROMPT_ROW else ""

    def _pc_list_up(self):
        """The DEPOSIT/WITHDRAW mon list is up and polling the joypad."""
        return self._pc_state() == self.PC_LIST_STATE and \
            "Choose a" in self._pc_prompt()

    def _pc_closed(self):
        """The PC session is over and the overworld owns input again.

        Neither half of this can be dropped. close_menus()/menu_open()
        alone report 'clean' with a box list still on screen (no cursor
        glyph, and the list's textbox is at row 15, not the row-12 one
        `textbox()` looks for); wScriptMode alone is useless here -- it
        reads 1 on a perfectly interactive overworld (live: the
        claude-indigo-plateau checkpoint)."""
        return not self._pc_list_up() and not self.menu_open()

    def _pc_exit(self, max_presses=12):
        """B out of the PC. B is the only safe key here (gotcha 13's
        shop lesson, one screen over): A on a list confirms a deposit."""
        for _ in range(max_presses):
            if self._pc_closed():
                break
            self.press("B:6 .:24")
        self.settle()
        return self._pc_closed()

    def box_list(self):
        """The current PC box, read out of SRAM -- ``{'box': n, 'count': k,
        'capacity': 20, 'mons': [{species, name, nickname, level}, ...]}``.

        Never touches the screen or a menu, so it is safe to call at any
        time (including before opening the PC, to see whether a deposit
        even fits) and it is authoritative: the WITHDRAW list paints this
        same order."""
        return box_state(self.emu, self.names)

    BOX_COUNT = 14

    def boxes(self):
        """Fill state of every PC box plus which one is ACTIVE --
        ``{'current': n, 'boxes': [{'box': i, 'count': k,
        'capacity': 20, 'full': bool}, ...]}``.

        Pure SRAM reads (no screen, no menus): the stored boxes live at
        ``sBox1Count``-``sBox14Count`` in SRAM banks 2-3, but the ACTIVE
        box's stored copy is stale until CHANGE BOX writes it back
        (engine/pokemon/bills_pc.asm GetBoxPointer), so its count comes
        from the live ``sBoxCount`` in bank 1 instead. A full active box
        silently bounces every ball throw ("The POKéMON BOX is full."),
        so a catching session should check this BEFORE hunting."""
        cur = (self.emu.read_u8("wCurBox") & 0x0F) + 1
        live = self.box_list()["count"]
        rows = []
        for i in range(1, self.BOX_COUNT + 1):
            if i == cur:
                k = live
            else:
                k = self.emu.read(self.emu.sym[f"sBox{i}Count"])
                if isinstance(k, (bytes, bytearray)):
                    k = k[0]
                if k == 0xFF:                      # never-initialized box
                    k = 0
            rows.append({"box": i, "count": k, "capacity": MONS_PER_BOX,
                         "full": k >= MONS_PER_BOX})
        return {"current": cur, "boxes": rows}

    def change_box(self, n=None):
        """Make box `n` (1-14) the ACTIVE box via Bill's PC CHANGE BOX;
        with `n` omitted, the first non-full box. True only when wCurBox
        really changed (or already matched). Needs a PC on this map.

        The flow saves the game (the engine's own "data will be saved"
        prompt is answered YES); refusals land in last_pc_reason:
        'bad-box' (out of range), 'no-space' (auto-pick with every box
        full), plus _pc_boot's reasons. An EXPLICIT full box is honored
        -- switching to one is how a boxed mon gets withdrawn; only the
        auto-pick avoids full boxes (they bounce catches, gotcha 30)."""
        st = self.boxes()
        if n is None:
            free = [b["box"] for b in st["boxes"] if not b["full"]]
            if not free:
                return self._pc_fail("no-space: all 14 boxes are full",
                                     exit_ui=False)
            n = st["current"] if st["current"] in free else free[0]
        if not 1 <= n <= self.BOX_COUNT:
            return self._pc_fail(f"bad-box: {n} is not 1-{self.BOX_COUNT}",
                                 exit_ui=False)
        if n == st["current"]:
            self.last_pc_reason = "already-current"
            return True
        # Open Bill's PC to its WITHDRAW/DEPOSIT/CHANGE BOX menu
        # (_pc_boot: walk under the terminal, screen-state driven --
        # gotcha 30).
        def box_menu(rows):
            return any("CHANGE BOX" in r for r in rows)
        if not self._pc_boot(box_menu):
            return False
        if not self.select_menu_row("CHANGE BOX", max_presses=8):
            return self._pc_fail("no-list: the CHANGE BOX row would not "
                                 "confirm")
        self.press(".:40")
        # Box chooser ("Choose a BOX."): glyph-cursor list of BOX1-BOX14.
        if not self.select_menu_row(f"BOX{n}",
                                    max_presses=self.BOX_COUNT + 4):
            return self._pc_fail(f"target-miss: BOX{n} never came under "
                                 f"the chooser cursor")
        self.press(".:30")
        # SWITCH / NAME / PRINT / QUIT submenu.
        if not self.select_menu_row("SWITCH", max_presses=6):
            return self._pc_fail("no-list: no SWITCH row after choosing "
                                 "the box")
        # Two save prompts follow, each behind its own text page ("data
        # will be saved. OK?", then "already a saved file… overwrite?")
        # and BOTH default their cursor to YES. The YES/NO boxes draw
        # OVER the chooser and share rows with it, which breaks
        # resolve_choice's geometry scrape (live: options came back as
        # ['BOX1','BOX2'] forever) -- so this is one place a paced A
        # loop is correct: every page advances on A, every default is
        # YES, and wCurBox flipping is the exit condition.
        f0 = self.emu.frame
        while self.emu.frame - f0 < 6000 and \
                (self.emu.read_u8("wCurBox") & 0x0F) + 1 != n:
            self.press("A:4 .:50")
        self._pc_exit()
        got = (self.emu.read_u8("wCurBox") & 0x0F) + 1
        if got != n:
            return self._pc_fail(f"switch-miss: current box is {got}, "
                                 f"wanted {n}", exit_ui=False)
        self.last_pc_reason = "switched"
        return True

    _NICK_KEYS = ("nickname", "nick")

    _SPECIES_KEYS = ("name", "species")

    @classmethod
    def _named_slot(cls, mon, entries):
        """Index of `mon` (nickname first, then species) in a list of
        party/box entries, or None. Nickname first because that is what a
        model says, species second because a box mon may be un-nicknamed."""
        want = _norm_name(mon)

        def match(entry, keys):
            return any(_norm_name(entry.get(k) or "") == want for k in keys)
        for keys in (cls._NICK_KEYS, cls._SPECIES_KEYS):
            for i, m in enumerate(entries):
                if match(m, keys):
                    return i
        return None

    def _pc_page(self, pred, presses=14):
        """Advance the PC's text pages with SINGLE A presses until
        `pred(rows)` holds.

        flush_dialog cannot do this job: cancelling the terminal's
        "Access whose PC?" menu leaves its ▶ painted behind the page that
        follows, and a stale glyph outside the box makes
        dialog_press_safe refuse every press (live: "BILLˢ PC accessed."
        never advanced). Only text pages sit between the rows this is
        asked to wait for, so an A press here cannot buy, teach or
        deposit anything."""
        for _ in range(presses):
            if pred(self.emu.screen_text()):
                return True
            self.press("A:4 .:40")
        return pred(self.emu.screen_text())

    def _pc_boot(self, pred, presses=14):
        """Walk under the nearest PC terminal, face it, and drive it by
        SCREEN STATE until `pred(rows)` holds (the caller's target menu).
        Handles the "turned on the PC" page and the terminal's
        BILL/<PLAYER>/OAK chooser, whose follow-up "What?" menu draws by
        ITSELF -- a blind A there lands on its default WITHDRAW row.

        talk_to is deliberately not used: on some Pokécenter layouts
        (live: Olivine) its approach pick lands a cell away and its
        dialog flush eats the whole terminal session (gotcha 30)."""
        if not self._pc_closed() and not self._pc_exit():
            return self._pc_fail("busy: a menu owns the screen and B "
                                 "would not clear it", exit_ui=False)
        cell = self._pc_tile()
        if cell is None:
            return self._pc_fail(
                f"no-pc: no COLL_PC ($93) tile on {self.map_name()} -- "
                f"stand in a Pokécenter or Bill's house "
                f"(find_tiles('pc'))", exit_ui=False)
        px, py = cell
        if self.pos()[2:] != (px, py + 1):
            if not self.goto(px, py + 1):
                return self._pc_fail(f"no-pc: could not stand under the "
                                     f"PC at {cell}", exit_ui=False)
        self.press("UP:2 .:10")
        for _ in range(presses):
            rows = self.emu.screen_text()
            if pred(rows):
                return True
            if any("BILL" in r and "PC" in r for r in rows):
                if not self.select_menu_row("BILL", max_presses=6):
                    return self._pc_fail("no-list: the BILL's PC row "
                                         "would not confirm")
                f0 = self.emu.frame
                while self.emu.frame - f0 < 600 and \
                        not pred(self.emu.screen_text()):
                    self.press(".:15")
                continue
            self.press("A:5 .:40")
        if pred(self.emu.screen_text()):
            return True
        return self._pc_fail(
            f"no-list: BILL's PC never drew the target menu "
            f"(row 14: {self.emu.screen_text()[14].strip()!r})")

    def _pc_open(self, action):
        """Open BILL's PC and take `action` ('DEPOSIT' or 'WITHDRAW'),
        leaving its mon list up. Reuses a list that is already up (the
        flow re-arms itself after every confirm, so recovery from a bad
        deposit is a second target on the SAME list)."""
        if self._pc_list_up():
            return True

        def box_menu(rows):
            return any("WITHDRAW" in r for r in rows)
        # _pc_boot walks under the terminal and drives the "turned on"
        # page + BILL chooser by screen state (talk_to mis-approaches on
        # some layouts -- gotcha 30); box_menu's rows are the real gate.
        if not self._pc_boot(box_menu):
            return False
        if not self.select_menu_row(action, max_presses=8):
            return self._pc_fail(f"no-list: the {action} row would not "
                                 f"confirm")
        f0 = self.emu.frame
        while self.emu.frame - f0 < 900:
            if self._pc_list_up():
                return True
            self.press(".:20")
        return self._pc_fail(f"no-list: {action} never drew a mon list "
                             f"(prompt: {self._pc_prompt()!r})")

    def _pc_tile(self):
        """The nearest PC tile on this map, or None. Journal #45 had to
        find this by hand because find_tiles had no word for $93."""
        here = self.pos()[2:]
        cells = self.find_tiles("pc")
        if not cells:
            return None
        return min(cells, key=lambda c: abs(c[0] - here[0])
                   + abs(c[1] - here[1]))

    def _pc_cursor_to(self, index, expect=None, max_steps=30):
        """Put the PC list's selection on 0-based `index`, verified against
        WRAM after every single press. `expect` (a species name) is
        cross-checked against the info panel PCMonInfo redraws, which is
        the only thing on screen that tracks this cursor (#73)."""
        for _ in range(max_steps):
            cur = self._pc_index()
            if cur == index:
                break
            self.press("D:4 .:18" if cur < index else "U:4 .:18")
        if self._pc_index() != index:
            return self._pc_fail(
                f"target-miss: the PC cursor stopped at {self._pc_index()} "
                f"short of {index}")
        if expect:
            self.press(".:24")            # let PCMonInfo finish repainting
            shown = self.menu.pc_info()["name"]
            if shown.upper() != str(expect).upper():
                return self._pc_fail(
                    f"target-miss: index {index} shows {shown!r}, expected "
                    f"{expect!r} -- the list is not what memory says")
        return True

    def _pc_confirm(self, action):
        """One A press to open the DEPOSIT/STATS/RELEASE/CANCEL box, then
        the labelled row. That box IS glyph-driven (a STATICMENU_CURSOR
        VerticalMenu, bills_pc.asm:228), so select_menu_row can read it --
        unlike the list behind it."""
        self.press("A:4 .:40")
        f0 = self.emu.frame
        while self.emu.frame - f0 < 600:
            if self._pc_state() == self.PC_SUBMENU_STATE:
                break
            self.press(".:15")
        else:
            return self._pc_fail(
                f"no-list: the {action} submenu never opened "
                f"(jumptable {self._pc_state()})")
        self.press(".:20")
        if not self.select_menu_row(action, max_presses=6):
            return self._pc_fail(f"no-list: no {action} row in the submenu")
        self.press(".:60")
        return True

    def deposit(self, mon):
        """Put the party member named `mon` (nickname or species) into the
        current box. True only when observe()['party'] really lost it.

        Refuses BEFORE pressing anything when the game would refuse or the
        result would be a whiteout risk; `last_pc_reason` says which:

          'no-such-mon'  nobody in the party answers to that name
          'last-mon'     it is the only mon that can fight
          'box-full'     the current box already holds 20
          'holds-mail'   the engine refuses to box a mail carrier

        Exactly ONE mon moves per call: the deposit list re-arms itself on
        the next party member, and a blind confirm loop empties the party
        (#72)."""
        self.last_pc_reason = None
        party = self.observe()["party"]
        slot = self._named_slot(mon, party)
        if slot is None:
            return self._pc_fail(
                f"no-such-mon: no party member named {mon!r} "
                f"(party: {[m['nick'] for m in party]})", exit_ui=False)
        entry = party[slot]
        if sum(1 for m in party if not m.get("egg")) <= 1 \
                and not entry.get("egg"):
            return self._pc_fail(
                f"last-mon: {entry['nick']} is the only mon that can fight "
                f"-- the engine refuses ('It's your last ᴾᴹ!')",
                exit_ui=False)
        box = self.box_list()
        if box["count"] >= box["capacity"]:
            return self._pc_fail(
                f"box-full: box {box['box']} already holds "
                f"{box['count']}/{box['capacity']} -- CHANGE BOX first",
                exit_ui=False)
        # observe()['party'] carries no held item; game_state does, and
        # BillsPC_CheckMail_PreventBlackout refuses a mail carrier outright
        held = (game_state(self.emu, self.names)["party"][slot].get("item")
                or "")
        if "MAIL" in held.upper():
            return self._pc_fail(f"holds-mail: {entry['nick']} carries "
                                 f"{held}", exit_ui=False)
        log.info(f"[deposit] {entry['nick']} ({entry['species']} "
                 f"L{entry['level']}) -> box {box['box']} "
                 f"({box['count']}/{box['capacity']})")
        if not self._pc_open("DEPOSIT"):
            return False
        if not self._pc_cursor_to(slot, expect=entry["species"]):
            return False
        if not self._pc_confirm("DEPOSIT"):
            return False
        return self._pc_settled("deposited", party, box,
                                gone=entry["nick"], delta=-1)

    def withdraw(self, mon):
        """Take the mon named `mon` (nickname or species) out of the
        current box and into the party. True only when
        observe()['party'] really gained it.

        `last_pc_reason`: 'not-in-box' (nothing in the box answers to that
        name -- box_list() shows what does), 'party-full' (six already).
        One mon per call, same reason as deposit."""
        self.last_pc_reason = None
        party = self.observe()["party"]
        box = self.box_list()
        index = self._named_slot(mon, box["mons"])
        if index is None:
            return self._pc_fail(
                f"not-in-box: nothing named {mon!r} in box {box['box']} "
                f"(holds: {[m['nickname'] for m in box['mons']]})",
                exit_ui=False)
        if len(party) >= 6:
            return self._pc_fail(
                "party-full: six already -- deposit one first", exit_ui=False)
        entry = box["mons"][index]
        log.info(f"[withdraw] {entry['nickname']} ({entry['name']} "
                 f"L{entry['level']}) <- box {box['box']} slot {index + 1}")
        if not self._pc_open("WITHDRAW"):
            return False
        if not self._pc_cursor_to(index, expect=entry["name"]):
            return False
        if not self._pc_confirm("WITHDRAW"):
            return False
        return self._pc_settled("withdrawn", party, box,
                                gained=entry["nickname"], delta=+1)

    def _pc_settled(self, done, party0, box0, gone=None, gained=None,
                    delta=0):
        """Did EXACTLY the one intended mon move? Judged on observed state
        (the live party plus the SRAM box), never on dialog text -- and
        loudly when more than one moved, because that is the #72 wound and
        a caller must not learn about it from a level-up log 20 minutes
        later."""
        self.press(".:60")
        party1 = self.observe()["party"]
        box1 = self.box_list()
        moved = len(party1) - len(party0)
        nicks0 = [m["nick"] for m in party0]
        nicks1 = [m["nick"] for m in party1]
        if moved != delta:
            if moved and abs(moved) > abs(delta):
                self._pc_exit()
                self.last_pc_reason = "over-applied"
                raise RuntimeError(
                    f"pc {done}: {abs(moved)} mons moved, not 1 "
                    f"({nicks0} -> {nicks1}) -- the PC list re-armed and "
                    f"something pressed A twice (FUCK_I_MESSED_UP.md #72)")
            return self._pc_fail(
                f"unchanged: party {nicks0} -> {nicks1}, box "
                f"{box0['count']} -> {box1['count']} (prompt: "
                f"{self._pc_prompt()!r})")
        if gone and _norm_name(gone) in [_norm_name(n) for n in nicks1]:
            return self._pc_fail(f"unchanged: {gone} is still in the party")
        if gained and _norm_name(gained) not in [_norm_name(n)
                                                 for n in nicks1]:
            return self._pc_fail(f"unchanged: {gained} did not join the "
                                 f"party")
        self._pc_exit()
        self.last_pc_reason = done
        log.info(f"  {done}: party {nicks0} -> {nicks1}, box "
                 f"{box0['count']} -> {box1['count']}")
        return True

    def use_cut(self, tree_x, tree_y, label="", forget_move=None):
        """Cut down the small tree at (tree_x, tree_y) on the current map:
        teaches HM01 CUT via the pack flow if nobody knows it yet (deleting
        `forget_move` if the learner is at four moves), walks to a standable
        cell beside the tree, faces it, and uses START -> POKéMON -> mon ->
        field-move CUT. Verifies the tree's collision actually cleared and
        steps onto its cell."""
        def scr():
            return "".join(self.emu.screen_text()).upper()
        if self.battle():
            self.fight()
        name = self.map_name()
        grid = self.nav.grid(name)
        hgt, wid = len(grid), len(grid[0])
        if not (0 <= tree_x < wid and 0 <= tree_y < hgt) or \
                grid[tree_y][tree_x] != self._CUT_TREE_BYTE:
            raise RuntimeError(f"use_cut: ({tree_x}, {tree_y}) is not a "
                               f"cuttable tree on {name}")
        knows, knower = self._party_knows("CUT")
        if not knows:
            log.info("  no one knows CUT; teaching HM01")
            self._teach_hm01(forget_move=forget_move)
            knows, knower = self._party_knows("CUT")
        # approach cell: any standable neighbour we can actually reach,
        # facing back toward the tree
        inv = {"U": "D", "D": "U", "L": "R", "R": "L"}
        cands = []
        for d, (dx, dy) in STEP.items():
            ax, ay = tree_x + dx, tree_y + dy
            if 0 <= ax < wid and 0 <= ay < hgt and \
                    self._standable(name, (ax, ay)):
                cands.append(((ax, ay), inv[d]))
        placed = False
        for round_ in range(3):          # wandering NPCs can blockade the
            for (ax, ay), face in cands: # only path; pause and retry
                if self.goto(ax, ay, label or "use_cut approach"):
                    self.press(f"{face}:4 .:10")
                    placed = True
                    break
            if placed:
                break
            log.info("  approach blocked (wandering NPC?); pausing")
            self.press(".:60 .:60 .:60 A:4 .:30")
        if not placed:
            raise RuntimeError(
                f"use_cut: no reachable approach beside the tree "
                f"({tree_x}, {tree_y})")

        # START -> POKEMON -> (knower or first mon) -> field-move CUT row
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            raise RuntimeError("use_cut: START menu never opened")
        # label-driven: the START menu REMEMBERS its last cursor slot, so
        # a fixed press count opens the wrong entry after any PACK visit.
        # 'POKé' alone also matches POKéDEX -- include the M.
        if not self.menu.select_label("POKéM"):
            self.close_menus()
            raise RuntimeError("use_cut: POKéMON entry not found in "
                               "START menu")
        if not self._wait_screen(lambda s: "CANCEL" in s):
            self.close_menus()
            raise RuntimeError("use_cut: party list never opened")
        if not self._party_cursor_to((knower or 0) + 1):
            self.close_menus()
            raise RuntimeError("use_cut: party cursor never reached the "
                               "CUT knower")
        # confirm-until-open: the first A can land during menu setup and
        # get swallowed (gotcha 2)
        sub = False
        for _ in range(6):
            self.press("A:6 .:40")
            if self._wait_screen(lambda s: "STATS" in s and "SWITCH" in s,
                                 frames=80):
                sub = True
                break
        if not sub:
            self.close_menus()
            raise RuntimeError("use_cut: POKéMON submenu never opened")
        # the party list stays visible behind the submenu box and field
        # moves sit ABOVE STATS/SWITCH, so steer by row TEXT (wren pt6)
        if not self.select_menu_row("CUT", confirm=False, max_presses=10):
            # a field move refused (wrong mon, indoors, "Can't use that
            # here") leaves the party menu + submenu OPEN, and an open
            # menu eats every movement input afterwards (gotcha 7). Every
            # field-move failure path must close its own UI.
            self.close_menus()
            raise RuntimeError("use_cut: CUT row missing from the "
                               "POKéMON submenu")
        self.press("A:6 .:50")                        # use CUT
        for _ in range(12):
            s = scr()
            if "YES" in s and "NO" in s:
                self.press("A:5 .:45")                # confirm cut
            elif not self.textbox():
                break
            else:
                self.press("A:4 .:45")
        self.settle()
        # postcondition: nothing modal left on screen (a stray menu here
        # gets baked into the next save and eats all movement input)
        if not self.close_menus():
            raise RuntimeError("use_cut: a menu is still open after CUT")
        # verify by walking onto the former tree cell (the static grid
        # still shows $12 -- cut trees are swapped only in the engine's
        # block memory)
        r = self._step(face)
        if self.pos()[2:] != (tree_x, tree_y):
            raise RuntimeError(
                f"use_cut: tree at {(tree_x, tree_y)} still standing after "
                f"CUT (step {r} -> {self.pos()[2:]})")
        log.info(f"  [cut] tree at {(tree_x, tree_y)} removed; stepped {r} "
              f"-> {self.map_name()} {self.pos()[2:]}")
        return True

    OW_FIELD_MOVES = {
        # move: (tile kind faced, badge the engine checks)
        "CUT": ("cut-tree", "HIVE"),
        "WATERFALL": ("waterfall", "RISING"),
        "WHIRLPOOL": ("whirlpool", "GLACIER"),
    }

    FACING_BYTE = {"D": 0x0, "U": 0x4, "L": 0x8, "R": 0xC}

    def _field_fail(self, reason):
        """Field-move refusal. Closes menus on the way out: a field move
        that fails leaves its menu open, and an open menu eats all
        movement input (AGENTS.md gotcha 17)."""
        self.last_field_reason = reason
        self.close_menus()
        log.warning(f"  field move: {reason}")
        return False

    def facing(self):
        """Which way the player is facing: 'U' | 'D' | 'L' | 'R'."""
        raw = self.emu.read_u8("wPlayerDirection") & 0xC
        return {v: k for k, v in self.FACING_BYTE.items()}.get(raw, "?")

    def face(self, mv):
        """Turn to face `mv` without stepping (a short directional press
        against anything turns in place) and confirm via
        wPlayerDirection. True when we really face that way."""
        for _ in range(4):
            if self.facing() == mv:
                return True
            self.press(f"{mv}:6 .:12")
        return self.facing() == mv

    def use_field_move(self, move, facing=None):
        """Use a water HM (WATERFALL, WHIRLPOOL) on the tile we are
        FACING, through the overworld A press. True only when the world
        actually changed -- the waterfall moved us, or the whirlpool is
        gone from the live map.

        `facing` ('U'/'D'/'L'/'R') turns first. Everything checkable is
        checked before the A press; `last_field_reason` says which:

          'unknown-move'  not an A-dispatched field move
          'no-knower'     nobody in the party knows it (field_moves())
          'no-badge'      the engine's badge gate would refuse
          'no-facing'     could not turn to the requested direction
          'wrong-tile'    the faced cell is not that obstacle
          'no-prompt'     the A press produced no "use it?" question
          'unchanged'     the prompt was answered and nothing moved
        """
        self.last_field_reason = None
        move = str(move).strip().upper()
        spec = self.OW_FIELD_MOVES.get(move)
        if spec is None:
            return self._field_fail(
                f"unknown-move: {move!r} is not an A-dispatched field move "
                f"({'/'.join(self.OW_FIELD_MOVES)})")
        want_kind, badge = spec
        knower = self.field_moves().get(move)
        if not knower:
            return self._field_fail(f"no-knower: nobody in the party knows "
                                    f"{move}")
        badges = game_state(self.emu, self.names)["player"]["johto_badges"]
        if badge and badge not in badges:
            return self._field_fail(f"no-badge: {move} needs the {badge} "
                                    f"badge (have: {badges})")
        if facing and not self.face(facing):
            return self._field_fail(f"no-facing: could not turn {facing} "
                                    f"(facing {self.facing()})")
        mv = self.facing()
        if mv not in STEP:
            return self._field_fail(f"no-facing: wPlayerDirection reads "
                                    f"{self.emu.read_u8('wPlayerDirection'):#04x}")
        x, y = self.pos()[2:]
        dx, dy = STEP[mv]
        target = (x + dx, y + dy)
        kind = self.tile_at(*target)
        if kind != want_kind:
            return self._field_fail(
                f"wrong-tile: facing {mv} at {(x, y)} the cell {target} is "
                f"{kind!r}, not {want_kind!r}")
        log.info(f"[{move.lower()}] {knower} at {(x, y)} facing {mv} -> "
                 f"{target}")
        self.press("A:4 .:40")
        prompted = False
        for _ in range(14):
            rows = self.emu.screen_text()
            if Menus.has_label(rows, "YES"):
                prompted = True
                self.press("A:5 .:60")
            elif self.textbox():
                self.press("A:4 .:45")
            else:
                break
        self.settle()
        if self.pos()[2:] != (x, y):              # waterfall climbed
            log.info(f"  [{move.lower()}] moved {(x, y)} -> "
                     f"{self.pos()[2:]}")
            self.last_field_reason = "used"
            return True
        self.sync_grid()                          # whirlpool dissolved
        if self.tile_at(*target) != want_kind:
            log.info(f"  [{move.lower()}] {target} is now "
                     f"{self.tile_at(*target)!r}")
            self.last_field_reason = "used"
            return True
        if not prompted:
            return self._field_fail(
                f"no-prompt: A at {(x, y)} facing {mv} never asked to use "
                f"{move} (row 14: {self.emu.screen_text()[14].strip()!r})")
        return self._field_fail(
            f"unchanged: {move} was confirmed but {target} is still "
            f"{self.tile_at(*target)!r} and we are still at {(x, y)}")

    def waterfall(self, facing="U"):
        """Climb the waterfall above us (HM07). Waterfalls only go UP --
        CheckMapCanWaterfall requires FACE_UP."""
        return self.use_field_move("WATERFALL", facing)

    def whirlpool(self, facing=None):
        """Dissolve the whirlpool we are facing (HM06)."""
        return self.use_field_move("WHIRLPOOL", facing)

    def cut(self, x=None, y=None, facing=None):
        """CUT the tree at (x, y) -- or the one we are facing (HM01).

        Cut trees are `COLL_CUT_TREE` ($12): a WALL to the pathfinder, so
        a route that needs one is simply "no path" (live: Ilex Forest's
        north exit, with HM01 in the bag and CUT on the lead). With
        coordinates this routes adjacent, faces the tree and presses A;
        the opened cell is then pushed into nav (`set_cell`) so the very
        next `goto` can plan through it. `last_field_reason` explains
        every False, including `no-tree` when nothing adjacent is one."""
        if x is not None and y is not None:
            target = (int(x), int(y))
            kind = self.tile_at(*target)
            if kind != "cut-tree":
                return self._field_fail(
                    f"wrong-tile: {target} is {kind!r}, not a cut tree")
            here = self.pos()[2:]
            if abs(here[0] - target[0]) + abs(here[1] - target[1]) != 1:
                spot = next((c for c in ((target[0], target[1] + 1),
                                         (target[0], target[1] - 1),
                                         (target[0] - 1, target[1]),
                                         (target[0] + 1, target[1]))
                             if self._standable(self.map_name(), c)
                             and self.goto(*c, f"approach tree {target}")),
                            None)
                if spot is None:
                    return self._field_fail(
                        f"no-approach: nothing walkable next to {target} "
                        f"(last goto: {self.last_goto_reason})")
                here = self.pos()[2:]
            facing = {(0, 1): "D", (0, -1): "U", (1, 0): "R",
                      (-1, 0): "L"}[(target[0] - here[0],
                                     target[1] - here[1])]
        elif facing is None:
            here = self.pos()[2:]
            facing = next((mv for mv, (dx, dy) in STEP.items()
                           if self.tile_at(here[0] + dx,
                                           here[1] + dy) == "cut-tree"),
                          None)
            if facing is None:
                return self._field_fail(
                    f"no-tree: no cut tree next to {here}")
        ok = self.use_field_move("CUT", facing)
        if ok:
            # use_field_move already ran sync_grid(), which patches nav
            # from the LIVE block map -- do not hand-write the cell. A cut
            # tree REGROWS when the map is re-entered, and a hand-written
            # override made nav believe in a gap that was not there:
            # ROUTE_35 (17,6) storm-blocked twenty replans on the way back.
            self.sync_grid()
        return ok

    def _pocket_select(self, idx, item_name, max_steps=40):
        """Steer the items-pocket cursor to absolute index `idx` and
        confirm with A. The pocket REMEMBERS its cursor between opens
        (pack.asm restores wItemsPocketCursor/wItemsPocketScrollPosition
        into the scrolling menu), so a fresh open can start mid-list:
        top-of-list screen scrapes miss and DOWN-only walks can never
        climb back up (leg-2 'no potion visible' with 2 in the bag).
        Navigate on the live WRAM index (wMenuScrollPosition +
        wMenuCursorY) in BOTH directions, then verify the highlighted
        row's TEXT really is the item before pressing A (wren pt6:
        select_menu_row -- _item_row_matches normalizes BOTH sides,
        case/space/hyphen/POKe blind, quantity-digit and edge-clip
        tolerant, and the column-band cursor pick ignores stale ▷/▶
        leftovers that shadowed 'SUPER POTION' in wren pt4)."""
        want = norm_item(item_name)
        last, stuck = None, 0
        cur = None
        for _ in range(max_steps):
            cur = self.menu.scroll_abs()
            if cur == idx:
                break
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return self._menu_fail(
                    f"pocket_select({item_name}): cursor pinned at {cur} "
                    f"short of row {idx} -- list edge or wrong menu")
            last = cur
            self.press("D:6 .:4" if cur < idx else "U:6 .:4")
        else:
            return self._menu_fail(
                f"pocket_select({item_name}): stopped at {cur} after "
                f"{max_steps} steps, wanted row {idx}")
        self.press(".:10")      # let the row repaint before scraping
        # text-targeted verify + confirm: the helper re-checks the row
        # under the ACTIVE cursor and can correct a small WRAM/screen
        # disagreement by text -- but never blind-A's a mismatched row
        if getattr(self.menu, "select_row_text", None) is None and \
                hasattr(self.menu, "cursor_row"):
            # older Menus / duck-typed fakes: verify the highlighted row's
            # text directly (pre-pt6 algorithm), rescanning the visible
            # rows for the ACTIVE glyph when a stale leftover shadows it
            row = self.menu.cursor_row()
            texts = [row[1] if isinstance(row, tuple) else row]
            texts += [l for l in self.emu.screen_text() if "\u25b6" in l]
            if any(_item_row_matches(t.replace("\u25b6", " "), want)
                   for t in texts if t):
                self.press("A:6 .:18")
                return True
        elif self.select_menu_row(item_name, max_presses=4,
                                  match=lambda t: _item_row_matches(t, want)):
            return True
        # WRAM/screen disagree: never blind-A
        return self._menu_fail(
            f"pocket_select({item_name}): row mismatch (norm {want}), "
            f"cursor row {self.menu.cursor_row()!r}"
            + (f"; {self.menu.last_reason}"
               if getattr(self.menu, "last_reason", None) else ""))

    def _party_target(self, slot, max_steps=12):
        """Steer the field party menu to row `slot` (0-based; eggs count
        as rows) and confirm with A.

        Same discipline as battle.py's _party_row_select, and
        BIDIRECTIONAL for the same reason: InitPartyMenuWithCancel
        restores wPartyMenuCursor into wMenuCursorY
        (engine/pokemon/party_menu.asm:624), so a fresh open starts on
        whatever row was picked LAST -- a DOWN-only walk can never climb
        back to slot 0 -- and REVIVE's fainted-target flow opens on the
        first ABLE mon.

        Position is wMenuCursorY (1-based), the row PartyMenuSelect
        itself branches on. The party list is a 2D menu
        (PartyMenu2DMenuData through Load2DMenuData), so
        wMenuScrollPosition is NOT its position here -- it still holds
        the item pocket's scroll offset, which is why scroll_abs must
        never be used for this list."""
        # gotcha 2: the frame the list is drawn its input loop is not
        # running yet, so the first D/U (or a same-row A) is swallowed --
        # live evidence: a D press left wMenuCursorY unchanged at 1.
        self.press(".:16")
        last, stuck = None, 0
        cur = None
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY") - 1
            if cur == slot:
                self.press("A:6 .:18")
                return True
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return self._menu_fail(
                    f"party_target({slot}): cursor pinned at row {cur} -- "
                    f"wrong menu or list edge")
            last = cur
            self.press("D:6 .:6" if cur < slot else "U:6 .:6")
        return self._menu_fail(f"party_target({slot}): stopped at row {cur} "
                               f"after {max_steps} steps")

    def _items_pocket_by_screen(self):
        """Fallback pack detection when goto_pocket's wJumptableIndex gate
        fails (wren pt6: field context can leave a non-pocket value there
        while the pack is plainly drawn). Steers by the drawn pocket
        banner: the pockets cycle ITEM <- BALL <- KEY <- TM on L, so at
        most 3 presses reach ITEM POCKET. A pack screen with an unreadable
        banner but visible 'x N' quantity rows counts as open --
        _pocket_select's row verification is the safety net for a wrong
        pocket. Returns True when the ITEMS pocket is (best-evidence) up."""
        for _ in range(4):
            rows = self.emu.screen_text()
            banner = _pack_pocket_banner(rows)
            if banner == "ITEM POCKET":
                log.info("  pack open on screen despite jumptable "
                         "mismatch; proceeding")
                return True
            if banner is None:
                if _pack_quantity_rows(rows):
                    log.info("  pack quantity rows on screen despite "
                             "jumptable mismatch; proceeding")
                    return True
                # nothing pack-like drawn: real miss
                return self._menu_fail(
                    "items_pocket: no pack banner and no quantity rows "
                    "on screen")
            self.press("L:4 .:12")  # cycle pockets toward ITEM POCKET
        return self._menu_fail(
            f"items_pocket: pocket banner still {banner!r} after 4 "
            f"L presses")

    def _start_menu_pack_row(self):
        """Get the START menu open with its PACK row drawn, and say so.
        Idempotent: a START menu left open by an earlier failure counts as
        already there (pressing START again would only close it)."""
        def _pack_row(s):
            return "PACK" in s
        if _pack_row("".join(self.emu.screen_text()).upper()):
            return True
        if self.menu_open():
            self.close_menus()      # a stray menu would eat the START press
        self.press("START:4 .:25")
        if self._wait_screen(_pack_row, 120):
            return True
        # Post-warp the START press sometimes lands during the fade;
        # blind D/A presses here WALK THE PLAYER (once onto a ladder).
        # Gotcha 2: the menu input loop isn't running the frame the menu
        # is drawn -- settle, drain stragglers, retry ONCE.
        log.info("  START menu slow to open; settling and retrying")
        self.settle()
        if self.textbox():
            self.flush_dialog()
        self.press("START:4 .:25")
        if self._wait_screen(_pack_row, 120):
            return True
        return self._menu_fail("start_menu: no PACK row drawn after two "
                               "START presses")

    _PACK_STATES = (2, 4, 6, 8)

    def _pack_up(self, rows=None):
        """Is the pack REALLY drawn? The jumptable's pocket state is the
        primary signal; field context can leave it stale, in which case
        the drawn pocket banner or the 'x N' quantity column proves it
        (wren pt6)."""
        if self.emu.read_u8("wJumptableIndex") in self._PACK_STATES:
            return True
        rows = self.emu.screen_text() if rows is None else rows
        return _pack_pocket_banner(rows) is not None or \
            bool(_pack_quantity_rows(rows))

    def _open_pack(self, max_confirms=3):
        """START -> PACK -> items pocket, with the pack open PROVED.

        This is the root cause of the pt10 field-item failures
        (`use_item` returning False with the bag untouched while the same
        items worked through the battle pack). Menus.select_label
        confirms a row with a 2-frame A and reports success from the
        CURSOR GLYPH alone -- it never looks at whether the pack opened.
        On the frames right after the START menu is drawn its input loop
        is not running yet (gotcha 2), so that A is swallowed on some
        frame parities and not others; live proof: two calls made from
        byte-identical savestates, one opened the pack and one left the
        START menu sitting there. goto_pocket then burned its whole
        budget on wJumptableIndex 128 (the START menu), the screen
        fallback saw no pocket banner and no quantity rows, and use_item
        returned False with NO log line at all -- leaving the START menu
        OPEN, which silently eats the caller's next input (gotcha 7), so
        the next call's START press merely closed it. That is the
        alternating success/failure the live log shows for identical
        calls.

        The fix is to retry the CONFIRM until the pack is verifiably up
        (jumptable pocket state, or the drawn pocket banner / quantity
        column when field context leaves the jumptable stale -- wren
        pt6). Re-pressing A on an already-open pack only re-opens the
        item submenu, which _pocket_select re-drives, so it is safe.

        The confirm now goes through select_label's `expect` gate, so the
        primitive's own answer means "the pack is up" -- and when it is
        not, the retry loop below is what recovers."""
        if not self._start_menu_pack_row():
            why = self.last_menu_reason or "START menu did not open"
            return self._menu_fail(f"open_pack: {why}")
        self.press(".:20")          # gotcha 2: let the input loop start
        if not self._confirm_label("PACK", self._pack_up, max_presses=8):
            reason = (getattr(self.menu, "last_reason", None)
                      or self.last_menu_reason or "PACK confirm unverified")
            if "state not reached" not in reason:
                return self._menu_fail(f"open_pack: {reason}")
            log.info(f"  {reason}; retrying the confirm")
        for _ in range(max_confirms):
            if goto_pocket(self.menu, "items") or \
                    self._items_pocket_by_screen():
                return True
            self.press("A:8 .:24")      # swallowed confirm: press again
        return self._menu_fail(
            f"open_pack: items pocket never came up in {max_confirms} "
            f"confirms")

    def _field_ui_clear(self):
        """Nothing modal is left on screen AND the pack's own jumptable is
        out of its pocket states (cancel_pack's gate, read directly so
        this needs no Menus)."""
        if self.emu.read_u8("wJumptableIndex") in self._PACK_STATES:
            return False
        return _field_clear(self.emu.screen_text())

    def _exit_field_ui(self, max_frames=1800):
        """B out of every field UI layer -- item message, party target
        list, pack, START menu -- until the overworld is interactive.

        Every use_item exit runs this. The old failure paths did
        `cancel_pack(); return False`, which is jumptable-gated and so
        left a stray START menu open on exactly the swallowed-A failure
        it was reporting; that menu then ate the caller's movement input
        (gotcha 7). B is also the safe key after a success: the item's
        "recovered NN HP!" prompt takes A or B, and an A drops straight
        back onto the target list where it would spend a SECOND item.

        Ends on a settling pause: without it the overworld has not
        re-latched input when the caller (or the next use_item) presses
        START, which is eaten and costs a whole retry cycle."""
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames and not self._field_ui_clear():
            self.press("B:6 .:14")
        clear = self._field_ui_clear()
        self.press(".:30")
        return clear

    def _item_fail(self, reason, message, exit_ui=True):
        """The one exit for every use_item failure: log it, record the
        machine-readable reason on self.last_item_reason, and put the
        field back."""
        self.last_item_reason = reason
        log.info(f"  {message}")
        if exit_ui:
            self._exit_field_ui()
        return False

    def _party_slot(self, mon):
        """0-based party row of the member NICKNAMED `mon`, so callers
        stop hand-counting slots (and stop miscounting them after a
        party_swap). Comparison is case/space-blind; eggs are addressable
        because they occupy a row. Raises ValueError on an unknown name --
        silently healing the wrong mon is worse than stopping."""
        want = _norm_name(mon)
        party = game_state(self.emu, self.names)["party"]
        for slot, m in enumerate(party):
            if _norm_name(m.get("nickname") or "") == want:
                return slot
        raise ValueError(
            f"use_item: no party member named {mon!r} "
            f"(party: {[m.get('nickname') for m in party]})")

    def use_item(self, item_name, target_slot=_UNSET, field=True, *,
                 mon=None):
        """Use an item from the pack outside battle on party member
        `target_slot` (0-based) -- or on the member NICKNAMED `mon`,
        resolved against the live party. `target_slot` and `mon` are
        mutually exclusive (ValueError if both are given).

        True ONLY on a bag decrement: the menus can flow perfectly while
        a swallowed A used nothing. Every outcome also lands a
        machine-readable diagnosis on self.last_item_reason:

          'used'           the item was consumed
          'no-effect'      the ENGINE refused it (_ItemWontHaveEffectText:
                           full-HP unstatused target, POTION on a fainted
                           mon) -- a legitimate no-op that consumed
                           nothing, never a mechanical failure
          'not-in-bag' | 'no-pack' | 'pocket-miss' | 'no-use-option' |
          'target-miss' | 'not-consumed'   mechanical failures
        """
        if mon is not None:
            if target_slot is not _UNSET:
                raise ValueError(
                    "use_item: pass target_slot OR mon, not both")
            target_slot = self._party_slot(mon)
        elif target_slot is _UNSET:
            target_slot = 0
        e = self.emu
        self.last_item_reason = None
        # Which pocket holds it? Key items live in their own flat list and
        # used to be invisible here ('not-in-bag' for a SQUIRTBOTTLE that
        # observe() could see -- FUCK_I_MESSED_UP.md #23).
        pocket = "items"
        idx = bag_item_index(e, self.names, item_name, "items")
        if idx is None:
            idx, pocket = bag_item_index(e, self.names, item_name, "key"), "key"
        if idx is None:
            idx, pocket = (bag_item_index(e, self.names, item_name, "balls"),
                           "balls")
        if idx is None:
            return self._item_fail("not-in-bag", f"no {item_name} in bag",
                                   exit_ui=False)
        if not self._open_pack():
            return self._item_fail(
                "no-pack", f"could not open the pack for {item_name}")
        if pocket != "items" and not goto_pocket(self.menu, pocket):
            return self._item_fail(
                "pocket-miss", f"could not reach the {pocket} pocket for "
                f"{item_name}")
        before = (bag_quantity(e, self.names, item_name)
                  if pocket != "key" else 1)
        if not self._pocket_select(idx, item_name):
            return self._item_fail(
                "pocket-miss",
                f"could not put the pocket cursor on {item_name}")
        # item submenu (USE/GIVE/TOSS/QUIT) pops up after a beat
        if not self.menu.wait_for_label("USE", 300) or \
                not self.menu.select_label("USE", max_presses=4):
            return self._item_fail("no-use-option",
                                   f"no USE option for {item_name}")
        used, reason = self._confirm_field_item(item_name, target_slot,
                                                before)
        self._exit_field_ui()
        self.last_item_reason = reason
        if not used:
            log.info(f"  {item_name} not used on slot {target_slot}: "
                     f"{reason}")
        return used

    def _confirm_field_item(self, item_name, target_slot, before,
                            max_frames=4500, max_confirms=3):
        """Drive the pack's post-USE pages and report (used, reason).

        Healing/status items ask for a target party list; repels/ropes
        just run, and those are polled for consumption too. Two traps
        (wren pt3 REVIVE repro: returned False, bag never decremented,
        while a manual pack drive worked):
          * the target cursor does NOT start on row 0 -- see
            _party_target -- so blind press counts pick the wrong mon;
          * the revive jingle + "... came to!" message pace slowly over a
            party menu that keeps CANCEL drawn, so success gates on the
            bag read-back, never on the menu closing."""
        e = self.emu
        targeted = self.menu.wait_for(_party_target_list, timeout_frames=400)
        if targeted and not self._party_target(target_slot):
            return False, "target-miss"
        confirms = 0
        f0 = last_a = e.frame
        while e.frame - f0 < max_frames:
            after = bag_quantity(e, self.names, item_name)
            if after is None or (before is not None and after < before):
                return True, "used"
            rows = e.screen_text()
            if _no_effect_message(rows):
                # The engine's own refusal: nothing was consumed and
                # nothing will be. Stop here -- another A would drop back
                # onto the target list and spend the item on someone else.
                return False, "no-effect"
            if self.textbox():
                self.press("A:6 .:18")       # page the item message
            elif targeted and confirms < max_confirms and \
                    e.frame - last_a > 400 and _party_target_list(rows):
                # party menus swallow the confirm A during setup
                # (gotcha 2); an unchanged bag proves nothing was used
                # yet, so a re-press can't double-consume
                self.press("A:6 .:18")
                confirms += 1
                last_a = e.frame
            else:
                self.press(".:20")           # jingle: input is deaf
        return False, "not-consumed"

    def _heal_items(self):
        """{normalized name: curative properties} for this ROM, cached."""
        global _field_heal_table
        if _field_heal_table is None:
            _field_heal_table = _load_heal_table(paths.ROM, self.emu.sym,
                                                 self.names)
        return _field_heal_table

    def heal(self, tries=2):
        """Nurse cycle: walk into the local Pokécenter if needed, talk to
        the nurse the MAP declares, and come back out with a full party.

        A method because every doc promised one -- the capability table
        reads `d.heal()` and the only implementation was the module-level
        `trek.heal_pokecenter(d)`, so a session hit
        `AttributeError: 'Driver' object has no attribute 'heal'` in the
        middle of a gym run. Same function, reachable where it is
        documented."""
        return heal_pokecenter(self, tries=tries)

    def heal_party(self, items=None, max_items_per_mon=6):
        """Top every damaged/statused party member back up out of the bag,
        cheapest sufficient item first. Returns {mon label: outcome}:

            {'BROOK': 'FULL RESTORE', 'SNAG': 'already full',
             'REED': 'no item'}

        The outcome is the item (or ', '-joined items) actually consumed,
        'already full' for a mon that needed nothing, 'no item' when the
        bag holds nothing that would help, or use_item's failure reason.
        Healthy mons are never touched, and the run stops per mon as soon
        as the relevant items run out.

        `items`: optional whitelist of item names heal_party may spend.
        Heal amounts, cure masks and prices all come from the ROM's own
        tables (_load_heal_table), so 'cheapest sufficient' is the game's
        arithmetic, not a guess."""
        table = self._heal_items()
        allow = None if items is None else {norm_item(n) for n in items}
        out = {}
        count = len(game_state(self.emu, self.names)["party"])
        for slot in range(count):
            spent = []
            label = None
            for _ in range(max_items_per_mon):
                mon = game_state(self.emu, self.names)["party"][slot]
                label = mon.get("nickname") or mon.get("name") or f"slot{slot}"
                if mon.get("egg"):
                    break
                status = self._status_byte(slot)
                need_hp = max(0, mon["max_hp"] - mon["hp"])
                if not need_hp and not status:
                    out[label] = ", ".join(spent) if spent else "already full"
                    break
                pick = cheapest_heal(table, self._bag(), allow, need_hp,
                                     status, mon["hp"] == 0)
                if pick is None:
                    out[label] = ", ".join(spent) + " (still hurt)" \
                        if spent else "no item"
                    break
                if not self.use_item(pick, target_slot=slot):
                    out[label] = ", ".join(spent + [
                        f"{pick}: {self.last_item_reason}"])
                    break
                spent.append(pick)
            else:
                out[label] = ", ".join(spent) + " (still hurt)"
        return out

    def _status_byte(self, slot):
        """Raw wPartyMon<slot>Status byte (the mask StatusHealingActions
        entries are tested against). game_state decodes it to names, but
        'which item cures this' needs the bits."""
        sym = self.emu.sym
        bank, base = sym["wPartyMon1"]
        base += slot * sym.offset("wPartyMon2", "wPartyMon1") + \
            sym.offset("wPartyMon1Status", "wPartyMon1")
        return self.emu.read((bank, base), 1)[0]

    def _shop_cursor_row(self, rows):
        from crystalagent.menus import _cursor_x
        for i, r in enumerate(rows):
            if _cursor_x(r) >= 0:
                return i
        return -1

    def _shop_list_up(self, rows=None):
        rows = self.emu.screen_text() if rows is None else rows
        return any("¥" in r for r in rows)

    def _shop_picker_up(self, rows=None):
        rows = self.emu.screen_text() if rows is None else rows
        return any("How many?" in r or "×" in r for r in rows)

    def _shop_exit(self, max_presses=12):
        """B out of every shop screen -- picker, list, clerk menu, page --
        and verify. B only: A on a list buys whatever the cursor sits on
        (gotcha 13), and A on the picker buys `qty` of it.

        Returns True when nothing shop-shaped and nothing modal is left.
        The old loop stopped at "no ¥ and no cursor", which a quantity
        picker satisfies while still owning the input."""
        for _ in range(max_presses):
            rows = self.emu.screen_text()
            if not (self._shop_list_up(rows) or self._shop_picker_up(rows)
                    or self.textbox() or self.menu_open()):
                self.press(".:40")            # outlast a closing repaint
                if not self._shop_list_up() and not self._shop_picker_up() \
                        and not self.menu_open():
                    return True
                continue
            self.press("B:6 .:16")
        left = self.emu.screen_text()
        if self._shop_picker_up(left):
            log.warning("  mart: a QUANTITY PICKER is still open -- "
                        "movement will be swallowed until it closes")
        return not self._shop_list_up(left) and not self._shop_picker_up(left)

    def mart_buy(self, x, y, item_name, qty=1, label=""):
        """Talk to the clerk at (x,y) and buy `qty` of `item_name`.
        Returns True if the bag ended up holding the item."""
        def bag_count():
            total = 0
            for count_sym, list_sym in (("wNumItems", "wItems"),
                                        ("wNumBalls", "wBalls")):
                n = min(self.emu.read_u8(count_sym), 20)
                if not n:
                    continue
                idx = bag_item_index(self.emu, self.names, item_name,
                                     "balls" if list_sym == "wBalls"
                                     else "items")
                if idx is not None:
                    bank, addr = self.emu.sym[list_sym]
                    raw = self.emu.read((bank, addr), n * 2)
                    total += raw[idx * 2 + 1]
            return total

        before = bag_count()
        bought = False
        want = norm_item(item_name)
        shop_open = self._shop_list_up()
        if not shop_open:
            if self.talk_to(x, y, label or "clerk") != "talked":
                return False
            opened = False
            for _attempt in range(2):
                # The clerk's own menu comes FIRST: "Welcome! How may I
                # help you?" over BUY / SELL / QUIT (a glyph menu, so
                # talk_to's flush_dialog correctly stops there). The
                # buy list only exists after BUY is taken, and taking it
                # has to be DELIBERATE -- a blind A here is gotcha 13.
                # Waiting passively for a '¥' that only BUY can produce
                # is what made this raise "FULL RESTORE x6 failed (bag
                # 0 -> 0)" at the Indigo Plateau mart with the item in
                # stock (session claude pt12).
                for _ in range(20):
                    if self._shop_list_up():
                        opened = True
                        break
                    if Menus.has_label(self.emu.screen_text(), "BUY") \
                            or any("BUY" in r for r in
                                   self.emu.screen_text()):
                        self.select_menu_row("BUY", max_presses=6)
                    self.press(".:8")
                if opened or _attempt:
                    break
                # gotcha 2 first-call race: the clerk A press can land the
                # frame the dialog engine isn't polling input yet --
                # settle, drain stragglers, re-talk ONCE before failing
                log.info("  shop menu slow to open; settling and "
                      "re-talking")
                self.settle()
                if self.textbox():
                    self.flush_dialog()
                if self.talk_to(x, y, label or "clerk") != "talked":
                    break
            if not opened:
                self._shop_exit()
                raise RuntimeError(
                    f"mart_buy: buy list did not open at ({x},{y}) -- "
                    f"clerk talk failed twice (registry actions must not "
                    f"fail as a silent log line)")
        # Only the LIST rows: the description textbox at the bottom
        # (rows 12-17) also carries item words, and the list itself is
        # name-row/price-row pairs in a 4-item window with a '▼' when
        # there is more below (live: ULTRA BALL / MAX REPEL / HYPER
        # POTION / MAX POTION, with FULL RESTORE off-window).
        list_rows = 12
        seen, flipped, direction = None, False, "D"
        for _ in range(40):                       # bounded item search
            rows = self.emu.screen_text()
            window = rows[:list_rows]
            cur = self._shop_cursor_row(window)
            target = next((i for i, r in enumerate(window)
                           if want in norm_item(r)), None)
            if cur >= 0 and target is not None and target == cur:
                # the list's first row is already under the cursor the
                # frame it opens, and an A that early is swallowed (gotcha
                # 2): GREAT BALL at Olivine "had no quantity picker" 3x
                self.press(".:16")
                self.press("A:6 .:40")           # open quantity picker
                if not self.menu.wait_for(
                        lambda r: any("How many?" in s for s in r),
                        timeout_frames=400):
                    log.info("  no quantity picker")
                    break
                def picker_qty():
                    for s in self.emu.screen_text():
                        if "×" in s:
                            try:
                                return int(s.split("×")[1].split()[0])
                            except (IndexError, ValueError):
                                return None
                    return None

                # qty keys are RIGHT=+10 / LEFT=-10 / UP=+1 / DOWN=-1
                # and presses get swallowed unpredictably -- verify the
                # ×NN glyph after EVERY press or overshoot (omp-fresh hit
                # x51 once on UP-only blind presses).
                tries = 0
                while picker_qty() != qty and tries < 40:
                    v = picker_qty()
                    if v is None:
                        self.press(".:10")
                    else:
                        step = ("R" if v + 10 <= qty else
                                "L" if v - 10 >= qty else
                                "U" if v < qty else "D")
                        self.press(f"{step}:4 .:14")
                    tries += 1
                if picker_qty() != qty:
                    break
                self.press(".:10")
                self.press("A:6 .:40")           # confirm the quantity
                # ...which opens "N ITEM(S) will be ¥NNNN." over a
                # YES/NO box (live at the Indigo Plateau mart). Nothing
                # answered it before, so the purchase never happened and
                # `bought` was set anyway -- "bag 0 -> 0, bought=True".
                # flush_dialog cannot answer it either: a choice box is
                # exactly what it refuses to touch (gotcha 13).
                for _ in range(6):
                    rows = self.emu.screen_text()
                    if Menus.has_label(rows, "YES"):
                        self.press("A:6 .:40")
                        break
                    if any("YES" in r for r in rows):
                        self.press("U:4 .:14")   # cursor sat on NO
                        continue
                    self.press(".:15")
                self.flush_dialog(3000)          # "Here you are! Thanks!"
                bought = True
                break                             # one purchase per call
            if cur < 0:
                break
            if target is None:
                # off-window: walk the list, and REVERSE once it pins --
                # scrolling one way forever is how an in-stock item below
                # the window reported "not for sale"
                if window == seen:
                    if flipped:
                        break
                    direction, flipped = "U", True
                seen = window
                self.press(f"{direction}:6 .:12")
                continue
            self.press("D:6 .:12" if target > cur else "U:6 .:12")
        # The clerk's refusal ("You don't have enough money.") is the last
        # thing on screen; capture it BEFORE exiting or the reason is lost
        # and the caller only learns "bought=True, bag 0 -> 0" (live: six
        # SUPER POTIONs at 700 each on a 2506 wallet).
        note = " ".join(r.strip() for r in self.emu.screen_text()[13:]
                        if r.strip())[:120]
        self._shop_exit()
        self.press(".:40")
        after = bag_count()
        ok = bought and after >= before + qty
        log.info(f"  mart_buy {item_name} x{qty}: "
              f"{'ok' if ok else 'FAILED'} ({before} -> {after})")
        if not ok:
            try:      # duck-typed drivers may not model the money read
                money = game_state(self.emu, self.names)["player"]["money"]
                purse = f", ¥{money} on hand"
            except Exception:
                purse = ""
            raise RuntimeError(
                f"mart_buy: {item_name} x{qty} failed "
                f"(bag {before} -> {after}{purse})"
                + (f" -- last screen: {note!r}" if note else ""))
        return True
