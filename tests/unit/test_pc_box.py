"""Bill's PC: box reads out of SRAM, and a deposit that moves ONE mon.

Guards FUCK_I_MESSED_UP.md #72 (a blind A loop on the deposit list put
five of six party members in the box, including the run's only real
fighter) and #73 (those lists paint no cursor glyph at all, so nothing on
the tilemap tracks the selection except the info panel).

The engine facts these pin, all from engine/pokemon/bills_pc.asm:
  - the list selection is wBillsPC_CursorPosition + wBillsPC_ScrollPosition
    (BillsPC_LoadMonStats reads exactly that sum),
  - a completed deposit resets the jumptable to .Init and both of those to
    0 -- the list RE-ARMS on the next party member,
  - the current box lives in SRAM bank 1 at sBox.
"""
import pytest

import trek
from trek import Driver
from crystalagent.menus import Menus
from crystalagent.state import box_mons, box_state

pytestmark = pytest.mark.unit

# sBox layout, from pokecrystal.sym (SRAM bank 1)
SBANK = 1
SBOX_COUNT = 0xAD10
SBOX_MON1 = 0xAD26          # sBoxMon1Species
SBOX_STRIDE = 0x20          # BOXMON_STRUCT_LENGTH
SBOX_LEVEL = 0x1F           # sBoxMon1Level - sBoxMon1Species
SBOX_NICKS = 0xB082
NICK_LEN = 11


class FakeSym(dict):
    def offset(self, a, b):
        return self[a][1] - self[b][1]


class FakeCharmap:
    @staticmethod
    def decode(raw):
        return "".join(chr(b - 0x80) for b in raw if b not in (0, 0x50))


class FakeNames:
    species = {118: "GOLDEEN", 175: "TOGEPI", 74: "GEODUDE"}


class FakeEmu:
    """Byte-addressed memory; SRAM and WRAM share one flat array here."""

    def __init__(self):
        self.mem = bytearray(0x10000)
        self.charmap = FakeCharmap()
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {}
        self.sym = FakeSym({
            "sBoxCount": (SBANK, SBOX_COUNT),
            "sBoxMon1Species": (SBANK, SBOX_MON1),
            "sBoxMon2Species": (SBANK, SBOX_MON1 + SBOX_STRIDE),
            "sBoxMon1Level": (SBANK, SBOX_MON1 + SBOX_LEVEL),
            "sBoxMonNicknames": (SBANK, SBOX_NICKS),
        })

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, name):
        if name in self.sym:
            return self.mem[self.sym[name][1]]
        return self.u8.get(name, 0)

    def read(self, where, n=1):
        addr = self.sym[where][1] if isinstance(where, str) else where[1]
        return bytes(self.mem[addr:addr + n])


def _nick(s):
    return bytes(ord(c) + 0x80 for c in s) + b"\x50"


def boxed_emu(mons=((118, 24, "GOLDEEN"), (175, 5, "TOGEPI"))):
    emu = FakeEmu()
    emu.mem[SBOX_COUNT] = len(mons)
    for i, (species, level, nick) in enumerate(mons):
        base = SBOX_MON1 + i * SBOX_STRIDE
        emu.mem[base] = species
        emu.mem[base + SBOX_LEVEL] = level
        raw = _nick(nick)
        emu.mem[SBOX_NICKS + i * NICK_LEN:
                SBOX_NICKS + i * NICK_LEN + len(raw)] = raw
    return emu


# -- SRAM decode -------------------------------------------------------------

def test_box_mons_reads_the_current_box_out_of_sram():
    mons = box_mons(boxed_emu(), FakeNames())
    assert [(m["name"], m["level"], m["nickname"]) for m in mons] == [
        ("GOLDEEN", 24, "GOLDEEN"), ("TOGEPI", 5, "TOGEPI")]


def test_box_state_reports_capacity_and_1_based_box_number():
    emu = boxed_emu()
    emu.u8["wCurBox"] = 0x83        # engine masks $f then adds 1
    st = box_state(emu, FakeNames())
    assert st["box"] == 4 and st["count"] == 2 and st["capacity"] == 20


# -- the glyph-less list (#73) ----------------------------------------------

def pc_screen(species="GOLDEEN", level=24, list_nick="TOGEPI"):
    """A PC list screen: PCMonInfo's panel (species row 14, level row 12
    cols 0-7) plus a list entry drawn at col 9 of the SAME row 12."""
    rows = [" " * 20 for _ in range(18)]
    rows[12] = f" ᴸ{level:<3}  ♂ {list_nick}"[:20].ljust(20)
    rows[14] = f" {species}".ljust(20)
    rows[16] = " Choose a ᴾᴹ.".ljust(20)
    return rows


def test_pc_info_reads_species_and_level_from_the_panel():
    menus = Menus(FakeEmu())
    info = menus.pc_info(pc_screen("GOLDEEN", 24))
    assert info == {"name": "GOLDEEN", "level": 24}


def test_pc_info_level_ignores_the_list_column():
    """Row 12 carries the panel level at col 1 AND a list nickname from
    col 9 on; slicing to the panel is what keeps them apart."""
    rows = pc_screen("TOGEPI", 5, list_nick="GOLDEEN")
    assert Menus(FakeEmu()).pc_info(rows)["level"] == 5


def test_select_pc_mon_walks_until_the_panel_names_it():
    emu = FakeEmu()
    seq = [pc_screen("TOGEPI"), pc_screen("GEODUDE"), pc_screen("GOLDEEN")]
    emu.rows = seq[0]
    presses = []

    menus = Menus(emu)

    def press(s):
        presses.append(s)
        emu.rows = seq[min(len(presses), len(seq) - 1)]
    menus.press = press
    assert menus.select_pc_mon("GOLDEEN") is True
    assert presses == ["D:4 .:16", "D:4 .:16"]     # no A: never confirms


def test_select_pc_mon_gives_up_with_a_reason_when_the_list_pins():
    emu = FakeEmu()
    emu.rows = pc_screen("TOGEPI")
    menus = Menus(emu)
    menus.press = lambda s: None                   # panel never changes
    assert menus.select_pc_mon("GOLDEEN", max_presses=6) is False
    assert "pinned" in menus.last_reason


# -- deposit / withdraw refusals (nothing pressed) ---------------------------

def pc_driver(party, box_count=2, monkeypatch=None):
    d = Driver.__new__(Driver)
    d.emu = boxed_emu()
    d.names = FakeNames()
    d.emu.mem[SBOX_COUNT] = box_count
    d.settle = lambda **kw: None
    d.press = lambda seq: d.emu.tick(5)
    d.observe = lambda: {"party": list(party)}
    d.map_name = lambda: "VIOLET_POKECENTER_1F"
    d.pressed = []
    d._pc_open = lambda action: d.pressed.append(action) or True
    d._pc_cursor_to = lambda i, expect=None: d.pressed.append(("cursor", i)) \
        or True
    d._pc_exit = lambda **kw: True
    if monkeypatch is not None:
        # the held-item check (mail cannot be boxed) reads game_state
        monkeypatch.setattr(trek, "game_state", lambda emu, names: {
            "party": [{"nickname": m["nick"], "name": m["species"],
                       "item": None} for m in party]})
    return d


def mon(nick, species="GOLDEEN", level=20, egg=False):
    return {"nick": nick, "species": species, "level": level, "hp": 10,
            "max_hp": 10, "status": None, "moves": [], "egg": egg}


def test_deposit_refuses_an_unknown_name_without_pressing_anything():
    d = pc_driver([mon("PANIC"), mon("BUBBLES")])
    assert d.deposit("MEWTWO") is False
    assert d.last_pc_reason.startswith("no-such-mon")
    assert d.pressed == []


def test_deposit_refuses_the_last_mon():
    """The engine refuses too ("It's your last ᴾᴹ!"); refusing first keeps
    the PC from being opened for nothing."""
    d = pc_driver([mon("PANIC")])
    assert d.deposit("PANIC") is False
    assert d.last_pc_reason.startswith("last-mon")
    assert d.pressed == []


def test_deposit_refuses_a_full_box():
    d = pc_driver([mon("PANIC"), mon("BUBBLES")], box_count=20)
    assert d.deposit("PANIC") is False
    assert d.last_pc_reason.startswith("box-full")
    assert d.pressed == []


def test_withdraw_refuses_a_full_party():
    d = pc_driver([mon(f"M{i}") for i in range(6)])
    assert d.withdraw("GOLDEEN") is False
    assert d.last_pc_reason.startswith("party-full")
    assert d.pressed == []


def test_withdraw_refuses_a_name_the_box_does_not_hold():
    d = pc_driver([mon("PANIC")])
    assert d.withdraw("MEWTWO") is False
    assert d.last_pc_reason.startswith("not-in-box")


# -- one mon per call (#72) --------------------------------------------------

def test_deposit_confirms_once_and_verifies_against_the_observed_party(
        monkeypatch):
    party = [mon("PANIC"), mon("BUBBLES"), mon("SPARE")]
    d = pc_driver(party, monkeypatch=monkeypatch)
    d.emu.mem[SBOX_COUNT] = 2
    confirms = []

    def confirm(action):
        confirms.append(action)
        party.pop(2)                       # SPARE leaves the party
        d.emu.mem[SBOX_COUNT] += 1
        return True
    d._pc_confirm = confirm
    assert d.deposit("SPARE") is True
    assert confirms == ["DEPOSIT"]         # exactly one confirm, ever
    assert d.last_pc_reason == "deposited"
    assert [m["nick"] for m in d.observe()["party"]] == ["PANIC", "BUBBLES"]


def test_deposit_that_moves_two_mons_raises_instead_of_reporting_success(
        monkeypatch):
    """The #72 wound: the list re-arms, so a second confirm empties another
    slot. If that ever happens again it must be loud, not silent."""
    party = [mon("PANIC"), mon("BUBBLES"), mon("SPARE")]
    d = pc_driver(party, monkeypatch=monkeypatch)

    def confirm(action):
        del party[1:]                      # BUBBLES and SPARE both go
        d.emu.mem[SBOX_COUNT] += 2
        return True
    d._pc_confirm = confirm
    with pytest.raises(RuntimeError, match="2 mons moved"):
        d.deposit("SPARE")
    assert d.last_pc_reason == "over-applied"


def test_deposit_that_changed_nothing_fails_with_a_reason(monkeypatch):
    d = pc_driver([mon("PANIC"), mon("SPARE")], monkeypatch=monkeypatch)
    d._pc_confirm = lambda action: True    # pressed, nothing moved
    d._pc_prompt = lambda: "There's no room!"
    assert d.deposit("SPARE") is False
    assert d.last_pc_reason.startswith("unchanged")


def test_named_slot_matches_nickname_first_then_species():
    entries = [{"nick": "SPARE", "species": "GOLDEEN"},
               {"nick": "BUBBLES", "species": "TOGEPI"}]
    assert Driver._named_slot("BUBBLES", entries) == 1
    assert Driver._named_slot("goldeen", entries) == 0     # species, folded
    assert Driver._named_slot("MEWTWO", entries) is None
    # box_list()'s shape uses different keys for the same two things
    boxed = [{"nickname": "OLD", "name": "GROWLITHE"}]
    assert Driver._named_slot("growlithe", boxed) == 0


def test_pc_list_state_comes_from_the_engine_jumptable():
    d = pc_driver([mon("PANIC")])
    d.emu.u8["wJumptableIndex"] = Driver.PC_LIST_STATE
    d.emu.rows = pc_screen()
    assert d._pc_list_up() is True
    d.emu.u8["wJumptableIndex"] = 3        # submenu: not the list
    assert d._pc_list_up() is False


def test_pc_index_is_cursor_plus_scroll():
    d = pc_driver([mon("PANIC")])
    d.emu.u8["wBillsPC_CursorPosition"] = 2
    d.emu.u8["wBillsPC_ScrollPosition"] = 5
    assert d._pc_index() == 7


def test_pc_tile_kind_names_the_terminal():
    assert trek._tile_kind(0x93) == "pc"   # COLL_PC, journal #45


# -- box roster + CHANGE BOX (full-box throws bounce; journal, TALLY run) ----

def boxes_driver(cur=1, counts=(20, 3, 0xFF) + (0,) * 11, live=20):
    """A Driver over a FakeEmu with all 14 stored box counts mapped to
    unique flat addresses (real SRAM banks collide in a flat array)."""
    d = pc_driver([mon("PANIC"), mon("BUBBLES")], box_count=live)
    d.emu.u8["wCurBox"] = (cur - 1) & 0x0F
    for i, k in enumerate(counts, start=1):
        addr = 0x9000 + i
        d.emu.sym[f"sBox{i}Count"] = (2, addr)
        d.emu.mem[addr] = k
    return d


def test_boxes_reads_live_count_for_current_and_stored_for_the_rest():
    d = boxes_driver(cur=1, live=20)
    st = d.boxes()
    assert st["current"] == 1
    assert st["boxes"][0] == {"box": 1, "count": 20, "capacity": 20,
                              "full": True}       # live sBoxCount, not copy
    assert st["boxes"][1]["count"] == 3 and not st["boxes"][1]["full"]
    assert st["boxes"][2]["count"] == 0           # $ff = never initialized


def test_change_box_already_current_is_a_no_press_success():
    d = boxes_driver(cur=2, live=3)
    assert d.change_box(2) is True
    assert d.last_pc_reason == "already-current"
    assert d.pressed == []

def test_change_box_honors_a_full_target_but_refuses_a_bad_index():
    d = boxes_driver(cur=2, live=3)
    d._pc_closed = lambda: True
    d.find_tiles = lambda kind: []             # no PC: fails PAST the maths
    assert d.change_box(1) is False            # stored 20/20 -- still tried
    assert d.last_pc_reason.startswith("no-pc")
    assert d.change_box(15) is False
    assert d.last_pc_reason.startswith("bad-box")


def test_change_box_default_picks_the_first_box_with_space():
    """cur=1 is FULL (live), so the bare call must aim at box 2 -- proven
    by the refusal shape: it gets as far as needing a real screen."""
    d = boxes_driver(cur=1, live=20)
    d._pc_closed = lambda: True
    d.find_tiles = lambda kind: []                # no PC on this fake map
    assert d.change_box() is False
    assert d.last_pc_reason.startswith("no-pc")   # past the box maths


def test_change_box_all_full_refuses_before_touching_the_screen():
    d = boxes_driver(cur=1, counts=(20,) * 14, live=20)
    assert d.change_box() is False
    assert d.last_pc_reason.startswith("no-space")
    assert d.pressed == []
