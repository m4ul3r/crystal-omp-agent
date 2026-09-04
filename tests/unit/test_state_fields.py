"""Pure field decoders in state.py: DVs, shininess, status, badges."""
import pytest

from crystalagent.state import EGG, JOHTO_BADGES, _badges, _dvs, _shiny, _status

pytestmark = pytest.mark.unit


def test_dvs_nibble_order():
    # high nibble first within each byte: atk|def then spd|spc
    assert _dvs(bytes([0xAB, 0xCD])) == [0xA, 0xB, 0xC, 0xD]


def test_shiny_requires_all_four_conditions():
    assert _shiny([0b0010, 10, 10, 10])          # atk bit1 set, rest 10
    assert not _shiny([0b0000, 10, 10, 10])      # atk bit1 clear
    assert not _shiny([0b0010, 9, 10, 10])       # def off by one
    assert not _shiny([0b0010, 10, 11, 10])      # spd off
    assert not _shiny([0b0010, 10, 10, 15])      # spc off


def test_shiny_rejects_near_miss_high_values():
    # 11 != 10 even though it "looks close"; engine demands exactly 10
    assert not _shiny([0b1010, 10, 10, 11])


def test_status_single_bits():
    assert _status(0x08) == ["PSN"]
    assert _status(0x10) == ["BRN"]
    assert _status(0x20) == ["FRZ"]
    assert _status(0x40) == ["PAR"]


def test_status_combined_and_sleep_counter():
    got = _status(0x48)                          # PSN | PAR
    assert got == ["PSN", "PAR"]
    assert _status(0x07) == ["SLP:7"]
    assert _status(0x03) == ["SLP:3"]
    assert _status(0x00) == []


def test_badge_bit_order():
    # MINERAL is bit 4, STORM bit 5 per ram_constants -- do not reorder
    assert _badges(0b0000_0101, JOHTO_BADGES) == ["ZEPHYR", "PLAIN"]
    assert _badges(0b0011_0000, JOHTO_BADGES) == ["MINERAL", "STORM"]
    assert _badges(0, JOHTO_BADGES) == []
    assert _badges(0xFF, JOHTO_BADGES) == JOHTO_BADGES


def test_egg_sentinel():
    assert EGG == 0xFD


class _FakeSym(dict):
    def offset(self, left, right):
        return self[left][1] - self[right][1]


class _FakeCharmap:
    @staticmethod
    def decode(raw):
        return "".join(chr(byte) for byte in raw if byte)


class _StateNames:
    maps = {(1, 2): "TEST_MAP"}
    species = {175: "TOGEPI"}
    items = {4: "BERRY"}
    moves = {7: "FIRE PUNCH", 8: "ICE PUNCH"}


class _StateEmu:
    frame = 123

    def __init__(self):
        base, stride, nick = 0x100, 0x30, 0x400
        offsets = {
            "Species": 0, "Item": 1, "Moves": 2, "DVs": 6, "PP": 8,
            "Level": 12, "Status": 13, "HP": 14, "MaxHP": 16,
        }
        table = {
            "wPartyMon1": (1, base),
            "wPartyMon2": (1, base + stride),
            "wPartyMonNicknames": (1, nick),
        }
        table.update({
            "wPartyMon1" + field: (1, base + offset)
            for field, offset in offsets.items()
        })
        self.sym = _FakeSym(table)
        self.charmap = _FakeCharmap()
        self.mem = bytearray(0x500)
        self.mem[base + offsets["Species"]] = 175
        self.mem[base + offsets["Item"]] = 4
        self.mem[base + offsets["Moves"]:base + offsets["Moves"] + 4] = \
            bytes((7, 8, 0, 0))
        self.mem[base + offsets["DVs"]:base + offsets["DVs"] + 2] = \
            bytes((0x2A, 0xAA))
        self.mem[base + offsets["PP"]:base + offsets["PP"] + 4] = \
            bytes((0xC5, 9, 0, 0))
        self.mem[base + offsets["Level"]] = 12
        self.mem[base + offsets["Status"]] = 0x40
        self.mem[base + offsets["HP"]:base + offsets["HP"] + 2] = \
            (22).to_bytes(2, "big")
        self.mem[base + offsets["MaxHP"]:base + offsets["MaxHP"] + 2] = \
            (31).to_bytes(2, "big")
        self.mem[nick:nick + 11] = b"EGGY\0\0\0\0\0\0\0"
        self.reads = []

    def read_u8(self, name):
        return {
            "wGameTimeMinutes": 2, "wGameTimeSeconds": 3,
            "wMapGroup": 1, "wMapNumber": 2, "wXCoord": 4, "wYCoord": 5,
            "wJohtoBadges": 1, "wKantoBadges": 0, "wPartyCount": 1,
            "wBattleMode": 0,
        }[name]

    def read_be(self, name, count):
        return {"wGameTimeHours": 1, "wMoney": 1234}[name]

    def read_text(self, name, count):
        return {"wPlayerName": "PLAYER", "wRivalName": "RIVAL"}[name]

    def read(self, where, count=1):
        self.reads.append((where, count))
        if where == "wPartySpecies":
            return bytes((EGG,))
        _, address = where
        return bytes(self.mem[address:address + count])


def test_game_state_bulk_reads_party_and_preserves_output():
    from crystalagent.state import game_state

    emu = _StateEmu()
    state = game_state(emu, _StateNames())
    assert state["location"] == {
        "map_group": 1, "map_number": 2, "map": "TEST_MAP", "x": 4, "y": 5,
    }
    assert state["party"] == [{
        "species": 175,
        "name": "TOGEPI",
        "egg": True,
        "dvs": [2, 10, 10, 10],
        "shiny": True,
        "form": None,
        "nickname": "EGGY",
        "level": 12,
        "hp": 22,
        "max_hp": 31,
        "status": ["PAR"],
        "item": "BERRY",
        "moves": [
            {"name": "FIRE PUNCH", "pp": 0xC5},
            {"name": "ICE PUNCH", "pp": 9},
        ],
    }]
    assert emu.reads == [
        ("wPartySpecies", 1),
        ((1, 0x100), 0x30),
        ((1, 0x400), 11),
    ]
