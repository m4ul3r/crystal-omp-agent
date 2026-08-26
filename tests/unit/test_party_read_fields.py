"""read_party's moves/stats block -- the successor assessment's fuel.

Tactics.sacrifice_line can only say whether the replacement finishes the
chipped enemy if the frame's roster entries carry moves and the five battle
stats. The party struct keeps them at fixed offsets (pokecrystal.sym:
wPartyMon1Moves dce1 .. wPartyMon1SpclDef dd0d, stride $30); these tests pin
the decode against those addresses and the graceful degradation when an
emulator cannot serve them.
"""

import pytest

from crystalagent.decide import _party_entry, read_party

pytestmark = pytest.mark.unit

BANK, BASE = 1, 0xDCDF          # wPartyMon1
STRIDE = 0x30                   # wPartyMon2 - wPartyMon1
OFFSETS = {"Species": 0, "Moves": 0xDCE1 - BASE, "Level": 0xDCFE - BASE,
           "Status": 0xDCFF - BASE, "HP": 0xDD01 - BASE,
           "MaxHP": 0xDD03 - BASE, "Attack": 0xDD05 - BASE,
           "Defense": 0xDD07 - BASE, "Speed": 0xDD09 - BASE,
           "SpclAtk": 0xDD0B - BASE, "SpclDef": 0xDD0D - BASE}


class FakeSym(dict):
    def offset(self, a, b):
        return self[a][1] - self[b][1]


class FakeCharmap:
    @staticmethod
    def decode(raw):
        return "".join(chr(b - 0x80) for b in raw if b not in (0, 0x50))


class FakeEmu:
    """Byte-addressed memory standing in for bank-1 WRAM."""

    sym = None

    def __init__(self, mem, break_on_moves=False):
        self.mem = bytearray(mem)
        self.charmap = FakeCharmap()
        self.break_on_moves = break_on_moves

    def read_u8(self, name):
        _, addr = self.sym[name]
        return self.mem[addr]

    def read(self, where, n):
        if isinstance(where, str):
            _, addr = self.sym[where]
        else:
            addr = where[1]
        if self.break_on_moves and addr == BASE + OFFSETS["Moves"]:
            raise ConnectionError("no such bank")
        return bytes(self.mem[addr:addr + n])


def _sym():
    table = {"wPartyMon1": (BANK, BASE), "wPartyMon2": (BANK, BASE + STRIDE),
             "wPartyCount": (BANK, 0xD280),
             "wPartySpecies": (BANK, 0xD6A0),
             "wPartyMonNicknames": (BANK, 0xDDF1)}
    for f, off in OFFSETS.items():
        table["wPartyMon1" + f] = (BANK, BASE + off)
    return FakeSym(table)


def _nick(s):
    return bytes(ord(c) + 0x80 for c in s) + b"\x50"


def _emu(mon_moves=(8, 9, 0, 0), break_on_moves=False):
    mem = bytearray(0x10000)
    sym = _sym()
    for name, (_, addr) in sym.items():
        if name == "wPartyCount":
            mem[addr] = 1
        elif name == "wPartySpecies":
            mem[addr:addr + 2] = bytes([160, 95])       # FERALIGATR, ONIX
        elif name == "wPartyMonNicknames":
            mem[addr:addr + 11 * 2] = _nick("BROOK") + b"\0" * 5 \
                + _nick("SNAG") + b"\0" * 6
    base = BASE
    mem[base + OFFSETS["Species"]:base + OFFSETS["Species"] + 2] = \
        (160).to_bytes(2, "big")
    mem[base + OFFSETS["Moves"]:base + OFFSETS["Moves"] + 4] = bytes(mon_moves)
    mem[base + OFFSETS["Level"]] = 42
    mem[base + OFFSETS["Status"]] = 0
    mem[base + OFFSETS["HP"]:base + OFFSETS["HP"] + 2] = \
        (150).to_bytes(2, "big")
    mem[base + OFFSETS["MaxHP"]:base + OFFSETS["MaxHP"] + 2] = \
        (152).to_bytes(2, "big")
    for key, val in (("Attack", 110), ("Defense", 100), ("Speed", 98),
                     ("SpclAtk", 105), ("SpclDef", 100)):
        off = base + OFFSETS[key]
        mem[off:off + 2] = val.to_bytes(2, "big")
    emu = FakeEmu(mem, break_on_moves)
    emu.sym = sym
    return emu


class Names:
    species = {160: "FERALIGATR", 95: "ONIX"}


def test_read_party_decodes_moves_and_the_five_battle_stats():
    """The struct offsets are the ROM's own (pokecrystal.sym): moves as ids,
    stats big-endian two-byte words."""
    mon = read_party(_emu(), Names())[0]
    assert mon["moves"] == [8, 9]              # empty slots dropped
    assert mon["attack"] == 110 and mon["defense"] == 100
    assert mon["speed"] == 98 and mon["spatk"] == 105 and mon["spdef"] == 100
    assert mon["nickname"] == "BROOK" and mon["level"] == 42


def test_read_party_degrades_to_empty_when_the_reads_fail():
    """A kernel without the party-struct banks must still yield a roster --
    sacrifice_line then reports hits_to_ko None instead of raising."""
    mon = read_party(_emu(break_on_moves=True), Names())[0]
    assert mon["moves"] == []
    assert mon["attack"] == 0 and mon["spatk"] == 0


def test_caller_supplied_parties_carry_the_fields_through():
    """game_state-shaped dicts keep their stats through _party_entry."""
    entry = _party_entry({"name": "FERALIGATR", "species": 160,
                          "level": 42, "hp": 150, "max_hp": 152,
                          "moves": [8], "attack": 110}, 0)
    assert entry["moves"] == [8] and entry["attack"] == 110
    bare = _party_entry({"name": "ONIX"}, 1)
    assert bare["moves"] == [] and bare["speed"] == 0
