"""Real battle play: move selection from the game's own data, fleeing,
ball throwing, switching -- driven through menu primitives.

All data is parsed from the disassembly/ROM, nothing hardcoded:
- constants/type_constants.asm      type ids
- data/types/type_matchups.asm      effectiveness chart
- ROM `Moves` table (via .sym)      power/type/accuracy per move id

Cursor positions are read from the engine's own variables:
- wMenuCursorPosition  battle main menu (FIGHT/PKMN/PACK/RUN = 1..4)
- wMenuCursorY         move list slot (1-based), scrolling lists
- wMenuScrollPosition  scrolling-list window offset
"""

import logging
import re
from pathlib import Path

from .menus import (Menus, battle_menu_up, naming_keyboard_up, _cursor_x,
                    _cursor_xs)
from .state import EGG, MON_NAME_LENGTH, _status

log = logging.getLogger("trek")

MOVE_LENGTH = 7  # animation, effect, power, type, accuracy, pp, effect chance


def _parse_types(path):
    types = {}
    tid = 0
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s+const (\w+)", line)
        if m:
            types[m.group(1)] = tid
            tid += 1
    return types


def _parse_matchups(path, types):
    mult = {}
    scale = {"SUPER_EFFECTIVE": 2.0, "MORE_EFFECTIVE": 1.5,
             "EFFECTIVE": 1.0, "NOT_VERY_EFFECTIVE": 0.5,
             "NO_EFFECT": 0.0}
    for m in re.finditer(r"\s*db\s+(\w+),\s*(\w+),\s*(\w+)",
                         Path(path).read_text()):
        atk, dfn, eff = m.groups()
        if atk in types and dfn in types and eff in scale:
            mult[(types[atk], types[dfn])] = scale[eff]
    return mult


class BattleData:
    def __init__(self, repo, sym, rom_path):
        repo = Path(repo)
        self.types = _parse_types(repo / "constants/type_constants.asm")
        self.matchups = _parse_matchups(repo / "data/types/type_matchups.asm",
                                        self.types)
        rom = open(rom_path, "rb").read()
        bank, base = sym["Moves"]
        start = bank * 0x4000 + (base - 0x4000 if base >= 0x4000 else base)
        self.moves = {}
        for mid in range(1, 252):
            rec = rom[start + (mid - 1) * MOVE_LENGTH:
                      start + mid * MOVE_LENGTH]
            self.moves[mid] = {
                "effect": rec[1],
                "power": rec[2],
                "type": rec[3],
                "accuracy": min(rec[4], 100),
            }

    def effectiveness(self, atk_type, def_types):
        """Type multiplier of `atk_type` against a defender's type pair.
        Duplicate entries count ONCE: the engine (CheckTypeMatchup,
        engine/battle/effect_commands.asm) walks the matchup table and
        applies each row if its defending type equals type1 OR type2, so a
        mono-type mon -- which stores its type twice -- is never squared
        (Water vs a mono-WATER mon is 0.5x, not 0.25x)."""
        m = 1.0
        for t in dict.fromkeys(def_types):
            m *= self.matchups.get((atk_type, t), 1.0)
        return m


def norm_item(name):
    """Canonical item-name key: uppercase alphanumerics only, so spacing,
    hyphens, and case never matter ('SUPER POTION' == 'SUPERPOTION' ==
    'Super Potion'). The repo writes the POKé glyph as '#' ("# BALL"),
    screens show "POKé BALL"; callers say "POKE BALL". Shared by the bag
    lookup AND every screen-row match (import: crystalagent.battle)."""
    return re.sub(r"[^A-Z0-9]", "",
                  name.replace("#", "POKE").replace("é", "e")
                  .replace("É", "E").replace("\x80", "e").upper())


_norm_item = norm_item   # legacy import name (trek.py)


def bag_item_index(emu, names, item_name, pocket="items"):
    """0-based position of an item inside a pack pocket's WRAM list.
    Entries are (id, quantity) pairs."""
    if pocket == "balls":
        count_sym, list_sym = "wNumBalls", "wBalls"
    else:
        count_sym, list_sym = "wNumItems", "wItems"
    count = min(emu.read_u8(count_sym), 20)
    want = norm_item(item_name)
    got = next((i for i, n in names.items.items()
                if norm_item(n) == want), None)
    if got is None or count == 0:
        return None
    bank, addr = emu.sym[list_sym]
    raw = emu.read((bank, addr), count * 2)
    for i in range(count):
        if raw[i * 2] == got:
            return i
    return None


def bag_quantity(emu, names, item_name, pocket="items"):
    """How many of an item sit in a pack pocket's WRAM list ((id, qty)
    pairs), or None if absent."""
    idx = bag_item_index(emu, names, item_name, pocket)
    if idx is None:
        return None
    list_sym = "wBalls" if pocket == "balls" else "wItems"
    bank, addr = emu.sym[list_sym]
    return emu.read((bank, addr + idx * 2 + 1), 1)[0]


def goto_pocket(menu, pocket, timeout_frames=500):
    """LEFT/RIGHT across the pack pockets until the jumptable sits in the
    requested pocket's menu state (items/balls/key = 2/4/6). Works in and
    out of battle -- the Pack/BattlePack jumptables are shared."""
    want = {"items": 2, "balls": 4, "key": 6}[pocket]
    order = [2, 4, 6, 8]
    start = menu.emu.frame
    while menu.emu.frame - start < timeout_frames:
        cur = menu.emu.read_u8("wJumptableIndex")
        if cur == want:
            menu.press(".:10")
            return True
        if cur in order:
            d = "R" if order.index(want) > order.index(cur) else "L"
            menu.press(f"{d}:4 .:12")
        else:
            menu.press(".:8")
    return False


def cancel_pack(menu):
    """Back fully out of an in-progress pack session."""
    for _ in range(6):
        if menu.emu.read_u8("wJumptableIndex") not in (2, 4, 6, 8):
            return True
        menu.press("B:4 .:10")
    return False


class Battle:
    """One battle session. Construct while wBattleMode != 0."""

    def __init__(self, emu, names, bdata):
        self.emu = emu
        self.names = names
        self.data = bdata
        self.menu = Menus(emu)
        # latched when the engine answers a confirmed SWITCH with
        # "<MON> can't be recalled!"; cleared as soon as WRAM shows the
        # trap is over (see switch_blocked_reason)
        self.switch_refused = False

    # -- observation -------------------------------------------------------

    def active(self):
        return self.emu.read_u8("wBattleMode") != 0

    def _struct_reader(self, base_label):
        sym = self.emu.sym
        bank, base = sym[base_label]
        off = lambda f: sym.offset(base_label + f, base_label)
        return lambda f, n=1: self.emu.read((bank, base + off(f)), n)

    def _party_nickname(self, slot):
        """Nickname of party slot `slot`, straight from wPartyMonNicknames."""
        bank, base = self.emu.sym["wPartyMonNicknames"]
        raw = self.emu.read((bank, base + slot * MON_NAME_LENGTH),
                            MON_NAME_LENGTH)
        return self.emu.charmap.decode(raw)

    def _status_of(self, label):
        """Status-condition names ('PSN', 'SLP:3', ...) of a battler's
        status byte. Defensive: a symbol table without the label (or a
        pre-battle read) yields [] rather than breaking the snapshot."""
        try:
            return _status(self.emu.read_u8(label))
        except Exception:
            return []

    def me(self):
        """Active battler snapshot. Identity: 'name' is the SPECIES name
        (compat -- it CHANGES on mid-battle evolution and is shared by
        duplicate mons); 'nickname' + 'party_slot' are the stable handles
        policies should match on (wCurBattleMon -> wPartyMonNicknames).
        'status' lists the active conditions (wBattleMonStatus)."""
        rd = self._struct_reader("wBattleMon")
        moves = list(rd("Moves", 4))
        pps = list(rd("PP", 4))
        species = rd("Species")[0]
        slot = self.emu.read_u8("wCurBattleMon")
        return {
            "species": species,
            "name": self.names.species.get(species, "?"),
            "nickname": self._party_nickname(slot),
            "party_slot": slot,
            "level": rd("Level")[0],
            "hp": int.from_bytes(rd("HP", 2), "big"),
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "types": list(rd("Type", 2)),
            "status": self._status_of("wBattleMonStatus"),
            "moves": [(m, p) for m, p in zip(moves, pps) if m],
        }

    def enemy(self):
        """Enemy battler snapshot; 'nickname' is the displayed name
        (wEnemyMonNickname), 'party_slot' the OT party index (wCurOTMon;
        meaningless for wild mons), 'status' its conditions."""
        rd = self._struct_reader("wEnemyMon")
        species = rd("Species")[0]
        return {
            "species": species,
            "name": self.names.species.get(species, "?"),
            "nickname": self.emu.read_text("wEnemyMonNickname",
                                           MON_NAME_LENGTH),
            "party_slot": self.emu.read_u8("wCurOTMon"),
            "level": rd("Level")[0],
            "hp": int.from_bytes(rd("HP", 2), "big"),
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "types": list(rd("Type", 2)),
            "status": self._status_of("wEnemyMonStatus"),
        }

    # -- cheap vitals (turn accounting) -------------------------------------
    # me()/enemy() decode nicknames, move lists and status for every call;
    # a per-turn before/after HP record only needs three numbers, so these
    # read exactly those. Used by crystalagent.decide.TurnLog to count the
    # free hits the Koga wipe had no record of.

    def my_hp(self):
        return self.emu.read_be("wBattleMonHP", 2)

    def enemy_hp(self):
        return self.emu.read_be("wEnemyMonHP", 2)

    def hp_snapshot(self):
        """{'my_hp', 'enemy_hp', 'enemy_species'} for one side of a turn."""
        return {
            "my_hp": self.my_hp(),
            "enemy_hp": self.enemy_hp(),
            "enemy_species": self.emu.read_u8("wEnemyMonSpecies"),
        }

    def _my_move_list_up(self, rows):
        """The player's MOVE LIST is open outside attack(): a ▶/cursor row
        directly followed by one of my known move names. A-mashing here
        keeps re-picking the cursor's (possibly DISABLED) move forever."""
        names = [self.names.moves.get(mid, "") for mid, _ in self.me()["moves"]]
        names = [n for n in names if n]
        if not names:
            return False
        from .menus import _cursor_x
        for r in rows:
            x = _cursor_x(r)
            if x >= 0:
                label = r[x + 1:].strip()
                if any(label.startswith(n) for n in names):
                    return True
        return False

    def _disabled_move_id(self):
        """Move currently DISABLED by enemy Disable; wPlayerDisableCount
        is the remaining-turn counter, wDisabledMove the move id."""
        try:
            if self.emu.read_u8("wPlayerDisableCount") == 0:
                return None
            return self.emu.read_u8("wDisabledMove")
        except Exception:
            return None

    # -- forced turns and trapping (pokecrystal engine/battle/core.asm) -----
    #
    # BattleTurn runs `call CheckPlayerLockedIn / jr c, .skip_iteration`:
    # when the check sets carry the engine SKIPS the entire FIGHT/PKMN/PACK/
    # RUN menu and drives the turn itself. CheckPlayerLockedIn tests exactly
    #   wPlayerSubStatus4 & 1 << SUBSTATUS_RECHARGE   (Hyper Beam cooldown)
    #   wPlayerSubStatus3 & 1 << SUBSTATUS_CHARGED    (Fly/Dig/Solarbeam turn 2)
    #   wPlayerSubStatus3 & 1 << SUBSTATUS_RAMPAGE    (Thrash/Outrage/Petal Dance)
    #   wPlayerSubStatus1 & 1 << SUBSTATUS_ROLLOUT    (Rollout lock)
    # ParsePlayerAction additionally skips MoveSelectionScreen -- the move
    # LIST, not the main menu -- while Encored (wPlayerSubStatus5) or Biding
    # (wPlayerSubStatus3): the menu is drawn but our move pick is discarded.
    # Bit numbers come from the SUBSTATUS_* const lists in
    # constants/battle_constants.asm.
    LOCK_BITS = (
        ("recharging", "wPlayerSubStatus4", 1 << 5),   # SUBSTATUS_RECHARGE
        ("rampaging",  "wPlayerSubStatus3", 1 << 1),   # SUBSTATUS_RAMPAGE
        ("charging",   "wPlayerSubStatus3", 1 << 4),   # SUBSTATUS_CHARGED
        ("rollout",    "wPlayerSubStatus1", 1 << 6),   # SUBSTATUS_ROLLOUT
        ("encored",    "wPlayerSubStatus5", 1 << 4),   # SUBSTATUS_ENCORED
        ("biding",     "wPlayerSubStatus3", 1 << 0),   # SUBSTATUS_BIDE
    )
    # locks that draw NO battle menu at all this turn ...
    MENU_SKIP_LOCKS = ("recharging", "rampaging", "charging", "rollout")
    # ... versus locks that draw the menu but pick the move for us
    AUTO_MOVE_LOCKS = ("encored", "biding")
    SUBSTATUS5_CANT_RUN = 1 << 7      # MEAN LOOK / SPIDER WEB

    def locked_turn(self):
        """Name of the forced-turn state the engine is resolving, or None
        when the turn is ours to steer. A locked turn is NOT a failed
        input: the engine is mid-sequence and hands control back by
        itself, so the only correct response is to tick frames and
        re-poll."""
        regs = {}
        try:
            for _name, label, _mask in self.LOCK_BITS:
                if label not in regs:
                    regs[label] = self.emu.read_u8(label)
        except Exception:
            return None     # no symbol table / not in battle: assume ours
        for name, label, mask in self.LOCK_BITS:
            if regs[label] & mask:
                return name
        return None

    def trapped(self):
        """Is the active mon held in place -- True / False / None when the
        symbols cannot be read?

        A partial-trapping move (BIND / WRAP / FIRE SPIN / CLAMP /
        WHIRLPOOL) counts down in wPlayerWrapCount; MEAN LOOK / SPIDER WEB
        set SUBSTATUS_CANT_RUN in wEnemySubStatus5. Both TryPlayerSwitch
        (.check_trapped) and TryToRunAwayFromBattle read exactly this pair
        before refusing, so they are the whole truth about whether SWITCH
        and RUN are legal this turn."""
        try:
            if self.emu.read_u8("wPlayerWrapCount"):
                return True
            return bool(self.emu.read_u8("wEnemySubStatus5")
                        & self.SUBSTATUS5_CANT_RUN)
        except Exception:
            return None

    def switch_blocked_reason(self):
        """Why a SWITCH cannot work this turn, or None.

        The engine does not fail a trapped switch quietly: TryPlayerSwitch
        prints BattleText_MonCantBeRecalled and `jp BattleMenuPKMN_Loop`,
        i.e. it drops back into the PARTY list with the switch un-done and
        the menu still open. That is the live Victory Road wedge -- ONIX
        had BOUND the active mon, the policy kept asking to switch, and
        the harness sat on an unchanging nested menu until the frame cap.
        WRAM is the primary signal; the latch from an observed refusal
        covers a symbol table that cannot answer."""
        state = self.trapped()
        if state:
            return "trapped: the active mon can't be recalled"
        if getattr(self, "switch_refused", False):
            if state is False:
                self.switch_refused = False   # WRAM: the trap is over
                return None
            return "the engine refused the last switch (can't be recalled)"
        return None

    def _flee_blocked_reason(self):
        """Why RUN cannot work this turn, or None. A refused RUN does not
        even burn the turn: BattleMenu_Run's .cant_escape and
        .cant_run_from_trainer paths fall through to `jp BattleMenu`, so
        the screen and both mons come back untouched and a policy that
        keeps picking 'flee' spins to the frame cap."""
        try:
            trainer = self.emu.read_u8("wBattleMode") == 2
        except Exception:
            return None     # unreadable: leave the action alone
        if trainer:
            return "no escape from a trainer battle"
        if self.trapped():
            return "trapped: can't escape"
        return None

    def best_move(self):
        """Slot index of the highest expected-damage move with PP left;
        None means every slot is dry (Struggle territory)."""
        disabled = self._disabled_move_id()
        me, enemy = self.me(), self.enemy()
        best, best_score = None, -1.0
        for i, (mid, pp) in enumerate(me["moves"]):
            if pp == 0 or mid not in self.data.moves:
                continue
            if disabled is not None and mid == disabled:
                continue
            mv = self.data.moves[mid]
            stab = 1.5 if mv["type"] in me["types"] else 1.0
            eff = self.data.effectiveness(mv["type"], enemy["types"])
            score = mv["power"] * eff * stab * (mv["accuracy"] / 100.0)
            if mv["power"] == 0:      # status moves: weak fallback pick
                score = 1.0
            if score > best_score:
                best, best_score = i, score
        return best

    def bag_item_index(self, item_name, pocket="items"):
        return bag_item_index(self.emu, self.names, item_name, pocket)

    # -- low-level menu actions ---------------------------------------------

    # battle menu grid (rows x 2 cols): FIGHT PKMN / PACK RUN
    BATTLE_MENU_POS = {1: (1, 1), 2: (1, 2), 3: (2, 1), 4: (2, 2)}

    def _battle_option(self, n, max_steps=8):
        """Select option n of the FIGHT/PKMN/PACK/RUN grid by steering the
        live 2D cursor (wMenuCursorY row / wMenuCursorX column)."""
        ty, tx = self.BATTLE_MENU_POS[n]
        for _ in range(max_steps):
            y = self.emu.read_u8("wMenuCursorY")
            x = self.emu.read_u8("wMenuCursorX")
            if y == ty and x == tx:
                self.menu.press("A:6 .:18")
                return True
            if y != ty:
                self.menu.press(("D" if y < ty else "U") + ":6 .:4")
            else:
                self.menu.press(("R" if x < tx else "L") + ":6 .:4")
        return False

    def _confirm_menu_open(self):
        """The frame the menu is drawn, its input loop isn't running yet;
        give it a beat so the next A press isn't swallowed."""
        self.menu.press(".:16")

    def _wait_move_menu(self, timeout_frames=600):
        """The move list is open when the ▶ cursor sits next to one of my
        known move names."""
        my_moves = [self.names.moves.get(m, "") for m, _ in self.me()["moves"]]

        def pred(rows):
            for r in rows:
                x = _cursor_x(r)
                if x >= 0:
                    label = r[x + 1:].strip()
                    if any(mv and label.startswith(mv) for mv in my_moves):
                        return True
            return False

        ok = self.menu.wait_for(pred, timeout_frames)
        self.menu.press(".:10")   # let the cursor settle
        return ok

    def _move_menu_select(self, slot_idx, max_steps=10):
        """Highlight move slot (0-based) in the open move list."""
        steps = 0
        while steps < max_steps:
            if self.emu.read_u8("wMenuCursorY") == slot_idx + 1:
                self.menu.press("A:2 .:12")
                return True
            self.menu.press("D:6 .:4")
            steps += 1
        return False

    # -- high-level actions --------------------------------------------------

    def attack(self, move_idx=None):
        """From the main battle menu: FIGHT -> move slot -> A."""
        if move_idx is not None:
            # a requested move with no PP left gets "rejected" by the game
            # AFTER the menu confirm, which reads as success here and wedges
            # the turn loop -- fall back to the best move that still has PP
            moves = self.me()["moves"]
            if move_idx >= len(moves) or moves[move_idx][1] == 0:
                move_idx = None
        if not self._battle_option(1):
            return False
        if not self._wait_move_menu():
            return False
        if move_idx is None:
            move_idx = self.best_move()
        if move_idx is None:          # out of PP everywhere: mash A = Struggle
            self.menu.press("A:2 .:12")
            return True
        if not self._move_menu_select(move_idx):
            return False
        # A game-side rejection ("... is DISABLED!", "no PP left for the
        # move") reads as menu success here, so play()'s fails counter
        # never trips and the same rejected move gets re-picked forever
        # (the historic Bridget wedges). Verify the turn actually started.
        # False positive cost: one B-press re-sync if the ENEMY announces
        # a Disable inside this short window -- cheap.
        rejected = self.menu.wait_for(
            lambda r: any(m in "".join(r).upper()
                          for m in ("NO PP", "DISABLED")),
            timeout_frames=90)
        return not rejected

    def attack_forced(self):
        """Confirm FIGHT and stop: an Encored / Biding turn never opens
        MoveSelectionScreen (ParsePlayerAction jumps past it and reuses
        wLastPlayerMove), so pressing FIGHT *is* the whole action.
        attack() would sit in _wait_move_menu for its full timeout and
        then report a misfire that never happened."""
        return self._battle_option(1)

    def flee(self):
        return self._battle_option(4)

    def throw_ball(self, ball="POKE BALL"):
        """From the main battle menu: PACK -> balls pocket -> ball -> USE."""
        idx = self.bag_item_index(ball, pocket="balls")
        if idx is None or not self._battle_option(3):
            return False
        # NB: _bail_pack always returns False -- a successful back-out is
        # not action success (a cancelled action reported as done loops
        # the turn forever).
        if not self._goto_pocket("balls"):
            return self._bail_pack()
        if not self._pocket_select(idx, ball):
            return self._bail_pack()
        if not self.menu.wait_for_label("USE") or \
                not self.menu.select_label("USE", max_presses=4):
            return self._bail_pack()
        return True

    ITEM_CONFIRM_FRAMES = 1800  # description + target pick + use text
    ITEM_STALL_REPS = 8         # identical screens before declaring a stall

    def use_battle_item(self, item_name, target_slot=0):
        """Main-menu PACK -> items pocket -> item -> confirm through the
        description / "Use on which PM?" pages -> bag-count decrement.

        The live Morty stall: select_abs's blind trailing A got swallowed
        and the flow parked on the item description until the wedge cap
        fired. Every step now runs on live WRAM cursor reads
        (_pocket_select / _party_target), every page advance is
        state-verified, success is ONLY a bag read-back, and any page that
        stops responding backs out to the battle menu and reports the
        action failed so play()'s substitution guard takes the next turn."""
        idx = self.bag_item_index(item_name, pocket="items")
        if idx is None or not self._battle_option(3):
            return False
        if not self._goto_pocket("items"):
            return self._bail_pack()
        before = bag_quantity(self.emu, self.names, item_name)
        if before is None:
            # consumption is the only success signal; an unverifiable use
            # is a wedge risk (once burned ~9 potions blind) -- refuse
            return self._bail_pack()
        if not self._pocket_select(idx, item_name):
            return self._bail_pack()
        if self._confirm_item_pages(item_name, target_slot, before):
            return True
        return self._bail_pack()

    def _pocket_select(self, idx, item_name, max_steps=40):
        """Steer the pack-pocket cursor to absolute index `idx` on the
        live WRAM position (wMenuScrollPosition + wMenuCursorY), in BOTH
        directions -- the pocket REMEMBERS its cursor between opens, so a
        DOWN-only walk from an assumed top row can never climb back up --
        then verify the highlighted row's TEXT really is the item before
        pressing A (select_abs desyncs once burned ~9 potions blind)."""
        want = norm_item(item_name)
        last, stuck = None, 0
        for _ in range(max_steps):
            cur = self.menu.scroll_abs()
            if cur == idx:
                break
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return False    # cursor pinned: list edge or wrong menu
            last = cur
            self.menu.press("D:6 .:4" if cur < idx else "U:6 .:4")
        else:
            return False
        self.menu.press(".:10")     # let the row repaint before scraping
        got = self.menu.cursor_row()
        if not got or not norm_item(got[1]).startswith(want):
            return False            # WRAM/screen disagree: never blind-A
        self.menu.press("A:6 .:18")
        return True

    def _party_target(self, slot, max_steps=12):
        """Steer the party-menu cursor to row `slot` (0-based) on the live
        WRAM cursor (wMenuCursorY, 1-based) and confirm with A. The menu
        persists its cursor between opens, and wMenuScrollPosition still
        holds the pocket's scroll offset here, so neither blind press
        counts nor scroll_abs are safe for this list."""
        last, stuck = None, 0
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY") - 1
            if cur == slot:
                self.menu.press("A:6 .:18")
                return True
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return False    # cursor pinned: wrong menu / list edge
            last = cur
            self.menu.press("D:6 .:6" if cur < slot else "U:6 .:6")
        return False

    @staticmethod
    def _party_pick_up(rows):
        """The "Use on which PM?" target list: CANCEL plus HP fractions.
        (The pocket list also draws a CANCEL row, but its quantities are
        '× n', never 'hp/max'.)"""
        if "USE ON WHICH" in "".join(rows).upper():
            return True
        return any("CANCEL" in r for r in rows) and \
            any(re.search(r"\d\s*/\s*\d+", r) for r in rows)

    def _confirm_item_pages(self, item_name, target_slot, before):
        """Drive whatever the battle pack shows after the item row's A --
        the USE/QUIT popup, the item description page, the "Use on which
        PM?" party list -- one state-verified press per pass. True only on
        a bag-count decrement; a screen that stops changing despite
        presses is a stall (False: the caller backs out and reports the
        action failed)."""
        f0 = self.emu.frame
        last_snap, reps = None, 0
        while self.emu.frame - f0 < self.ITEM_CONFIRM_FRAMES:
            after = bag_quantity(self.emu, self.names, item_name)
            if after is None or after < before:
                return True     # consumption: the only success signal
            rows = self.menu.screen()
            snap = tuple(rows)
            if snap == last_snap:
                reps += 1
                if reps >= self.ITEM_STALL_REPS:
                    return False    # pages stopped responding: stall
            else:
                last_snap, reps = snap, 0
            if self.menu.has_label(rows, "USE"):
                if not self.menu.select_label("USE", max_presses=4):
                    return False
            elif self._party_pick_up(rows):
                # an unchanged bag proves nothing was used yet, so a
                # re-confirm here can never double-consume (gotcha 2:
                # party menus swallow the first A during setup)
                if not self._party_target(target_slot):
                    return False
            else:
                # description page / battle text: a plain A advances it
                self.menu.press("A:6 .:20")
        return False

    def _bail_pack(self):
        """Back out of a misfired pack/item flow all the way to the battle
        menu. cancel_pack alone is jumptable-gated and leaves non-pocket
        pages (item description, target list) on screen -- exactly the
        frozen page the Morty wedge fingerprinted. Always returns False so
        callers can `return self._bail_pack()`."""
        self._cancel_pack()
        for _ in range(6):
            if battle_menu_up(self.menu.screen()):
                break
            self.menu.press("B:4 .:12")
        return False

    # -- battle party menu + its per-mon submenu ----------------------------
    #
    # engine/battle/core.asm BattleMenuPKMN_Loop drives TWO stacked menus:
    #   * the scrolling PARTY list (SetUpBattlePartyMenu_Loop /
    #     JumpToPartyMenuAndPrintText / SelectBattleMon), positioned through
    #     wMenuScrollPosition + wMenuCursorY like every scrolling list, and
    #   * BattleMonMenu (engine/pokemon/mon_submenu.asm:247) drawn ON TOP at
    #     `menu_coords 11, 11, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1`: a fixed
    #     three-item static list, "SWITCH@" / "STATS@" / "CANCEL@", whose
    #     pick the loop reads straight back as
    #       ld a, [wMenuCursorY] / cp $1 SWITCH / cp $2 STATS / cp $3 CANCEL
    # Both keep a cursor glyph painted at the same time (the party list's
    # ▷ behind the box's ▶), so the submenu is STEERED on wMenuCursorY and
    # only CONFIRMED against glyphs inside its own column band.
    SUBMENU_ROWS = {"SWITCH": 1, "STATS": 2, "CANCEL": 3}
    SUBMENU_LEFT = 11        # BattleMonMenu .MenuHeader menu_coords x1
    RECALL_REFUSED = "BE RECALLED"   # BattleText_MonCantBeRecalled

    def _submenu_choice(self, rows):
        """Which BattleMonMenu row its OWN cursor sits on, or None. Scans
        every glyph per row (not just the leftmost) and keeps the ones
        inside the submenu's column band, so the party list's cursor
        painted further left can never be mistaken for it."""
        for row in rows:
            for x in _cursor_xs(row):
                if x + 1 < self.SUBMENU_LEFT:
                    continue
                label = row[x + 1:].strip(" │|").strip()
                for name in self.SUBMENU_ROWS:
                    if label.startswith(name):
                        return name
        return None

    def _submenu_up(self, rows):
        return self._submenu_choice(rows) is not None

    def _submenu_select(self, label, max_steps=8):
        """Put the BattleMonMenu cursor on `label` and press A. Steering
        is WRAM (wMenuCursorY, the value the engine itself branches on);
        the A press only fires once the glyph is verifiably painted in the
        submenu's own column, so a lagging tilemap can't confirm STATS."""
        want = self.SUBMENU_ROWS[label]
        for _ in range(max_steps):
            if self._submenu_choice(self.menu.screen()) == label:
                self.menu.press("A:6 .:18")
                return True
            try:
                cur = self.emu.read_u8("wMenuCursorY")
            except Exception:
                cur = None
            if cur == want:
                self.menu.press(".:8")     # WRAM there, glyph not yet drawn
                continue
            self.menu.press("U:6 .:6" if cur is not None and cur > want
                            else "D:6 .:6")
        return False

    def _recall_refused(self, rows):
        return self.RECALL_REFUSED in "".join(rows).upper()

    def _dismiss_refusal(self, max_presses=6):
        """Clear the "<MON> can't be recalled!" prompt box."""
        for _ in range(max_presses):
            if not self._recall_refused(self.menu.screen()):
                return True
            self.menu.press("A:6 .:20")
        return False

    def _exit_party_menu(self, max_presses=8):
        """B out of the party list and any submenu until the FIGHT/PKMN/
        PACK/RUN action menu is back. Always returns False: a switch that
        did not happen is a misfired action, and leaving the nested menu
        open is what wedged the live battle."""
        for _ in range(max_presses):
            if battle_menu_up(self.menu.screen()):
                break
            self.menu.press("B:6 .:16")
        return False

    def _party_row_select(self, index, max_steps=20):
        """Steer the battle party list to absolute row `index`, then A.

        BIDIRECTIONAL on purpose: the list REMEMBERS its cursor between
        opens (the live wedge screen had it parked on row 5), so
        Menus.select_abs's DOWN-only walk can never climb back up to an
        earlier slot. Position comes from the engine's own
        wMenuScrollPosition + wMenuCursorY, and a cursor that stops moving
        means the wrong menu is up."""
        last, stuck = None, 0
        for _ in range(max_steps):
            cur = self.menu.scroll_abs()
            if cur == index:
                self.menu.press("A:6 .:18")
                return True
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return False      # cursor pinned: list edge or wrong menu
            last = cur
            self.menu.press("D:6 .:6" if cur < index else "U:6 .:6")
        return False

    def switch_to(self, party_index):
        """PKMN -> party row -> A -> SWITCH/STATS/CANCEL -> A -> send-out.

        The submenu step is a menu of its own and must be driven as one.
        The engine may then REFUSE: a trapped mon gets
        BattleText_MonCantBeRecalled and `jp BattleMenuPKMN_Loop`, which
        leaves the party menu open with the submenu cursor still on
        SWITCH and nothing on screen changing ever again -- the live
        Victory Road wedge (60 'fights', zero exp). A refusal is
        dismissed, backed out to the action menu, latched so switching
        drops out of the legal set for the rest of the trapped stretch,
        and reported as a misfire so play() re-decides."""
        if not self._battle_option(2):
            return False
        if not self._party_row_select(party_index):
            return self._exit_party_menu()
        if not self.menu.wait_for(self._submenu_up, timeout_frames=300):
            return self._exit_party_menu()
        if not self._submenu_select("SWITCH"):
            return self._exit_party_menu()
        # TryPlayerSwitch answers the confirmed SWITCH immediately -- the
        # trapped branch is a StdBattleTextbox call right after the A -- so
        # a short window is enough, and it costs the SUCCESS path nothing
        # but idle frames while the send-out starts.
        if self.menu.wait_for(self._recall_refused, timeout_frames=120):
            self.switch_refused = True
            log.warning("[battle] switch refused: the active mon can't be "
                        "recalled (trapped) -- dropping switches until the "
                        "trap ends")
            self._dismiss_refusal()
            return self._exit_party_menu()
        return True   # "Switch this pokémon?" resolves through text flow

    def _goto_pocket(self, pocket, timeout_frames=500):
        return goto_pocket(self.menu, pocket, timeout_frames)

    def _cancel_pack(self):
        return cancel_pack(self.menu)

    def _back_out(self):
        self.menu.press("B:4 .:10")
        self.menu.press("B:4 .:10")
        return False

    def _forced_switch_up(self, rows):
        """The post-faint party list (CANCEL row + 'HP / max' rows). Not
        the battle menu, so generic A-mashing parks on the fainted lead
        and re-errors forever ("There's no will to battle!").
        A potion target list ("Use on which PM?") renders the same CANCEL
        + HP rows -- only a FAINTED active mon makes it a real forced
        switch; treating the heal list as one hammers a full-HP lead and
        loops the battle to its frame cap (wedged 150 train battles)."""
        if not any("CANCEL" in r for r in rows):
            return False
        if not any(re.search(r"\d\s*/\s*\d+", r) for r in rows):
            return False
        return self.me()["hp"] <= 0

    def _drive_forced_switch(self):
        """Send out the first alive mon: select its slot, then keep
        confirming until the list closes (the first A lands during menu
        setup)."""
        for idx in self._alive_slots():
            if self.menu.select_abs(idx):
                break
        else:
            self.menu.press("B:4 .:12")
            return
        start = self.emu.frame
        while self.emu.frame - start < 600:
            rows = self.emu.screen_text()
            if not any("CANCEL" in r for r in rows):
                return
            self.menu.press("A:6 .:25")

    def _alive_slots(self):
        count = min(self.emu.read_u8("wPartyCount"), 6)
        sym = self.emu.sym
        bank, base = sym["wPartyMon1"]
        off = sym.offset("wPartyMon1HP", "wPartyMon1")
        stride = sym.offset("wPartyMon2", "wPartyMon1")
        out = []
        for i in range(count):
            hp = int.from_bytes(
                self.emu.read((bank, base + i * stride + off), 2), "big")
            if hp > 0:
                out.append(i)
        return out

    # -- main loop -----------------------------------------------------------

    def party_alive(self):
        count = min(self.emu.read_u8("wPartyCount"), 6)
        sym = self.emu.sym
        bank, base = sym["wPartyMon1"]
        off = sym.offset("wPartyMon1HP", "wPartyMon1")
        stride = sym.offset("wPartyMon2", "wPartyMon1")
        for i in range(count):
            hp = int.from_bytes(
                self.emu.read((bank, base + i * stride + off), 2), "big")
            if hp > 0:
                return True
        return False

    # -- policy-action validation and freeze detection -----------------------

    def _party_count(self):
        return min(self.emu.read_u8("wPartyCount"), 6)

    def _egg_slots(self):
        """Party indexes holding an EGG (wPartySpecies sentinel; the mon
        struct itself carries the hatched species, so HP reads look alive)."""
        try:
            slots = self.emu.read("wPartySpecies", self._party_count())
            return {i for i, s in enumerate(slots) if s == EGG}
        except Exception:
            return set()

    def _invalid_action_reason(self, act, me):
        """Why a policy action cannot possibly work this turn (switch to a
        fainted/EGG/out-of-range/TRAPPED slot, a RUN the engine refuses,
        item or ball not in the bag, attack slot empty or dry). None when
        the action is at least executable.
        Executing an impossible action wastes the turn AND re-arms forever:
        the live GATOR wedge was ('switch', i)-to-a-fainted-mon retried
        every turn until the frame cap, and the Victory Road ONIX wedge was
        'flee'/'switch' under BIND -- both refusals bounce the engine
        straight back to its menu without consuming a turn."""
        kind = act[0] if isinstance(act, tuple) else act
        arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
        if kind == "switch":
            i = arg if arg is not None else 1
            if not isinstance(i, int) or not 0 <= i < self._party_count():
                return f"switch target {i!r} out of party range"
            if i in self._egg_slots():
                return f"switch target {i} is an EGG"
            if i not in self._alive_slots():
                return f"switch target {i} is fainted"
            blocked = self.switch_blocked_reason()
            if blocked is not None:
                return blocked
        elif kind == "flee":
            return self._flee_blocked_reason()
        elif kind == "item":
            name = arg or "POTION"
            if self.bag_item_index(name) is None:
                return f"item {name!r} not in bag"
        elif kind == "ball":
            name = arg or "POKE BALL"
            if self.bag_item_index(name, pocket="balls") is None:
                return f"ball {name!r} not in bag"
        elif kind == "attack" and arg is not None:
            moves = me["moves"]
            if not isinstance(arg, int) or not 0 <= arg < len(moves):
                return f"attack slot {arg!r} out of range"
            if moves[arg][1] == 0:
                return f"attack slot {arg} has no PP"
        return None

    WEDGE_REPS = 3              # identical snapshots before escalating
    WEDGE_CONFIRM_FRAMES = 600  # a real freeze stays frozen this long

    def _wedge_snapshot(self, rows):
        """State fingerprint for freeze detection: the visible text plus
        both combatants' vitals. Animations hold the text layer still for
        dozens of frames but always move one of these soon after; a real
        wedge changes none of them."""
        try:
            me, enemy = self.me(), self.enemy()
            vitals = (me["species"], me["hp"], enemy["species"], enemy["hp"])
        except Exception:
            vitals = None
        return (tuple(rows), battle_menu_up(rows), vitals)

    LOCK_WAIT_FRAMES = 6000     # a forced turn resolves long before this
    STALL_SUBSTITUTE = 2        # no-change turns before trying something else
    STALL_STRIKES = 5           # no-change turns before bailing out

    def _vitals(self):
        """"Did that turn change anything?" fingerprint: both mons'
        species and HP plus which party slot is out. Cheap, and blind to
        the text layer -- a refused RUN/SWITCH repaints the menu but moves
        none of these."""
        try:
            me, enemy = self.me(), self.enemy()
        except Exception:
            return None
        return (me.get("species"), me.get("hp"), me.get("party_slot"),
                enemy.get("species"), enemy.get("hp"))

    @staticmethod
    def _stall_alternative(act, me, nth=1):
        """A DIFFERENT action from one that has already changed nothing
        twice. Anything that is not an attack degrades to attacking; a
        stalled attack rotates onto another move slot that still has PP."""
        kind = act[0] if isinstance(act, tuple) else act
        if kind != "attack":
            return "attack"
        slots = [i for i, (_m, pp) in enumerate(me.get("moves", ())) if pp]
        if len(slots) > 1:
            return ("attack", slots[nth % len(slots)])
        return "attack"

    WEDGE_RECOVERIES = 1        # recovery attempts before abandoning

    def _recover_to_action_menu(self, rows):
        """Put an unchanging screen back into a KNOWN state without ever
        re-sending the turn's action, and say what was done.

        A nested battle menu left open -- the party list plus its
        SWITCH/STATS/CANCEL box -- is exactly the shape the live wedge
        froze in, and A-mashing it only re-triggers whatever the engine
        already refused. So: clear a refusal prompt if one is up,
        otherwise B out of the party menu to the action menu, and fall
        back to the plain B re-sync for anything else."""
        if self._recall_refused(rows):
            self.switch_refused = True
            self._dismiss_refusal()
            self._exit_party_menu()
            return "dismissed a \"can't be recalled\" refusal"
        if self._submenu_up(rows) or (not battle_menu_up(rows)
                                      and any("CANCEL" in r for r in rows)):
            self._exit_party_menu()
            return "backed out of an open party menu"
        self.menu.press("B:4 .:12")
        return "pressed B to re-sync"

    # stat-page fingerprint: the level-up stat sheet lists these labels
    STAT_PAGE_LABELS = ("ATTACK", "DEFENSE", "SPCL", "SPEED")

    @staticmethod
    def _levelup_screen(rows):
        """Benign level-up pages -- the 'grew to level N!' announcement and
        the stat sheet it opens -- hold text AND vitals perfectly still,
        exactly like a freeze, but a plain A advances them. They are
        PROGRESS: never confirm-wait on them, never print the wedge
        diagnostic (the pt5c grind spammed dozens of frozen-screen dumps
        on these). A truly stuck level-up page is still bounded by the
        caller's frame cap."""
        joined = " ".join(rows).upper()
        if "GREW TO" in joined:
            return True
        return sum(1 for s in Battle.STAT_PAGE_LABELS if s in joined) >= 3

    def _log_wedge_diag(self, rows):
        log.warning("[battle diagnostic] frozen screen (state unchanged):")
        for r in rows:
            if r.strip():
                log.warning("  | %s", r)
        try:
            me, enemy = self.me(), self.enemy()
            log.warning(
                "[battle diagnostic] me=%s L%d %d/%d enemy=%s L%d %d/%d",
                me["name"], me["level"], me["hp"], me["max_hp"],
                enemy["name"], enemy["level"], enemy["hp"], enemy["max_hp"])
        except Exception as err:
            log.warning("[battle diagnostic] vitals unavailable: %s", err)

    def play(self, policy=None, max_frames=120000, potion_frac=0.3,
             want_nickname=False, text_handler=None):
        """Fight the whole battle. `policy(rows, me, enemy)` may return one
        of 'attack', ('attack', move_idx), 'flee', ('ball', name),
        ('item', name), ('switch', party_idx); defaults to smart damage +
        auto-POTION + fleeing hopeless wild fights. Returns 'won' | 'fled'
        | 'caught' | 'wipe' | 'timeout' | 'naming' | 'stuck' | 'wedged' |
        'stalled'.
        Identity: policies SHOULD match their roster on me['nickname'] /
        me['party_slot'] -- me['name'] is the SPECIES name and silently
        changes on mid-battle/mid-grind EVOLUTION (the pt5c TOGEPI policy
        matched 'TOGEPI', PEBBLE evolved to TOGETIC, and the mon Struggled
        to death for 5 cycles).
        Every pass classifies the turn three ways:
        (a) ours -- the battle menu is up, the policy is asked, the action
            is executed and the turn resolves;
        (b) FORCED -- locked_turn() reports the engine is mid-sequence
            (recharge / rampage / charge / rollout draw no menu at all;
            Encore / Bide draw the menu but pick the move). The harness
            ticks frames and re-polls; it never re-sends an action and
            never charges the frozen-screen counter for those frames;
        (c) STUCK -- the menu is up, the action was accepted, and nothing
            moved. A different action is substituted, and after
            STALL_STRIKES fruitless turns play() returns 'stalled'.
        Policy actions that cannot work this turn (switch to a fainted/
        EGG/missing/TRAPPED slot, a RUN the engine refuses, item or ball
        not in the bag, attack slot empty or dry) are substituted with the
        default policy's pick after one warning; each substitution feeds
        the wedge guard, so a policy that keeps returning invalid actions
        degrades to plain attacks within two turns. A screen+vitals
        fingerprint that stays frozen despite recovery returns 'wedged'
        instead of looping to the frame cap.
        want_nickname: answer YES to the post-catch prompt and hand off to
        the caller at the keyboard instead of declining it.
        text_handler(rows): optional modal-text hook (e.g. the level-up
        move-learning flow). Called before generic text handling; return
        True when it consumed the frame's input."""
        f0 = self.emu.frame
        last_action = None
        caught = False
        was_menu = False
        fails = 0     # consecutive misfired actions (wedge guard)
        warned_invalid = set()   # invalid policy actions already logged
        wedge_snap = None        # freeze-detection fingerprint
        wedge_reps = 0           # consecutive identical fingerprints
        wedge_fixes = 0          # recovery attempts spent on this freeze
        diag_prints = 0          # frozen-screen diagnostics printed (cap 2)
        lock_since = None        # frame the current forced turn started
        lock_logged = None       # forced-turn state already announced
        stalls = 0               # consecutive turns that changed nothing
        stall_act = None         # the action those turns kept re-sending
        while self.active():
            if self.emu.frame - f0 > max_frames:
                return "timeout"
            if "was caught" in "".join(self.emu.screen_text()):
                caught = True
            rows = self.emu.screen_text()
            # (b) FORCED turn: CheckPlayerLockedIn (engine/battle/core.asm)
            # makes BattleTurn skip the whole FIGHT/PKMN/PACK/RUN menu, so
            # there is no action to send and the unchanged screen is the
            # engine working, not a wedge. Tick frames, re-poll, and keep
            # the freeze counters out of it.
            lock = self.locked_turn()
            if lock is None:
                lock_since, lock_logged = None, None
            elif lock_since is None:
                lock_since = self.emu.frame
            if (lock in self.MENU_SKIP_LOCKS and not battle_menu_up(rows)
                    and self.emu.frame - lock_since < self.LOCK_WAIT_FRAMES):
                if lock_logged != lock:
                    lock_logged = lock
                    log.info("[battle] forced turn (%s): the engine owns "
                             "this turn, waiting it out", lock)
                wedge_snap, wedge_reps, wedge_fixes = None, 0, 0
                was_menu = False
                self.menu.press("A:2 .:8")   # advance the forced-turn text
                continue
            snap = self._wedge_snapshot(rows)
            if snap == wedge_snap:
                wedge_reps += 1
            else:
                wedge_snap, wedge_reps, wedge_fixes = snap, 0, 0
            if wedge_reps >= self.WEDGE_REPS and not self._levelup_screen(rows):
                # Same text AND same vitals over several passes: either a
                # long animation or a genuine freeze. Confirm before
                # escalating -- an animation moves within the window.
                if self.menu.wait_for(
                        lambda r: self._wedge_snapshot(r) != wedge_snap,
                        timeout_frames=self.WEDGE_CONFIRM_FRAMES):
                    wedge_snap, wedge_reps = None, 0
                    continue
                if wedge_fixes < self.WEDGE_RECOVERIES:
                    # diagnostic capped at two prints total: the first
                    # freeze dumps state, everything after is one line
                    # (the live wedge printed 200+ identical dumps)
                    if diag_prints == 0:
                        self._log_wedge_diag(rows)
                        diag_prints = 1
                    elif diag_prints == 1:
                        log.warning("[battle diagnostic] suppressing "
                                    "further identical diagnostics")
                        diag_prints = 2
                    wedge_fixes += 1
                    # never re-send the action: get back to a known state
                    log.warning("[battle] unchanged screen: %s (recovery "
                                "%d/%d)", self._recover_to_action_menu(rows),
                                wedge_fixes, self.WEDGE_RECOVERIES)
                    was_menu = False
                    continue
                # recovery didn't move the fingerprint: structured bail
                if diag_prints == 1:
                    log.warning("[battle diagnostic] suppressing further "
                                "identical diagnostics")
                    diag_prints = 2
                return "wedged"
            if not battle_menu_up(rows):
                was_menu = False
                if naming_keyboard_up(rows):
                    # "Give a nickname?" answered YES: stop mashing A --
                    # every press adds a junk character. Caller handles it.
                    return "naming"
                if text_handler and text_handler(rows):
                    # modal flow (level-up move learning) handled by caller
                    continue
                if self._forced_switch_up(rows):
                    self._drive_forced_switch()
                    continue
                joined = "".join(rows).upper()
                if self._my_move_list_up(rows):
                    # the move list is open outside attack() (e.g. cursor
                    # parked on a DISABLED move): A would re-pick it and
                    # loop forever -- back out to the main menu
                    self.menu.press("B:6 .:12")
                    continue
                if not want_nickname and (
                        "GIVE A NICKNAME" in joined
                        or ("YES" in joined and "NO" in joined)):
                    # nickname prompt, no name requested: decline (B=NO)
                    self.menu.press("B:6 .:12")
                    continue
                self.menu.press("A:2 .:8")     # advance battle text
                continue
            if not was_menu:
                # menu just appeared: let its input loop spin up so the
                # first A press isn't swallowed by setup frames
                was_menu = True
                self._confirm_menu_open()
                continue
            me, enemy = self.me(), self.enemy()
            lock = self.locked_turn()
            forced = lock in self.AUTO_MOVE_LOCKS
            substituted = False
            if forced:
                # (b) with the menu up: ParsePlayerAction throws our move
                # pick away and replays wLastPlayerMove, so confirming
                # FIGHT is the entire action. Asking the policy here would
                # only invite an action the engine is about to ignore.
                if lock_logged != lock:
                    lock_logged = lock
                    log.info("[battle] forced move (%s): confirming FIGHT, "
                             "the engine picks the move", lock)
                act = "attack"
            else:
                act = policy(rows, me, enemy) if policy else None
                if act is not None:
                    why = self._invalid_action_reason(act, me)
                    if why is not None:
                        # impossible action: burn zero turns on it. One
                        # warning per distinct mistake, then substitute the
                        # default -- never re-ask the policy mid-turn.
                        if why not in warned_invalid:
                            warned_invalid.add(why)
                            log.warning("[battle] policy action %r "
                                        "impossible (%s): substituting "
                                        "default", act, why)
                        fails += 1   # counts against the wedge guard below
                        substituted = True
                        act = None
                if act is None:
                    act = self._default_policy(me, enemy, potion_frac)
                    why = self._invalid_action_reason(act, me)
                    if why is not None:
                        # the DEFAULT is impossible too (a trapped mon told
                        # to flee, an empty bag). Attacking always works,
                        # and re-sending the refusal is the wedge itself.
                        if why not in warned_invalid:
                            warned_invalid.add(why)
                            log.warning("[battle] default action %r "
                                        "impossible (%s): attacking "
                                        "instead", act, why)
                        act = "attack"
                if fails >= 2 and act != "flee":
                    # wedge guard: an action that misfired twice in a row
                    # will misfire forever (bad item lookup, unreachable
                    # menu row). Degrade to a plain attack so the battle
                    # always progresses instead of flailing in the pack
                    # (which can even toss items) until the frame cap.
                    act = "attack"
                if stall_act is not None and act == stall_act and \
                        stalls >= self.STALL_SUBSTITUTE:
                    # (c) this exact action has changed nothing twice: it
                    # will change nothing again. Try a different one.
                    alt = self._stall_alternative(act, me, stalls)
                    log.warning("[battle] %r changed nothing for %d turns: "
                                "substituting %r", act, stalls, alt)
                    act = alt
            kind = act[0] if isinstance(act, tuple) else act
            arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
            before_vitals = self._vitals()
            ok = True
            if forced:
                ok = self.attack_forced()
            elif kind == "flee":
                ok = self.flee()
            elif kind == "ball":
                ok = self.throw_ball(arg or "POKE BALL")
            elif kind == "item":
                ok = self.use_battle_item(arg or "POTION")
            elif kind == "switch":
                ok = self.switch_to(arg if arg is not None else 1)
            else:
                ok = self.attack(arg if isinstance(arg, int) else None)
            if not ok:
                # a menu interaction misfired: back out and re-sync. The
                # repainted menu with unchanged vitals is retry progress,
                # not a freeze -- reset the wedge fingerprint (the Morty
                # stall 'wedged' out here before the fails counter could
                # degrade to attacks); this lane is bounded by fails, not
                # the freeze detector (forced attack at 2, 'stuck' at 12).
                fails += 1
                if fails >= 12:
                    return "stuck"   # even plain attacks misfire: bail
                self.menu.press("B:4 .:12")
                was_menu = False
                wedge_snap, wedge_reps = None, 0
                continue
            if not substituted:
                # a substituted turn keeps its fail count so a policy that
                # stays invalid degrades to plain attacks within two turns
                fails = 0
            last_action = kind
            # let the turn resolve back to the main menu (or the battle end)
            self.menu.wait_for(
                lambda r: battle_menu_up(r) or not self.active(),
                timeout_frames=2000)
            was_menu = False
            # (c) the MENU accepted the action but the battle state did not
            # move at all. A refused RUN or SWITCH does exactly this:
            # BattleMenu_Run's .cant_escape and TryPlayerSwitch's .trapped
            # bounce back to their own menu without burning a turn, so the
            # naive loop re-sends forever (the live ONIX/BIND 90k-frame
            # timeout). Count the strike, and bail with a distinct reason
            # long before the frame cap.
            after_vitals = self._vitals()
            if after_vitals is not None and after_vitals == before_vitals:
                stalls += 1
                stall_act = act
                if stalls >= self.STALL_STRIKES:
                    log.warning("[battle] no action changed the battle "
                                "state in %d turns (last %r): bailing",
                                stalls, act)
                    return "stalled"
            else:
                stalls, stall_act = 0, None
        if caught or "was caught" in "".join(self.emu.screen_text()):
            return "caught"
        if last_action == "flee" and self.party_alive():
            return "fled"
        return "won" if self.party_alive() else "wipe"

    def _default_policy(self, me, enemy, potion_frac):
        frac = me["hp"] / max(me["max_hp"], 1)
        wild = self.emu.read_u8("wBattleMode") == 1
        if me["hp"] <= 0:
            return "flee" if wild else "attack"
        if frac < potion_frac and self.bag_item_index("POTION") is not None:
            return ("item", "POTION")
        if wild and frac < 0.25 and enemy["level"] > me["level"]:
            return "flee"
        return "attack"
