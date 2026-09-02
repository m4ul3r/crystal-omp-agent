"""claude-wren leg 4 battle-item fixes: the in-battle item executor drives
the pack on live WRAM cursor reads, confirms through the description /
"Use on which PM?" pages with per-press state verification, succeeds ONLY
on a bag-count decrement, and on any stall Bs out to the battle menu and
reports the action failed (the live Morty repro parked on the SUPER POTION
description until the wedge cap fired). Item-name resolution accepts every
reasonable spelling through the shared norm_item normalizer."""
import logging
import re
from types import SimpleNamespace

import pytest

from crystalagent.battle import (
    Battle, bag_item_index, bag_quantity, norm_item, _norm_item)

pytestmark = pytest.mark.unit

ITEM_NAMES = {14: "SUPER POTION", 9: "ANTIDOTE"}
POCKET = (("SUPER POTION", 14, 3), ("ANTIDOTE", 9, 2))
MENU_ROWS = ["GATOR  L18", "", "FIGHT  PKMN", "PACK   RUN"]
DESC_ROWS = ["", "Restores POKeMON", "HP by 50."]


class PackSym:
    TABLE = {"wItems": (1, 0x100)}

    def __getitem__(self, key):
        return self.TABLE[key]


class PackEmu:
    """Battle-pack state machine: battle menu -> pocket list -> item
    description page -> optional USE/QUIT popup -> "Use on which PM?"
    party list -> consumption. `stall_at` names a page whose A presses
    are dead (the live Morty freeze); B always works."""

    def __init__(self, items=POCKET, start_pos=0, pages=("desc",),
                 stall_at=None, row_text=None):
        self.frame = 0
        self.items = [list(it) for it in items]
        self.state = "menu"
        self.pos = start_pos          # pocket cursor, absolute index
        self.party_pos = 0            # party cursor, 0-based
        self.pages = list(pages)      # A-through pages between pocket+party
        self.page_i = 0
        self.stall_at = stall_at
        self.row_text = row_text      # force every pocket row's text
        self.used_on = None
        self.pressed = []
        self.sym = PackSym()

    # -- reads ---------------------------------------------------------
    def read_u8(self, name):
        if name == "wNumItems":
            return len(self.items)
        if name == "wMenuScrollPosition":
            # a stale pocket scroll survives into the party list: any
            # scroll_abs steering there lands on the wrong row
            return 3 if self.state == "party" else 0
        if name == "wMenuCursorY":
            if self.state == "pocket":
                return self.pos + 1
            if self.state == "party":
                return self.party_pos + 1
            return 1
        if name == "wJumptableIndex":
            in_pack = self.state in ("pocket",) + tuple(self.pages)
            return 2 if in_pack else 0xFF
        if name == "wCurBattleMon":
            return 0                  # the lead is the active mon here
        raise KeyError(name)

    def read(self, loc, n):
        bank, addr = loc
        base_bank, base = self.sym["wItems"]
        assert bank == base_bank
        raw = []
        for _name, iid, qty in self.items:
            raw += [iid, qty]
        raw.append(0xFF)
        off = addr - base
        return bytes(raw[off:off + n])

    def screen_text(self):
        if self.state == "menu":
            return list(MENU_ROWS)
        if self.state == "pocket":
            rows = []
            for i, (name, _iid, qty) in enumerate(self.items):
                cur = "\u25b6" if i == self.pos else " "
                rows.append(cur + (self.row_text or name))
                rows.append(f"        \u00d7  {qty}")
            rows.append(" CANCEL")
            return rows + DESC_ROWS       # description pane at the bottom
        if self.state == "desc":
            return list(DESC_ROWS)
        if self.state == "popup":
            return ["\u25b6USE", " QUIT"]
        if self.state == "party":
            c = ["\u25b6" if self.party_pos == i else " " for i in range(2)]
            return ["Use on which PM?",
                    f"{c[0]}GATOR      12/  24",
                    f"{c[1]}PIDGEY      9/  18",
                    " CANCEL"]
        return ["GATOR recovered", "50 HP!"]      # "used"

    # -- input ---------------------------------------------------------
    def run_sequence(self, steps):
        for buttons, frames in steps:
            self.frame += frames
            for b in sorted(buttons):
                self._press(b)
        return 0

    def _press(self, btn):
        self.pressed.append((self.state, btn))
        if btn == "b":
            if self.state in ("desc", "popup", "party"):
                self.state = "pocket"
            elif self.state == "pocket":
                self.state = "menu"
            return
        if self.state == self.stall_at:
            return                     # dead page: the live Morty freeze
        if btn in ("down", "up"):
            d = 1 if btn == "down" else -1
            if self.state == "pocket":
                self.pos = max(0, min(len(self.items) - 1, self.pos + d))
            elif self.state == "party":
                self.party_pos = max(0, min(1, self.party_pos + d))
            return
        if btn != "a":
            return
        if self.state == "pocket":
            self.page_i = 0
            self.state = self.pages[0] if self.pages else "party"
        elif self.state in ("desc", "popup"):
            self.page_i += 1
            self.state = (self.pages[self.page_i]
                          if self.page_i < len(self.pages) else "party")
        elif self.state == "party":
            self.used_on = self.party_pos
            self.items[self.pos][2] -= 1       # consume the selected item
            self.state = "used"


class ItemHarness(Battle):
    """Battle with a real Menus over PackEmu; only the battle-menu entry
    and the pocket hop are faked (they are upstream of the fixed code)."""

    def __init__(self, emu):
        names = SimpleNamespace(items=dict(ITEM_NAMES), moves={}, species={})
        super().__init__(emu, names, None)

    def _battle_option(self, n, max_steps=8):
        assert n == 3                  # PACK
        self.emu.state = "pocket"
        return True

    def _goto_pocket(self, pocket, timeout_frames=500):
        return True


# -- (a) description page confirmed, bag decremented, turn succeeds -----------

def test_item_flow_confirms_description_and_decrements_bag():
    emu = PackEmu(start_pos=1)         # persisted cursor NOT on the item
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION") is True
    assert emu.items[0][2] == 2                    # bag decremented by one
    assert emu.used_on == 0                        # target slot confirmed
    assert emu.state == "used"
    # climbed UP on live WRAM (a DOWN-only walk could never reach row 0)
    assert ("pocket", "up") in emu.pressed
    assert ("desc", "a") in emu.pressed            # description page advanced


def test_item_flow_drives_use_popup_page():
    emu = PackEmu(pages=("desc", "popup"))
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION") is True
    assert ("popup", "a") in emu.pressed           # USE confirmed
    assert emu.items[0][2] == 2


def test_item_flow_targets_party_slot_on_wram_not_scroll_abs():
    """wMenuScrollPosition is stale (pocket scroll) on the party list:
    slot 1 must still be reached via wMenuCursorY steering."""
    emu = PackEmu()
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION", target_slot=1) is True
    assert emu.used_on == 1


# -- (b) stalled pages: B out, action reported failed, bounded ----------------

def test_stalled_description_page_bails_out_and_fails():
    """The Morty repro: cursor reaches SUPER POTION, the description page
    never advances. The executor must NOT hang: B out to the battle menu,
    consume nothing, report False."""
    emu = PackEmu(stall_at="desc")
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION") is False
    assert emu.items[0][2] == 3                    # nothing consumed
    assert emu.state == "menu"                     # B'd all the way out
    assert emu.frame < 3000                        # bounded, no wedge loop


def test_swallowed_party_confirm_bails_without_consuming():
    emu = PackEmu(stall_at="party")
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION") is False
    assert emu.items[0][2] == 3
    assert emu.state == "menu"


def test_wrong_screen_row_never_blind_confirms():
    """WRAM index says SUPER POTION but the highlighted TEXT is another
    item: refuse before the A (select_abs once burned ~9 potions)."""
    emu = PackEmu(row_text="ANTIDOTE")
    h = ItemHarness(emu)
    assert h.use_battle_item("SUPER POTION") is False
    assert emu.used_on is None and emu.items[0][2] == 3


# -- (b) play(): failed item feeds substitution, never 'wedged' ---------------

class PlayHarness(ItemHarness):
    """play() runs for real over PackEmu; the item executor is the real
    (stalling) one, attack is a fake that ends the battle."""

    def __init__(self, emu):
        super().__init__(emu)
        self.turns_left = 1
        self.attacks = 0

    def active(self):
        return self.turns_left > 0

    def me(self):
        return {"species": 159, "name": "GATOR", "level": 18, "hp": 20,
                "max_hp": 24, "types": [1], "moves": [(10, 5)]}

    def enemy(self):
        return {"species": 92, "name": "GASTLY", "level": 21, "hp": 15,
                "max_hp": 15, "types": [7]}

    def party_alive(self):
        return True

    def attack(self, move_idx=None):
        self.attacks += 1
        self.turns_left -= 1
        return True


def test_stalled_item_degrades_to_attack_instead_of_wedging(caplog):
    """Two failed item turns feed the fails counter; turn 3 is a forced
    attack. Before the fix the unchanged menu+vitals fingerprint tripped
    the freeze detector and play() returned 'wedged' after ONE attempt."""
    emu = PackEmu(stall_at="desc")
    h = PlayHarness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=lambda r, me, en: ("item", "SUPER POTION"))
    assert outcome == "won"
    assert h.attacks == 1                          # substitution took over
    assert emu.items[0][2] == 3                    # stall never consumed
    item_tries = [s for s, b in emu.pressed if s == "desc"]
    assert item_tries                              # executor really ran
    assert not any("frozen screen" in r.getMessage() for r in caplog.records)


# -- (c) normalizer: every reasonable spelling hits the same bag entry --------

@pytest.mark.parametrize("spelling", [
    "SUPER POTION", "SUPERPOTION", "Super Potion", "super-potion",
    "SUPER  POTION"])
def test_normalizer_resolves_all_spellings(spelling):
    emu = PackEmu()
    names = SimpleNamespace(items=dict(ITEM_NAMES))
    assert norm_item(spelling) == "SUPERPOTION"
    assert bag_item_index(emu, names, spelling) == 0
    assert bag_quantity(emu, names, spelling) == 3


def test_normalizer_shared_export_for_trek():
    """trek.py's legacy `_norm_item` import stays valid and IS the shared
    normalizer; POKé glyph spellings still collapse."""
    assert _norm_item is norm_item
    assert norm_item("# BALL") == norm_item("POK\u00e9 BALL") == "POKEBALL"


def test_flow_uses_normalized_names_end_to_end():
    """The executor itself resolves a spaceless policy spelling against a
    two-word ROM name and screen row."""
    emu = PackEmu()
    h = ItemHarness(emu)
    assert h.use_battle_item("SuperPotion") is True
    assert emu.items[0][2] == 2
