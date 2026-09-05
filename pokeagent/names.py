"""Names and data tables, read out of the ROM and the decomp.

Species, move, ability and type names live in the ROM as fixed-width
charmap-encoded arrays; item entries are a 44-byte struct carrying the name,
price and pocket. Base stats, move power/type/accuracy and the type chart are
ROM tables too. All of it is read through the symbol table -- nothing here is
transcribed, which is the same rule the Crystal harness held.

Map names come from ``data/maps/map_groups.json`` rather than the ROM,
because the (group, num) -> name mapping is a build-time artifact and the
JSON is the thing the build itself consumes.
"""

import json
import struct
from dataclasses import dataclass
from functools import cached_property

from . import paths

#: Fixed-width name table strides, from include/data2.h:42-45. These are the
#: array's declared element type, so they are safe to state.
SPECIES_NAME_LEN = 11
MOVE_NAME_LEN = 13
ABILITY_NAME_LEN = 13
TYPE_NAME_LEN = 7

# Struct STRIDES are deliberately NOT transcribed. `struct BattleMove` is nine
# u8 fields (include/pokemon.h:310-320) and reads as nine bytes, but the array
# stride is twelve because the compiler pads to 4-byte alignment. Transcribing
# "9" produced a table where TACKLE had 0 power and SURF had 19% accuracy --
# confidently wrong numbers, the failure mode the Crystal retrospective calls
# out as worse than a crash (RETROSPECTIVE.md 3.1). So every stride below is
# derived from the symbol's own size divided by the element count, and the
# division must come out whole or construction fails loudly.

#: Smallest possible `struct Item` (src/item.c:11-28); only seeds the stride
#: search below, which picks the real value out of the symbol's size.
ITEM_MIN_SIZE = 0x2C

TYPE_MUL_NORMAL = 10


@dataclass(frozen=True, slots=True)
class MoveData:
    id: int
    name: str
    effect: int
    power: int
    type: int
    accuracy: int
    pp: int
    secondary_chance: int
    target: int
    priority: int
    flags: int


@dataclass(frozen=True, slots=True)
class BaseStats:
    species: int
    hp: int
    attack: int
    defense: int
    speed: int
    sp_attack: int
    sp_defense: int
    type1: int
    type2: int
    catch_rate: int
    exp_yield: int
    gender_ratio: int
    growth_rate: int
    ability1: int
    ability2: int
    safari_flee_rate: int


@dataclass(frozen=True, slots=True)
class ItemData:
    id: int
    name: str
    price: int
    hold_effect: int
    pocket: int
    importance: int
    battle_usage: int


class Names:
    """Every ROM-resident lookup table the harness needs."""

    def __init__(self, emu, charmap, consts):
        self.emu = emu
        self.cm = charmap
        self.consts = consts

        # Element counts come from the name tables, whose element type is the
        # declared array width and therefore trustworthy. Every struct stride
        # is then derived from its symbol's size. See the note at the top of
        # this module for why nothing here is transcribed.
        self.species_count = self._count("gSpeciesNames", SPECIES_NAME_LEN)
        self.move_count = self._count("gMoveNames", MOVE_NAME_LEN)
        self.base_stats_stride = self._stride("gBaseStats", self.species_count)
        self.move_stride = self._stride("gBattleMoves", self.move_count)
        self.tmhm_stride = self._stride("gTMHMLearnsets", self.species_count)
        self.item_stride = self._stride("gItems", self._item_count())

    def _count(self, symbol, stride):
        size = self.emu.sym.size(symbol)
        if not size or size % stride:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, not a whole number of "
                f"{stride}-byte entries -- the symbol table and the ROM disagree"
            )
        return size // stride

    def _stride(self, symbol, count):
        size = self.emu.sym.size(symbol)
        if not size or size % count:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, which is not divisible by "
                f"{count} entries -- refusing to guess a stride"
            )
        return size // count

    def _item_count(self):
        # gItems has no companion name table; its stride is fixed by the
        # struct's alignment (4) and the last field's offset, so solve for the
        # count that makes the array come out whole at a 4-aligned stride.
        size = self.emu.sym.size("gItems")
        for stride in range(ITEM_MIN_SIZE, ITEM_MIN_SIZE + 16, 4):
            if size % stride == 0:
                return size // stride
        raise ValueError(f"cannot infer gItems stride from size {size:#x}")

    # ---- fixed-width name arrays ------------------------------------

    def _name_at(self, symbol, index, stride):
        return self.cm.decode(self.emu.read((symbol, index * stride), stride))

    def species(self, species_id) -> str:
        return self._name_at("gSpeciesNames", species_id, SPECIES_NAME_LEN)

    def move(self, move_id) -> str:
        return self._name_at("gMoveNames", move_id, MOVE_NAME_LEN)

    def ability(self, ability_id) -> str:
        return self._name_at("gAbilityNames", ability_id, ABILITY_NAME_LEN)

    def type(self, type_id) -> str:
        return self._name_at("gTypeNames", type_id, TYPE_NAME_LEN)

    def item(self, item_id) -> str:
        return self.item_data(item_id).name

    # ---- structured tables ------------------------------------------

    def item_data(self, item_id) -> ItemData:
        raw = self.emu.read(("gItems", item_id * self.item_stride), self.item_stride)
        return ItemData(
            id=struct.unpack_from("<H", raw, 0x0E)[0],
            name=self.cm.decode(raw[0:14]),
            price=struct.unpack_from("<H", raw, 0x10)[0],
            hold_effect=raw[0x12],
            pocket=raw[0x1A],
            importance=raw[0x18],
            battle_usage=raw[0x20],
        )

    def move_data(self, move_id) -> MoveData:
        raw = self.emu.read(("gBattleMoves", move_id * self.move_stride), self.move_stride)
        return MoveData(
            id=move_id,
            name=self.move(move_id),
            effect=raw[0],
            power=raw[1],
            type=raw[2],
            accuracy=raw[3],
            pp=raw[4],
            secondary_chance=raw[5],
            target=raw[6],
            priority=struct.unpack_from("<b", raw, 7)[0],
            flags=raw[8],
        )

    def base_stats(self, species_id) -> BaseStats:
        raw = self.emu.read(("gBaseStats", species_id * self.base_stats_stride), self.base_stats_stride)
        return BaseStats(
            species=species_id,
            hp=raw[0], attack=raw[1], defense=raw[2],
            speed=raw[3], sp_attack=raw[4], sp_defense=raw[5],
            type1=raw[6], type2=raw[7],
            catch_rate=raw[8], exp_yield=raw[9],
            gender_ratio=raw[0x10], growth_rate=raw[0x13],
            ability1=raw[0x16], ability2=raw[0x17],
            safari_flee_rate=raw[0x18],
        )

    #: 8 growth rates x 101 levels of u32, which is exactly the symbol's own
    #: size (0xca0 = 3232 = 8 * 101 * 4). Derived, not assumed: a build whose
    #: table grew would make this assertion fail instead of silently reading
    #: the wrong row.
    EXP_LEVELS = 101

    @cached_property
    def _exp_table(self) -> tuple[tuple[int, ...], ...]:
        base = self.emu.resolve("gExperienceTables")
        span = self.emu.sym.size("gExperienceTables")
        rates, rem = divmod(span, self.EXP_LEVELS * 4)
        if rem:
            raise ValueError(
                f"gExperienceTables spans {span} bytes, not a whole number of "
                f"{self.EXP_LEVELS}-level u32 rows"
            )
        blob = self.emu.read(base, span)
        return tuple(
            struct.unpack_from(f"<{self.EXP_LEVELS}I", blob,
                               r * self.EXP_LEVELS * 4)
            for r in range(rates)
        )

    def level_from_exp(self, species_id: int, exp: int) -> int:
        """The level a mon with this much EXP is at.

        The BOX format does not store a level -- only EXP -- so anything
        reasoning about a boxed mon (which is most of the dex's evolution
        work) has to derive it. This is the game's own loop, transcribed:
        ``GetLevelFromBoxMonExp`` walks the table while the next threshold is
        still <= exp (pret/src/pokemon_1.c:1834-1852). Doing it any other way
        -- interpolating, or reusing a party mon's plaintext level -- gets the
        boundary wrong by one on every growth rate.
        """
        table = self._exp_table[self.base_stats(species_id).growth_rate]
        level = 1
        while level <= 100 and table[level] <= exp:
            level += 1
        return level - 1

    @cached_property
    def type_chart(self) -> dict:
        """``(attacking, defending) -> multiplier``, only non-neutral pairs.

        The ROM table is 3 bytes per row (attacker, defender, multiplier x10)
        and is terminated by a 0xFE separator introducing the Foresight rows,
        then 0xFF. data/type_effectiveness.inc:1-6.
        """
        base = self.emu.resolve("gTypeEffectiveness")
        raw = self.emu.read(base, 3 * 400)
        chart = {}
        for i in range(0, len(raw), 3):
            atk, dfn, mul = raw[i], raw[i + 1], raw[i + 2]
            if atk == 0xFF:
                break
            if atk == 0xFE:  # rows past here only apply after Foresight
                continue
            chart[(atk, dfn)] = mul / TYPE_MUL_NORMAL
        return chart

    def effectiveness(self, move_type, def_type1, def_type2=None) -> float:
        mul = self.type_chart.get((move_type, def_type1), 1.0)
        if def_type2 is not None and def_type2 != def_type1:
            mul *= self.type_chart.get((move_type, def_type2), 1.0)
        return mul

    def level_up_moves(self, species_id) -> list[tuple[int, int]]:
        """``[(level, move_id), ...]`` from the species' learnset.

        ``gLevelUpLearnsets`` is a pointer table; each array is u16 entries
        of ``(level << 9) | move`` ending at 0xFFFF
        (src/data/pokemon/level_up_learnsets.h:9-10).
        """
        ptr = self.emu.u32(("gLevelUpLearnsets", species_id * 4))
        out = []
        for i in range(64):
            entry = self.emu.u16((ptr, i * 2))
            if entry == 0xFFFF:
                break
            out.append((entry >> 9, entry & 0x1FF))
        return out

    def tmhm_learnset(self, species_id) -> int:
        """64-bit mask; bit n means the species learns ``ITEM_TM01 + n``
        (src/data/pokemon/tmhm_learnsets.h:5)."""
        lo, hi = struct.unpack("<II", self.emu.read(("gTMHMLearnsets", species_id * self.tmhm_stride), 8))
        return lo | (hi << 32)

    def learns_tm(self, species_id, item_id) -> bool:
        first = self.consts.items["ITEM_TM01_FOCUS_PUNCH"]
        bit = item_id - first
        if not 0 <= bit < 64:
            return False
        return bool(self.tmhm_learnset(species_id) >> bit & 1)

    # ---- maps --------------------------------------------------------

    @cached_property
    def _map_groups(self):
        return json.loads((paths.MAPS / "map_groups.json").read_text())

    @cached_property
    def map_table(self) -> dict[tuple[int, int], str]:
        """``(group, num) -> "LittlerootTown"``."""
        table = {}
        for gi, group in enumerate(self._map_groups["group_order"]):
            for ni, name in enumerate(self._map_groups[group]):
                table[(gi, ni)] = name
        return table

    @cached_property
    def map_ids(self) -> dict[str, tuple[int, int]]:
        return {v: k for k, v in self.map_table.items()}

    def map_name(self, group, num) -> str:
        return self.map_table.get((group, num), f"MAP_{group}_{num}")
