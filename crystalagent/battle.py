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

import re
from pathlib import Path

from .menus import Menus, battle_menu_up, naming_keyboard_up, _cursor_x

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
        m = 1.0
        for t in def_types:
            m *= self.matchups.get((atk_type, t), 1.0)
        return m


def _norm_item(name):
    """Normalize an item name for lookup: the repo writes the POKé glyph
    as '#' ("# BALL"), screens show "POKé BALL"; callers say "POKE BALL"."""
    return re.sub(r"[^A-Z0-9]", "",
                  name.replace("#", "POKE").replace("é", "e")
                  .replace("\x80", "e").upper())


def bag_item_index(emu, names, item_name, pocket="items"):
    """0-based position of an item inside a pack pocket's WRAM list.
    Entries are (id, quantity) pairs."""
    if pocket == "balls":
        count_sym, list_sym = "wNumBalls", "wBalls"
    else:
        count_sym, list_sym = "wNumItems", "wItems"
    count = min(emu.read_u8(count_sym), 20)
    want = _norm_item(item_name)
    got = next((i for i, n in names.items.items()
                if _norm_item(n) == want), None)
    if got is None or count == 0:
        return None
    bank, addr = emu.sym[list_sym]
    raw = emu.read((bank, addr), count * 2)
    for i in range(count):
        if raw[i * 2] == got:
            return i
    return None


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

    # -- observation -------------------------------------------------------

    def active(self):
        return self.emu.read_u8("wBattleMode") != 0

    def _struct_reader(self, base_label):
        sym = self.emu.sym
        bank, base = sym[base_label]
        off = lambda f: sym.offset(base_label + f, base_label)
        return lambda f, n=1: self.emu.read((bank, base + off(f)), n)

    def me(self):
        rd = self._struct_reader("wBattleMon")
        moves = list(rd("Moves", 4))
        pps = list(rd("PP", 4))
        species = rd("Species")[0]
        return {
            "species": species,
            "name": self.names.species.get(species, "?"),
            "level": rd("Level")[0],
            "hp": int.from_bytes(rd("HP", 2), "big"),
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "types": list(rd("Type", 2)),
            "moves": [(m, p) for m, p in zip(moves, pps) if m],
        }

    def enemy(self):
        rd = self._struct_reader("wEnemyMon")
        species = rd("Species")[0]
        return {
            "species": species,
            "name": self.names.species.get(species, "?"),
            "level": rd("Level")[0],
            "hp": int.from_bytes(rd("HP", 2), "big"),
            "max_hp": int.from_bytes(rd("MaxHP", 2), "big"),
            "types": list(rd("Type", 2)),
        }

    def best_move(self):
        """Slot index of the highest expected-damage move with PP left;
        None means every slot is dry (Struggle territory)."""
        me, enemy = self.me(), self.enemy()
        best, best_score = None, -1.0
        for i, (mid, pp) in enumerate(me["moves"]):
            if pp == 0 or mid not in self.data.moves:
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
        if not self._battle_option(1):
            return False
        if not self._wait_move_menu():
            return False
        if move_idx is None:
            move_idx = self.best_move()
        if move_idx is None:          # out of PP everywhere: mash A = Struggle
            self.menu.press("A:2 .:12")
            return True
        return self._move_menu_select(move_idx)

    def flee(self):
        return self._battle_option(4)

    def throw_ball(self, ball="POKE BALL"):
        """From the main battle menu: PACK -> balls pocket -> ball -> USE."""
        idx = self.bag_item_index(ball, pocket="balls")
        if idx is None or not self._battle_option(3):
            return False
        if not self._goto_pocket("balls"):
            return self._cancel_pack()
        if not self.menu.select_abs(idx):
            return self._cancel_pack()
        if not self.menu.wait_for_label("USE") or \
                not self.menu.select_label("USE", max_presses=4):
            return self._cancel_pack()
        return True

    def use_battle_item(self, item_name, target_slot=0):
        """Main-menu PACK -> items pocket -> item -> USE -> pick target."""
        idx = self.bag_item_index(item_name, pocket="items")
        if idx is None or not self._battle_option(3):
            return False
        if not self._goto_pocket("items"):
            return self._cancel_pack()
        if not self.menu.select_abs(idx):
            return self._cancel_pack()
        if not self.menu.wait_for_label("USE") or \
                not self.menu.select_label("USE", max_presses=4):
            return self._cancel_pack()
        # healing items pop the party menu; pick the target mon
        if self.menu.wait_for(
                lambda r: any("CANCEL" in x for x in r), timeout_frames=400):
            self.menu.select_abs(target_slot)
            self.menu.press("A:6 .:25")
        return True

    def switch_to(self, party_index):
        """From the main battle menu: PKMN -> slot -> SWITCH."""
        if not self._battle_option(2):
            return False
        if not self.menu.select_abs(party_index):
            return self._back_out()
        if not self.menu.wait_for_label("SWITCH", timeout_frames=300) or \
                not self.menu.select_label("SWITCH", max_presses=4):
            return self._back_out()
        return True   # "Switch this pokémon?" resolves through text flow

    def _goto_pocket(self, pocket, timeout_frames=500):
        return goto_pocket(self.menu, pocket, timeout_frames)

    def _cancel_pack(self):
        return cancel_pack(self.menu)

    def _back_out(self):
        self.menu.press("B:4 .:10")
        self.menu.press("B:4 .:10")
        return False

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

    def play(self, policy=None, max_frames=120000, potion_frac=0.3,
             want_nickname=False):
        """Fight the whole battle. `policy(rows, me, enemy)` may return one
        of 'attack', ('attack', move_idx), 'flee', ('ball', name),
        ('item', name), ('switch', party_idx); defaults to smart damage +
        auto-POTION + fleeing hopeless wild fights. Returns
        'won' | 'fled' | 'caught' | 'wipe' | 'timeout' | 'naming'.
        want_nickname: answer YES to the post-catch prompt and hand off to
        the caller at the keyboard instead of declining it."""
        f0 = self.emu.frame
        last_action = None
        caught = False
        was_menu = False
        while self.active():
            if self.emu.frame - f0 > max_frames:
                return "timeout"
            if "was caught" in "".join(self.emu.screen_text()):
                caught = True
            rows = self.emu.screen_text()
            if not battle_menu_up(rows):
                was_menu = False
                if naming_keyboard_up(rows):
                    # "Give a nickname?" answered YES: stop mashing A --
                    # every press adds a junk character. Caller handles it.
                    return "naming"
                joined = "".join(rows).upper()
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
            act = policy(rows, me, enemy) if policy else None
            if act is None:
                act = self._default_policy(me, enemy, potion_frac)
            kind = act[0] if isinstance(act, tuple) else act
            arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
            ok = True
            if kind == "flee":
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
                # a menu interaction misfired: back out and re-sync
                self.menu.press("B:4 .:12")
                was_menu = False
                continue
            last_action = kind
            # let the turn resolve back to the main menu (or the battle end)
            self.menu.wait_for(
                lambda r: battle_menu_up(r) or not self.active(),
                timeout_frames=2000)
            was_menu = False
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
