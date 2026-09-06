"""Pokedex completion as a drivable objective.

Once the Elite Four is done the run has no story left to follow, so the
objective becomes "fill the regional dex as far as one cartridge and one
player can". That is a *planning* problem, not a scripting problem, and it
needs three things the rest of the harness did not have:

1. **Which species are even reachable.** Seven Hoenn entries are Ruby
   exclusives, seven more only evolve on a link trade, and two (Jirachi,
   Deoxys) never existed outside an external distribution. The engine agrees
   about the last pair -- ``src/birch_pc.c:94-102`` deliberately discounts
   them when it decides whether Birch says the dex is complete -- so a
   planner that offers them as targets is lying to its caller.
2. **Where the rest live.** The vendored regional-dex-buddy dataset
   (``data/dex/<game>.json``) knows every gift, in-game trade, fossil and
   fixed encounter, which no ROM table records in one place. But its area
   labels are Bulbapedia prose ("Mount Pyre (outside)"), not the map names
   this harness navigates by, so they get mapped -- and where a label cannot
   be pinned to exactly one map, that is *reported*, not guessed.
3. **How the chains work.** 60 of the 188 reachable entries have no catch
   location at all, because they are evolutions. So the plan resolves them
   through the ROM's own ``gEvolutionTable`` down to something you can
   actually walk up to and throw a ball at.

Two sources of truth, used for what each is actually good for:

* **Wild encounters come from the ROM.** ``gWildMonHeaders`` is keyed by
  ``(mapGroup, mapNum)``, so it yields the harness's own map names, the real
  level range and the real slot -- version-correct by construction, with no
  ``#ifdef SAPPHIRE`` to get wrong. Slot probabilities come from the
  cumulative ``ENCOUNTER_CHANCE_*`` macros the engine itself branches on
  (``src/wild_encounter.c:144-233``).
* **Everything a wild table cannot express comes from the dataset**: gifts,
  the three in-game NPC trades, the two fossil revivals, the roamer, the
  static legendaries.

Nothing about species, levels, evolution methods or encounter rates is
transcribed here. Struct strides are derived from symbol sizes and refuse a
non-integral division (AGENTS.md gotcha 12), and every map name a mapping
produces is checked against ``nav.MapData.index`` before it is handed out.
"""

import functools
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import cconst, cstruct, paths, pokemon

#: The vendored regional-dex-buddy export. See data/dex/SOURCE.txt.
DEX_DATA_DIR = paths.TOOL_DIR / "data" / "dex"

#: src/wild_encounter.c:262 -- the header table ends at mapGroup 0xFF.
WILD_HEADER_TERMINATOR = 0xFF

#: ARM32 pointer width. Needed because `struct WildPokemonHeader`
#: (include/wild_encounter.h:16-24) is four pointers after two u8s and
#: carries no /*0x..*/ offset comments for :mod:`pokeagent.cstruct` to read.
POINTER_SIZE = 4

#: src/pokedex.c:3986-3993 -- the dex bitfields are indexed by
#: `nationalDexNo - 1`, not by species and not by Hoenn number.
DEX_FLAG_BIAS = 1

# ---- why a species is out of reach -------------------------------------------

OUT_OF_REACH_VERSION = "version-exclusive"
OUT_OF_REACH_TRADE_EVOLUTION = "trade-evolution"
OUT_OF_REACH_EVENT = "event-only"
OUT_OF_REACH_TRADE_PARTNER = "needs-trade-partner"
OUT_OF_REACH_UNOBTAINABLE = "unobtainable"

#: Dataset `method` values that no solo player can reach: external
#: distributions, not game content. Corroborated by src/birch_pc.c:94-102,
#: which excludes exactly the two species these produce.
EVENT_METHODS = frozenset({
    "colosseum-bonus-disc-us",
    "colosseum-bonus-disc-jpn",
    "pokemon-channel-pal",
})

# ---- route kinds, cheapest first ---------------------------------------------

ROUTE_EVOLVE = "evolve"
ROUTE_GIFT = "gift"
ROUTE_WILD = "wild"
ROUTE_STATIC = "static"
ROUTE_NPC_TRADE = "npc_trade"
ROUTE_FOSSIL = "fossil"
ROUTE_BREED = "breed"
ROUTE_ROAM = "roam"
ROUTE_EVENT = "event"

#: Base cost per route kind. Not a simulation of effort -- a stated
#: preference order, exposed on every Step so a caller can audit the choice
#: instead of wondering why the plan said what it said.
ROUTE_BASE_COST = {
    ROUTE_EVOLVE: 0.0,
    ROUTE_GIFT: 1.0,
    ROUTE_WILD: 2.0,
    ROUTE_STATIC: 3.0,
    ROUTE_NPC_TRADE: 4.0,
    ROUTE_FOSSIL: 5.0,
    ROUTE_BREED: 6.0,
    ROUTE_ROAM: 9.0,
    ROUTE_EVENT: 99.0,
}

#: What each encounter method costs beyond showing up: Surf, a rod, Dive.
METHOD_SURCHARGE = {
    "land": 0.0,
    "water": 0.5,
    "dive": 1.5,
    "rock_smash": 1.0,
    "old_rod": 0.5,
    "good_rod": 0.75,
    "super_rod": 1.0,
}

#: Dataset methods -> route kind. Anything absent is reported, not guessed.
DATASET_ROUTE = {
    "gift": ROUTE_GIFT,
    "gift-egg": ROUTE_GIFT,
    "static": ROUTE_STATIC,
    "npc-trade": ROUTE_NPC_TRADE,
    "devon-scope": ROUTE_STATIC,
    "roaming-grass": ROUTE_ROAM,
    "roaming-water": ROUTE_ROAM,
    "walk": ROUTE_WILD,
    "surf": ROUTE_WILD,
    "old-rod": ROUTE_WILD,
    "good-rod": ROUTE_WILD,
    "super-rod": ROUTE_WILD,
    "rock-smash": ROUTE_WILD,
    "seaweed": ROUTE_WILD,
    "feebas-tile-fishing": ROUTE_WILD,
}

#: Steps whose route is one of these are listed but cannot be executed yet.
PARTY_GROUP = "PARTY"


# ---- the vendored dataset ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Encounter:
    """One way the dataset says a species shows up somewhere."""

    area: str
    area_slug: str
    method: str
    method_label: str
    min_level: int
    max_level: int
    chance: int
    conditions: tuple[str, ...] = ()

    @property
    def is_event(self) -> bool:
        return self.method in EVENT_METHODS

    @property
    def is_fossil(self) -> bool:
        return any("fossil" in c.lower() for c in self.conditions)


@dataclass(frozen=True, slots=True)
class DexEntry:
    """One regional-dex slot, with the species id it resolves to in the ROM."""

    dex: int
    natdex: int
    species: int
    slug: str
    name: str
    rom_name: str
    types: tuple[str, ...]
    obtainable: bool
    unobtainable: bool
    exclusive_to: str | None
    needs_trade_partner: bool
    trade_evolution: tuple[str, str | None] | None
    encounters: tuple[Encounter, ...]
    notes: tuple[str, ...] = ()

    @property
    def event_only(self) -> bool:
        """Reachable in the dataset, but only through an external
        distribution -- so not reachable here at all."""
        return bool(self.encounters) and all(e.is_event for e in self.encounters)


@dataclass(frozen=True, slots=True)
class OutOfReach:
    """A dex slot the objective deliberately drops, and why."""

    dex: int
    natdex: int
    name: str
    reason: str
    detail: str


@functools.lru_cache(maxsize=None)
def load_dataset(dex_id: str) -> dict:
    """Raw ``data/dex/<dex_id>.json``. Vendored; never fetched."""
    path = DEX_DATA_DIR / f"{dex_id}.json"
    paths.require(
        path,
        f"dex dataset for {dex_id!r}",
        f"expected the regional-dex-buddy export at {DEX_DATA_DIR}",
    )
    return json.loads(path.read_text(encoding="utf-8"))


def dataset_games() -> list[str]:
    return sorted(p.stem for p in DEX_DATA_DIR.glob("*.json") if p.stem != "games")


# ---- the ROM's evolution table -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Evolution:
    """One ``struct Evolution`` row (include/pokemon.h:380-385)."""

    from_species: int
    to_species: int
    method: int
    method_name: str
    param: int

    @property
    def by_level(self) -> bool:
        return self.method_name.startswith("EVO_LEVEL")

    @property
    def level(self) -> int | None:
        """The level threshold, when the method has one.

        ``EVO_LEVEL_NINJASK`` and ``EVO_LEVEL_SHEDINJA`` carry a level too --
        Nincada at 20 produces both -- so they are included.
        """
        return self.param if self.by_level else None

    @property
    def item(self) -> int | None:
        return self.param if self.method_name in ("EVO_ITEM", "EVO_TRADE_ITEM") else None

    @property
    def needs_trade(self) -> bool:
        return self.method_name in ("EVO_TRADE", "EVO_TRADE_ITEM")

    @property
    def by_friendship(self) -> bool:
        return self.method_name.startswith("EVO_FRIENDSHIP")


class EvolutionTable:
    """``gEvolutionTable`` plus the two species/dex-number translations.

    ``gEvolutionTable`` is declared ``[NUM_SPECIES][5]`` of a three-u16
    struct, which reads as six bytes -- but the linked array is 40 bytes per
    species, because agbcc pads the row. Transcribing "6" would have shifted
    every entry past the first, so the stride is derived from the symbol's own
    size and a non-integral division is refused (AGENTS.md gotcha 12).
    """

    #: include/pokemon.h:387-390 -- struct EvolutionData holds five rows.
    EVOS_PER_SPECIES = 5

    def __init__(self, emu, names, consts):
        self.emu = emu
        self.names = names
        self.consts = consts
        self.species_count = names.species_count

        size = emu.sym.size("gEvolutionTable")
        if not size or size % self.species_count:
            raise ValueError(
                f"gEvolutionTable is {size:#x} bytes, not divisible by "
                f"{self.species_count} species -- refusing to guess a stride"
            )
        self.row_stride = size // self.species_count
        if self.row_stride % self.EVOS_PER_SPECIES:
            raise ValueError(
                f"gEvolutionTable row is {self.row_stride} bytes, not divisible "
                f"by {self.EVOS_PER_SPECIES} entries"
            )
        self.entry_stride = self.row_stride // self.EVOS_PER_SPECIES

        self.methods = {
            k: v
            for k, v in cconst.parse_defines(str(paths.INCLUDE / "pokemon.h")).items()
            if k.startswith("EVO_")
        }
        if not self.methods:
            raise ValueError("no EVO_* constants in include/pokemon.h")
        self.method_names = {v: k for k, v in self.methods.items()}

        self._forward: dict[int, tuple[Evolution, ...]] = {}
        self._backward: dict[int, list[Evolution]] = {}
        self._load()

    def _load(self):
        base = self.emu.resolve("gEvolutionTable")
        raw = self.emu.read(base, self.row_stride * self.species_count)
        for species in range(1, self.species_count):
            rows = []
            off = species * self.row_stride
            for i in range(self.EVOS_PER_SPECIES):
                method, param, target = struct.unpack_from(
                    "<HHH", raw, off + i * self.entry_stride
                )
                if method == 0 or target == 0:
                    continue
                evo = Evolution(
                    from_species=species,
                    to_species=target,
                    method=method,
                    method_name=self.method_names.get(method, f"EVO_{method:#x}"),
                    param=param,
                )
                rows.append(evo)
                self._backward.setdefault(target, []).append(evo)
            if rows:
                self._forward[species] = tuple(rows)

    # ---- species / dex number translation ----------------------------

    @functools.cached_property
    def _species_to_natdex(self) -> tuple[int, ...]:
        """``gSpeciesToNationalPokedexNum``, indexed by ``species - 1``
        (src/pokemon_3.c:444)."""
        size = self.emu.sym.size("gSpeciesToNationalPokedexNum")
        raw = self.emu.read("gSpeciesToNationalPokedexNum", size)
        return struct.unpack(f"<{size // 2}H", raw)

    @functools.cached_property
    def _species_to_hoenn(self) -> tuple[int, ...]:
        size = self.emu.sym.size("gSpeciesToHoennPokedexNum")
        raw = self.emu.read("gSpeciesToHoennPokedexNum", size)
        return struct.unpack(f"<{size // 2}H", raw)

    @functools.cached_property
    def _natdex_to_species(self) -> dict[int, int]:
        """Inverse of the above, matching ``NationalPokedexNumToSpecies``
        (src/pokemon_3.c:405-415): the lowest species id wins."""
        out: dict[int, int] = {}
        for i, natdex in enumerate(self._species_to_natdex):
            out.setdefault(natdex, i + DEX_FLAG_BIAS)
        return out

    def natdex(self, species: int) -> int:
        if not 1 <= species <= len(self._species_to_natdex):
            raise ValueError(f"species {species} outside gSpeciesToNationalPokedexNum")
        return self._species_to_natdex[species - 1]

    def hoenn_dex(self, species: int) -> int:
        if not 1 <= species <= len(self._species_to_hoenn):
            raise ValueError(f"species {species} outside gSpeciesToHoennPokedexNum")
        return self._species_to_hoenn[species - 1]

    def species_of_natdex(self, natdex: int) -> int | None:
        return self._natdex_to_species.get(natdex)

    # ---- the graph ----------------------------------------------------

    def evolutions(self, species: int) -> tuple[Evolution, ...]:
        """What ``species`` turns into."""
        return self._forward.get(species, ())

    def pre_evolutions(self, species: int) -> tuple[Evolution, ...]:
        """What turns into ``species``. Empty for a base form."""
        return tuple(self._backward.get(species, ()))

    def chain(self, species: int) -> tuple[int, ...]:
        """``species`` and everything downstream of it, breadth first."""
        out, queue, seen = [], [species], {species}
        while queue:
            cur = queue.pop(0)
            out.append(cur)
            for evo in self.evolutions(cur):
                if evo.to_species not in seen:
                    seen.add(evo.to_species)
                    queue.append(evo.to_species)
        return tuple(out)

    def roots(self, species: int) -> tuple[int, ...]:
        """The base forms ``species`` descends from (itself, if it is one)."""
        pres = self.pre_evolutions(species)
        if not pres:
            return (species,)
        out: list[int] = []
        for evo in pres:
            for root in self.roots(evo.from_species):
                if root not in out:
                    out.append(root)
        return tuple(out)

    def describe(self, evo: Evolution) -> str:
        """"raise TORCHIC to level 16" / "use a MOON STONE on SKITTY"."""
        source = self.names.species(evo.from_species)
        if evo.by_level:
            return f"raise {source} to level {evo.param}"
        if evo.item is not None:
            return f"use {self.names.item(evo.item)} on {source}"
        if evo.by_friendship:
            return f"raise {source}'s friendship, then level it up"
        if evo.method_name == "EVO_BEAUTY":
            return f"raise {source} to beauty {evo.param}, then level it up"
        if evo.needs_trade:
            return f"trade {source} (needs a second player)"
        return f"evolve {source} ({evo.method_name})"


# ---- the ROM's wild encounter table ------------------------------------------


@dataclass(frozen=True, slots=True)
class WildSlot:
    """One slot of one map's encounter table."""

    map_name: str
    map_group: int
    map_num: int
    kind: str
    slot: int
    species: int
    min_level: int
    max_level: int
    #: Probability this slot is the one picked, in percent.
    slot_chance: float
    #: The table's own ``encounterRate``: how often a step rolls at all.
    encounter_rate: int


def _sequential_offsets(source: Path, struct_name: str) -> tuple[dict[str, int], int]:
    """Field offsets and total size for a struct of scalars and pointers.

    :func:`pokeagent.cstruct.layout` reads real ``/*0x..*/`` annotations and
    :func:`layout_sequential` walks scalars, but neither handles a pointer
    field -- and ``struct WildPokemonHeader`` is four of them. So this walks
    the declaration with natural alignment, treating any ``*`` field as
    :data:`POINTER_SIZE`. The result is checked against the symbol's size by
    the caller, which is what makes the assumption safe to make.
    """
    text = paths.require(
        source, source.name, "is the pret/ submodule checked out?"
    ).read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"struct\s+{struct_name}\s*\{{(.*?)\n\}}", text, re.S)
    if not match:
        raise KeyError(f"struct {struct_name} not found in {source}")

    fields: dict[str, int] = {}
    offset, align = 0, 1
    for line in match.group(1).splitlines():
        line = line.split("//")[0].strip()
        if not line.endswith(";"):
            continue
        decl = line[:-1]
        ptr = "*" in decl
        name = re.split(r"[\s*]+", decl)[-1]
        if ptr:
            width = POINTER_SIZE
        else:
            base = re.split(r"[\s*]+", decl)[-2] if " " in decl else decl
            width = cstruct._SCALAR.get(base, 0)
        if not width:
            raise ValueError(f"cannot size field {decl!r} of struct {struct_name}")
        if offset % width:
            offset += width - (offset % width)
        fields[name] = offset
        offset += width
        align = max(align, width)
    if offset % align:
        offset += align - (offset % align)
    return fields, offset


class WildTable:
    """``gWildMonHeaders`` -- every wild encounter, by real map name.

    Read from the live ROM rather than ``src/data/wild_encounters.json``,
    because that file carries both versions' tables behind ``#ifdef
    SAPPHIRE`` and choosing the wrong branch would silently hand back Ruby's
    Seedot where Sapphire has Lotad. The ROM cannot be wrong about which
    game it is.
    """

    KINDS = ("land", "water", "rock_smash", "fishing")

    def __init__(self, emu, names, mapdata):
        self.emu = emu
        self.names = names
        self.mapdata = mapdata

        header_fields, header_size = _sequential_offsets(
            paths.INCLUDE / "wild_encounter.h", "WildPokemonHeader"
        )
        info_fields, _ = _sequential_offsets(
            paths.INCLUDE / "wild_encounter.h", "WildPokemonInfo"
        )
        mon_fields, mon_size = _sequential_offsets(
            paths.INCLUDE / "wild_encounter.h", "WildPokemon"
        )
        table_size = emu.sym.size("gWildMonHeaders")
        if not table_size or table_size % header_size:
            raise ValueError(
                f"gWildMonHeaders is {table_size:#x} bytes, not a whole number "
                f"of {header_size}-byte headers"
            )
        self.header_fields = header_fields
        self.header_size = header_size
        self.info_fields = info_fields
        self.mon_fields = mon_fields
        self.mon_size = mon_size
        self.header_capacity = table_size // header_size

        self.slot_chance, self.rod_slots = _encounter_chances()
        self.unnamed_maps: list[tuple[int, int]] = []
        self.slots: tuple[WildSlot, ...] = ()
        self._by_species: dict[int, list[WildSlot]] = {}
        self._load()

    def _load(self):
        base = self.emu.resolve("gWildMonHeaders")
        raw = self.emu.read(base, self.header_size * self.header_capacity)
        h = self.header_fields
        slots: list[WildSlot] = []
        for i in range(self.header_capacity):
            off = i * self.header_size
            group = raw[off + h["mapGroup"]]
            if group == WILD_HEADER_TERMINATOR:
                break
            num = raw[off + h["mapNum"]]
            map_name = self.mapdata.by_number.get((group, num))
            if map_name is None:
                self.unnamed_maps.append((group, num))
                map_name = f"MAP_{group}_{num}"
            for kind in self.KINDS:
                ptr = int.from_bytes(
                    raw[
                        off + h[f"{kind_field(kind)}"] : off
                        + h[f"{kind_field(kind)}"]
                        + POINTER_SIZE
                    ],
                    "little",
                )
                if not ptr:
                    continue
                slots.extend(self._read_info(ptr, kind, map_name, group, num))
        self.slots = tuple(slots)
        for s in self.slots:
            self._by_species.setdefault(s.species, []).append(s)

    def _read_info(self, ptr, kind, map_name, group, num):
        rate = self.emu.u8(ptr + self.info_fields["encounterRate"])
        array = self.emu.u32(ptr + self.info_fields["wildPokemon"])
        chances = self.slot_chance[kind]
        blob = self.emu.read(array, self.mon_size * len(chances))
        m = self.mon_fields
        out = []
        for slot in range(len(chances)):
            base = slot * self.mon_size
            species = int.from_bytes(
                blob[base + m["species"] : base + m["species"] + 2], "little"
            )
            if not species:
                continue
            out.append(
                WildSlot(
                    map_name=map_name,
                    map_group=group,
                    map_num=num,
                    kind=self._slot_kind(kind, slot, map_name),
                    slot=slot,
                    species=species,
                    min_level=blob[base + m["minLevel"]],
                    max_level=blob[base + m["maxLevel"]],
                    slot_chance=chances[slot],
                    encounter_rate=rate,
                )
            )
        return out

    def _slot_kind(self, kind, slot, map_name):
        """Turn the table's four physical kinds into what the player does.

        Fishing splits by rod, because which rod you hold decides which
        slots are even reachable (src/wild_encounter.c:197-233). Water on an
        ``Underwater*`` map is Dive, not Surf -- those are the seaweed
        patches that hold Clamperl and Relicanth.
        """
        if kind == "fishing":
            for rod, indices in self.rod_slots.items():
                if slot in indices:
                    return rod
            return "fishing"
        if kind == "water" and map_name.startswith("Underwater"):
            return "dive"
        return kind

    def for_species(self, species: int) -> tuple[WildSlot, ...]:
        return tuple(self._by_species.get(species, ()))

    def for_map(self, map_name: str) -> tuple[WildSlot, ...]:
        return tuple(s for s in self.slots if s.map_name == map_name)

    @property
    def species(self) -> frozenset[int]:
        return frozenset(self._by_species)


def kind_field(kind: str) -> str:
    """``"rock_smash"`` -> ``"rockSmashMonsInfo"``, the header's field name."""
    head, *rest = kind.split("_")
    return head + "".join(p.title() for p in rest) + "MonsInfo"


@functools.lru_cache(maxsize=1)
def _encounter_chances() -> tuple[dict[str, tuple[float, ...]], dict[str, tuple[int, ...]]]:
    """Per-slot probabilities, from the macros the engine branches on.

    ``src/data/wild_encounters.h`` defines ``ENCOUNTER_CHANCE_<FAMILY>_SLOT_n``
    cumulatively and ``..._TOTAL`` as the last one, which is exactly how
    ``ChooseWildMonIndex_*`` compares them (src/wild_encounter.c:144-233). The
    slot *indices* live in the macro names, so the old/good/super rod split
    is read out of the header too rather than retyped.
    """
    env = cconst.parse_defines(str(paths.PRET / "src" / "data" / "wild_encounters.h"))
    pattern = re.compile(r"^ENCOUNTER_CHANCE_(.+?)_SLOT_(\d+)$")
    families: dict[str, dict[int, int]] = {}
    for name, value in env.items():
        m = pattern.match(name)
        if m:
            families.setdefault(m.group(1), {})[int(m.group(2))] = value
    if not families:
        raise ValueError("no ENCOUNTER_CHANCE_* macros in src/data/wild_encounters.h")

    rod_slots: dict[str, tuple[int, ...]] = {}
    chances: dict[str, tuple[float, ...]] = {}
    fishing: dict[int, float] = {}
    for family, slots in families.items():
        total = env.get(f"ENCOUNTER_CHANCE_{family}_TOTAL")
        if not total:
            raise ValueError(f"ENCOUNTER_CHANCE_{family}_TOTAL is missing or zero")
        ordered = sorted(slots)
        per: dict[int, float] = {}
        previous = 0
        for index in ordered:
            per[index] = 100.0 * (slots[index] - previous) / total
            previous = slots[index]
        if family.startswith("FISHING_MONS_"):
            rod = family[len("FISHING_MONS_"):].lower()
            rod_slots[rod] = tuple(ordered)
            fishing.update(per)
        else:
            key = family.removesuffix("_MONS").lower()
            chances[key] = tuple(per[i] for i in ordered)
    if fishing:
        chances["fishing"] = tuple(fishing[i] for i in sorted(fishing))
    return chances, rod_slots


# ---- dataset areas -> this harness's map names --------------------------------


@dataclass(frozen=True, slots=True)
class AreaMap:
    """What a dataset area label resolves to, and how confidently."""

    label: str
    slug: str
    maps: tuple[str, ...]
    #: True when the label names exactly one decomp map.
    exact: bool
    reason: str = ""

    def __bool__(self):
        return bool(self.maps)

    @property
    def primary(self) -> str:
        return self.maps[0] if self.maps else ""


#: Dataset area slugs the algorithmic rule below cannot resolve, or resolves
#: wrongly. Each value is (maps, reason); a one-map tuple with an empty
#: reason is an exact match, anything else says why it is not.
#:
#: Where a label is a Bulbapedia floor that the decomp does not split the
#: same way, the entry names the whole group rather than picking one -- a
#: sweepable set is honest, a coin-flip is not. Several were pinned by
#: cross-checking the dataset's species list against the map's real
#: ``gWildMonHeaders`` table (Snorunt is only in the ice room, so the
#: dataset's "B3F" is ``ShoalCave_LowTideIceRoom``).
AREA_OVERRIDES: dict[str, tuple[tuple[str, ...], str]] = {
    # Bulbapedia writes "Mount Pyre"; the decomp writes "MtPyre".
    "mt-pyre-1f": (("MtPyre_1F",), ""),
    "mt-pyre-2f": (("MtPyre_2F",), ""),
    "mt-pyre-3f": (("MtPyre_3F",), ""),
    "mt-pyre-4f": (("MtPyre_4F",), ""),
    "mt-pyre-5f": (("MtPyre_5F",), ""),
    "mt-pyre-6f": (("MtPyre_6F",), ""),
    "mt-pyre-outside": (("MtPyre_Exterior",), ""),
    "mt-pyre-summit": (("MtPyre_Summit",), ""),
    "sky-pillar-apex": (("SkyPillar_Top",), ""),
    "new-mauville-area": (("NewMauville_Inside",), ""),
    "new-mauville-entrance": (("NewMauville_Entrance",), ""),
    "mossdeep-city-stevens-house": (("MossdeepCity_StevensHouse",), ""),
    "southern-island-area": (("SouthernIsland_Interior",), ""),
    # data/maps/SouthernIsland_Interior/scripts.inc:64 puts the encounter here.
    "hoenn-route-119-weather-institute": (
        ("Route119_WeatherInstitute_2F",),
        "",
    ),
    # The decomp keeps two rooms per Meteor Falls floor; the dataset's
    # "back" is the second room, confirmed by Bagon appearing only in
    # MeteorFalls_B1F_2R's land table.
    "meteor-falls-area": (("MeteorFalls_1F_1R",), ""),
    "meteor-falls-back": (("MeteorFalls_1F_2R",), ""),
    "meteor-falls-b1f": (("MeteorFalls_B1F_1R",), ""),
    "meteor-falls-backsmall-room": (("MeteorFalls_B1F_2R",), ""),
    "granite-cave-1fsmall-room": (
        ("GraniteCave_1F",),
        "the dataset splits Granite Cave 1F into a hall and a side room; "
        "the decomp has one map",
    ),
    "hoenn-safari-zone-nwmach-bike-area": (("SafariZone_Northwest",), ""),
    "hoenn-safari-zone-neacro-bike-area": (("SafariZone_Northeast",), ""),
    "hoenn-safari-zone-sw": (("SafariZone_Southwest",), ""),
    "hoenn-safari-zone-se": (("SafariZone_Southeast",), ""),
    "shoal-cave-low-tide": (("ShoalCave_LowTideEntranceRoom",), ""),
    "shoal-cave-b3f": (("ShoalCave_LowTideIceRoom",), ""),
    "shoal-cave-b1f": (
        ("ShoalCave_LowTideInnerRoom", "ShoalCave_LowTideLowerRoom",
         "ShoalCave_LowTideStairsRoom"),
        "the inner, lower and stairs rooms carry identical encounter tables, "
        "so the dataset's floor label cannot pick one",
    ),
    "shoal-cave-b2f": (
        ("ShoalCave_LowTideInnerRoom", "ShoalCave_LowTideLowerRoom",
         "ShoalCave_LowTideStairsRoom"),
        "same three rooms as the dataset's B1F; the floor labels do not "
        "distinguish them",
    ),
    "shoal-cave-high-tide": (
        ("ShoalCave_HighTideEntranceRoom", "ShoalCave_HighTideInnerRoom"),
        "gWildMonHeaders has no entry for either high-tide map -- the same "
        "species are caught in the low-tide rooms",
    ),
    "abandoned-ship-area": (
        ("AbandonedShip_Rooms_B1F", "AbandonedShip_HiddenFloorCorridors"),
        "the ship is eleven maps; only these two carry encounter tables",
    ),
    "seafloor-cavern-area": (
        ("SeafloorCavern_Entrance", "SeafloorCavern_Room1",
         "SeafloorCavern_Room2", "SeafloorCavern_Room3",
         "SeafloorCavern_Room4", "SeafloorCavern_Room5",
         "SeafloorCavern_Room6", "SeafloorCavern_Room7",
         "SeafloorCavern_Room8"),
        "one dataset label for nine decomp rooms",
    ),
    "team-aqua-hideout-area": (
        ("AquaHideout_1F", "AquaHideout_B1F", "AquaHideout_B2F"),
        "one dataset label for three decomp floors",
    ),
    "team-magma-hideout-area": (
        ("MagmaHideout_1F", "MagmaHideout_B1F", "MagmaHideout_B2F"),
        "one dataset label for three decomp floors",
    ),
    "mirage-island-area": (
        ("Route130",),
        "Mirage Island is Route 130 under a swapped layout, gated on "
        "IsMirageIslandPresent (src/overworld.c:1041-1043, "
        "src/time_events.c:42)",
    ),
    "roaming-hoenn-area": (
        (),
        "a roaming encounter has no map: gSaveBlock1.roamer is relocated "
        "between routes (src/wild_encounter.c:456-463)",
    ),
    "hoenn-pokecenter-area": (
        (),
        "external distribution (Colosseum Bonus Disc / Pokemon Channel), "
        "not an in-game map",
    ),
}

#: Areas whose label is a bare route or settlement resolve by spelling.
_ROUTE = re.compile(r"^hoenn-route-(\d+)-area$")
_UNDERWATER = re.compile(r"^hoenn-route-(\d+)-underwater$")
_FLOOR = re.compile(r"^(B?\d+F|Entrance|Summit|Exterior)$", re.I)


class AreaAtlas:
    """Maps the dataset's prose area labels onto real decomp map names.

    Every result is validated against ``nav.MapData.index`` -- the same 394
    names ``travel()`` accepts -- so a stale override fails loudly here
    instead of producing a plan step that names a map the navigator has
    never heard of.
    """

    def __init__(self, mapdata):
        self.mapdata = mapdata
        self.index = mapdata.index
        self._cache: dict[str, AreaMap] = {}
        self._by_label: dict[str, set[str]] = {}
        self._label_of: dict[str, str] = {}

    @functools.cached_property
    def underwater_by_route(self) -> dict[str, str]:
        """``"Route124" -> "Underwater1"``, from the decomp's own map JSON.

        Every ``Underwater*`` map declares an ``emerge`` connection naming
        the surface route it sits under, so this needs no guessing.
        """
        out = {}
        for name in self.index:
            if not name.startswith("Underwater"):
                continue
            for conn in self.mapdata.info(name).connections:
                if conn.get("direction") == "emerge":
                    surface = conn["map"].removeprefix("MAP_")
                    surface = "".join(p.title() for p in surface.split("_"))
                    out[surface] = name
        return out

    def register(self, label: str, slug: str):
        """Remember the label/slug pairing the dataset used.

        Two directions, both needed: a label shared by several slugs must
        say which ones it collides with, and a slug needs its label back
        because only the label carries the floor parenthesis that spells a
        decomp map name.
        """
        self._by_label.setdefault(label, set()).add(slug)
        self._label_of[slug] = label

    def area_to_map(self, area_slug_or_name: str) -> AreaMap:
        """Resolve a dataset area slug or display label to map names.

        Returns an :class:`AreaMap`; a falsy one (no maps) carries the reason
        in ``.reason`` rather than raising, because "this dataset label has
        no map" is information the objective wants to surface, not an error.
        """
        key = str(area_slug_or_name)
        if key in self._cache:
            return self._cache[key]
        result = self._resolve(key)
        for name in result.maps:
            if name not in self.index:
                raise KeyError(
                    f"area {key!r} maps to {name!r}, which is not one of the "
                    f"{len(self.index)} maps in data/maps/map_groups.json"
                )
        self._cache[key] = result
        return result

    def _resolve(self, key: str) -> AreaMap:
        if key in AREA_OVERRIDES:
            maps, reason = AREA_OVERRIDES[key]
            return AreaMap(key, key, maps, len(maps) == 1 and not reason, reason)

        m = _UNDERWATER.match(key)
        if m:
            route = f"Route{m.group(1)}"
            under = self.underwater_by_route.get(route)
            if under:
                return AreaMap(key, key, (under,), True)
            return AreaMap(
                key, key, (),
                f"no Underwater* map declares an emerge connection to {route}",
            )

        m = _ROUTE.match(key)
        if m:
            name = f"Route{m.group(1)}"
            if name in self.index:
                return AreaMap(key, key, (name,), True)

        # A slug carries no floor parenthesis ("cave-of-origin-1f"), but the
        # display label it came with does ("Cave of Origin (1F)"), and that
        # is the form that spells CaveOfOrigin_1F. Try the label first.
        label = self._label_of.get(key)
        if label:
            spelled = self._spell(label) or self._spell(key)
            if spelled:
                return AreaMap(key, key, (spelled,), True)

        # A display label rather than a slug: several slugs may share it.
        slugs = self._by_label.get(key)
        if slugs and key not in self.index:
            if len(slugs) == 1:
                return self._resolve(next(iter(slugs)))
            merged: list[str] = []
            for slug in sorted(slugs):
                for name in self._resolve(slug).maps:
                    if name not in merged:
                        merged.append(name)
            return AreaMap(
                key, "", tuple(merged), False,
                f"the label {key!r} is shared by {len(slugs)} dataset areas "
                f"({', '.join(sorted(slugs))})",
            )

        spelled = self._spell(key)
        if spelled:
            return AreaMap(key, key, (spelled,), True)

        camel, _ = _split_label(label or key)
        group = tuple(n for n in sorted(self.index) if n.startswith(camel + "_"))
        if group:
            return AreaMap(
                key, key, group, False,
                f"{key!r} spells no single map; {len(group)} maps share the "
                f"prefix {camel!r}",
            )
        return AreaMap(
            key, key, (), False,
            f"{key!r} does not spell any of the {len(self.index)} decomp map names",
        )

    def _spell(self, text: str) -> str | None:
        """The one map name ``text`` spells, if it spells exactly one."""
        camel, qualifier = _split_label(text)
        if qualifier and _FLOOR.match(qualifier):
            for form in (f"{camel}_{qualifier.upper()}", f"{camel}_{qualifier.title()}"):
                if form in self.index:
                    return form
        return camel if camel in self.index else None


def _split_label(label: str) -> tuple[str, str | None]:
    """``"Granite Cave (B1F)"`` -> ``("GraniteCave", "B1F")``."""
    text = label.replace("-area", "").replace("-", " ") if "-" in label else label
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", text)
    base, qualifier = (m.group(1), m.group(2)) if m else (text, None)
    return re.sub(r"[^A-Za-z0-9]", "", base.title()), qualifier


# ---- the objective -----------------------------------------------------------


@dataclass(slots=True)
class Step:
    """One actionable row of a plan."""

    dex: int
    natdex: int
    species: int
    name: str
    label: str
    route: str
    #: The map to go to, from the harness's own map index. Empty when the
    #: step happens in the party (evolve, breed) or has no map at all.
    map_name: str = ""
    #: Every map the step could be done on, when more than one qualifies.
    maps: tuple[str, ...] = ()
    #: Dataset area label, for a step that came from the dataset.
    area: str = ""
    method: str = ""
    min_level: int = 0
    max_level: int = 0
    chance: float = 0.0
    encounter_rate: int = 0
    #: Species you must already own for this step (evolve/breed source).
    source: int = 0
    detail: str = ""
    cost: float = 0.0
    #: Why this step cannot be executed right now, if it cannot.
    blocked: str | None = None
    #: Sweep group: a map name, or PARTY_GROUP for in-party work.
    group: str = ""

    def __str__(self):
        where = self.group if self.group != PARTY_GROUP else "party"
        return f"[{self.dex:>3}] {self.name:<11} {self.route:<9} {where:<32} {self.detail}"


class DexTarget:
    """The post-Elite-Four objective: fill the regional dex, solo.

    Construct once against a live backend and reuse; the dataset, the
    evolution table and the encounter table are all read at construction.
    """

    def __init__(self, emu, names, consts, mapdata, spec=None,
                 dex_id=None, paired_with=None):
        if spec is not None:
            dex_id = dex_id or spec.dex_id
            paired_with = paired_with or spec.paired_with
        if not dex_id:
            raise ValueError(
                "DexTarget needs a dex_id (or a GameSpec carrying one); "
                f"available datasets: {', '.join(dataset_games())}"
            )
        self.emu = emu
        self.names = names
        self.consts = consts
        self.mapdata = mapdata
        self.dex_id = dex_id
        self.paired_with = paired_with

        self.raw = load_dataset(dex_id)
        self.evolutions = EvolutionTable(emu, names, consts)
        self.wild = WildTable(emu, names, mapdata)
        self.atlas = AreaAtlas(mapdata)

        #: Loud, not silent: anything the objective could not fully resolve.
        self.warnings: list[str] = []
        self.last_plan_reason: str = ""
        self.last_progress_reason: str = ""

        self.entries: tuple[DexEntry, ...] = self._build_entries()
        self.by_dex = {e.dex: e for e in self.entries}
        self.by_natdex = {e.natdex: e for e in self.entries}
        self.by_species = {e.species: e for e in self.entries if e.species}
        self.achievable, self.out_of_reach = self._partition()
        self.achievable_natdex = frozenset(e.natdex for e in self.achievable)

        self._sb2 = cstruct.layout("SaveBlock2")
        self._sb1 = cstruct.layout("SaveBlock1")
        self._pokedex = cstruct.layout("Pokedex")
        self._storage = cstruct.layout("PokemonStorage", "pokemon.h")
        self.dex_flag_bytes = self._derive_dex_flag_bytes()

    # ---- construction -------------------------------------------------

    def _regional(self) -> dict:
        dexes = self.raw.get("dexes") or []
        if not dexes:
            raise ValueError(f"data/dex/{self.dex_id}.json has no dexes[]")
        return dexes[0]

    def _build_entries(self) -> tuple[DexEntry, ...]:
        out = []
        for raw in self._regional()["entries"]:
            species = self.evolutions.species_of_natdex(raw["natdex"])
            if species is None:
                self.warnings.append(
                    f"dex #{raw['dex']} {raw['name']} has natdex "
                    f"{raw['natdex']}, which gSpeciesToNationalPokedexNum "
                    f"does not list"
                )
                species = 0
            encounters = []
            for loc in raw.get("locations") or ():
                self.atlas.register(loc["area"], loc["areaSlug"])
                for var in loc.get("variants") or ():
                    encounters.append(
                        Encounter(
                            area=loc["area"],
                            area_slug=loc["areaSlug"],
                            method=var["method"],
                            method_label=var["methodLabel"],
                            min_level=var.get("minLevel") or 0,
                            max_level=var.get("maxLevel") or 0,
                            chance=var.get("chance") or 0,
                            conditions=tuple(
                                c["label"] for c in (var.get("conditions") or ())
                            ),
                        )
                    )
            trade = raw.get("tradeEvolution")
            out.append(
                DexEntry(
                    dex=raw["dex"],
                    natdex=raw["natdex"],
                    species=species,
                    slug=raw["slug"],
                    name=raw["name"],
                    rom_name=self.names.species(species) if species else raw["name"],
                    types=tuple(raw.get("types") or ()),
                    obtainable=bool(raw.get("obtainable")),
                    unobtainable=bool(raw.get("unobtainable")),
                    exclusive_to=raw.get("exclusiveTo"),
                    needs_trade_partner=bool(raw.get("needsTradePartner")),
                    trade_evolution=(
                        (trade["from"], trade.get("heldItem")) if trade else None
                    ),
                    encounters=tuple(encounters),
                    notes=tuple(n["text"] for n in (raw.get("notes") or ())),
                )
            )
        return tuple(out)

    def _partition(self) -> tuple[tuple[DexEntry, ...], tuple[OutOfReach, ...]]:
        """Split the regional dex into what one player can get and what they
        cannot, with a sentence per exclusion."""
        keep, drop = [], []
        for e in self.entries:
            if e.unobtainable:
                drop.append(OutOfReach(
                    e.dex, e.natdex, e.name, OUT_OF_REACH_UNOBTAINABLE,
                    "the dataset marks this entry unobtainable in this game",
                ))
            elif e.exclusive_to and e.exclusive_to != self.dex_id:
                drop.append(OutOfReach(
                    e.dex, e.natdex, e.name, OUT_OF_REACH_VERSION,
                    f"exclusive to {e.exclusive_to.title()}; "
                    f"{self.dex_id.title()} can only trade for it",
                ))
            elif e.trade_evolution:
                # The dataset speaks for the SERIES; the ROM speaks for this
                # cartridge, and when they disagree the ROM wins. Milotic is
                # the case: the dataset says "trade Feebas holding a Prism
                # Scale", which is how it works from Gen 5 -- but this
                # cartridge's own gEvolutionTable says EVO_BEAUTY
                # (src/data/pokemon/evolution.h), an entirely in-cartridge
                # evolution. A species the dataset calls trade-locked stays
                # achievable when the ROM offers any non-trade route into it.
                rom_routes = (
                    self.evolutions.pre_evolutions(e.species)
                    if e.species else ()
                )
                non_trade = [r for r in rom_routes if not r.needs_trade]
                if non_trade:
                    keep.append(e)
                    continue
                source, item = e.trade_evolution
                holding = f" while holding a {item}" if item else ""
                drop.append(OutOfReach(
                    e.dex, e.natdex, e.name, OUT_OF_REACH_TRADE_EVOLUTION,
                    f"only evolves when {source} is traded{holding}",
                ))
            elif e.name in self._event_only_names():
                # The engine's own completion rating concedes these
                # (src/birch_pc.c:94-102 discounts Jirachi and Deoxys), and
                # the Eon partner is version-picked: in Sapphire the roamer is
                # LATIAS (include/constants/species.h:1283), so LATIOS exists
                # only behind the Eon Ticket event island.
                drop.append(OutOfReach(
                    e.dex, e.natdex, e.name, OUT_OF_REACH_EVENT,
                    "only from an external distribution or event item",
                ))
            elif e.needs_trade_partner:
                drop.append(OutOfReach(
                    e.dex, e.natdex, e.name, OUT_OF_REACH_TRADE_PARTNER,
                    "needs a second player",
                ))
            else:
                keep.append(e)
        return tuple(keep), tuple(drop)

    def _event_only_names(self) -> frozenset[str]:
        """Species this cartridge can never hand out without an event."""
        eon = "Latios" if self.dex_id == "sapphire" else "Latias"
        return frozenset(("Jirachi", "Deoxys", eon))

    def choice_locked(self, state=None) -> frozenset[int]:
        """Natdex numbers shut off by a choice this save has already made.

        Two of the game's gifts are exclusive choices: Birch offers one
        starter of three, and Route 111 offers one fossil of two -- taking
        either hides BOTH (Route111/scripts.inc:55-58), and pokeruby has no
        Desert Underpass to recover the other. Until the choice is made
        nothing is locked; afterwards the unchosen lines are as gone as a
        version exclusive, and counting them in the target would leave the
        dex objective permanently short of 100%.
        """
        caught, seen = self.dex_flags(state)
        locked: set[int] = set()
        for lines, taken in self._exclusive_groups(caught, seen, state):
            if taken is None:
                continue
            for ln in lines:
                if ln is not taken:
                    locked.update(ln)
        return frozenset(locked)

    def _line_natdex(self, root_species: int) -> list[int]:
        """Every natdex number in the evolution line rooted at this species."""
        out, frontier = [], [root_species]
        while frontier:
            sp = frontier.pop()
            if sp in out:
                continue
            out.append(sp)
            frontier.extend(
                evo.to_species for evo in self.evolutions.evolutions(sp)
            )
        return [self.evolutions.natdex(sp) for sp in out]

    def _exclusive_groups(self, caught, seen, state=None):
        """``[(lines, taken_or_None)]`` for the game's either/or gifts.

        Birch offers one starter of three; Route 111 offers one fossil of two.
        The fossil pair keys off ``caught | seen`` rather than ``caught``
        because taking the item is what destroys the other one, and that
        happens long before anything is registered.
        """
        groups = []
        starters = [self._line_natdex(sp) for sp in self.starters]
        chosen = [ln for ln in starters if any(n in caught for n in ln)]
        groups.append((starters, chosen[0] if len(chosen) == 1 else None))

        # SPECIES_* off the constants table, not a `species_id` method --
        # `names` has no such method, so the original
        # `getattr(self.names, "species_id", lambda _: None)` resolved to the
        # fallback every single time and this entire branch was DEAD. The
        # fossil pair has never been treated as exclusive by this code.
        roots = [
            sp for sp in (self.consts.species.get(f"SPECIES_{name}")
                          for name in ("LILEEP", "ANORITH"))
            if sp
        ]
        if len(roots) != 2:
            self.warnings.append(
                "SPECIES_LILEEP/SPECIES_ANORITH are not both in "
                "constants/species.h -- the Route 111 fossil choice cannot be "
                "modelled and the dex target will be over-counted by one line"
            )
        if len(roots) == 2:
            lines = [self._line_natdex(sp) for sp in roots]
            # ASK THE GAME WHICH FOSSIL IT REVIVED. `caught | seen` cannot
            # answer this: `seen` is poison for an exclusivity test, because
            # the Elite Four SHOW you the line you can never own -- Steven's
            # CRADILY sets the LILEEP line's seen bit, so BOTH lines read as
            # "held", `len(taken)` came out 2, and the ternary below fell to
            # None. The group therefore never locked, and the dex target kept
            # advertising LILEEP and CRADILY as achievable on a save that had
            # already revived the claw fossil. An over-counted target is not a
            # cosmetic error: it sends a hunt after a species the cartridge
            # cannot produce.
            #
            # VAR_WHICH_FOSSIL_REVIVED is the engine's own record (1 = root ->
            # LILEEP, 2 = claw -> ANORITH; src/fossil_specials.c). The hide
            # flags are the fallback: Route111/scripts.inc:55-58 sets BOTH on
            # either pickup and nothing in the game clears them, and Sapphire
            # has no MirageTower or DesertUnderpass map at all -- those are
            # Emerald additions -- so once they are set the un-revived line is
            # gone for good.
            revived = 0
            try:
                revived = int(state.var("VAR_WHICH_FOSSIL_REVIVED"))
            except Exception:                      # noqa: BLE001
                revived = 0
            if revived in (1, 2):
                # 1 -> root/LILEEP is ours, so the ANORITH line is locked out;
                # 2 -> the reverse. `taken` names the line we KEPT.
                groups.append((lines, lines[0] if revived == 1 else lines[1]))
            else:
                taken = [ln for ln in lines if any(n in caught for n in ln)]
                groups.append((lines, taken[0] if len(taken) == 1 else None))
        return groups

    def exclusive_surplus(self, state=None) -> int:
        """Dex slots NO save can fill, because an either/or choice is PENDING.

        `choice_locked` only fires once a choice has been MADE, so before it
        the target counted every option: three starter lines and BOTH fossil
        lines. That is not a reachable number. Route 111's two fossils sit
        side by side and taking either sets both hide flags and removes both
        objects in the same script (data/maps/Route111/scripts.inc:57-59,
        79-81), with no `clearflag` anywhere in the game and -- this is the
        part that fooled me -- **no Desert Underpass in Sapphire at all**,
        because that map is an Emerald addition. So one fossil line, two dex
        slots, is permanently unreachable no matter what this save does.

        The identity stays honest: both lines remain routable options until
        one is taken, so this returns a COUNT rather than locking a line the
        player can still legitimately choose.
        """
        caught, seen = self.dex_flags(state)
        surplus = 0
        for lines, taken in self._exclusive_groups(caught, seen, state):
            if taken is not None or len(lines) < 2:
                continue
            surplus += (sum(len(ln) for ln in lines)
                        - max(len(ln) for ln in lines))
        return surplus

    def _derive_dex_flag_bytes(self) -> int:
        """``DEX_FLAGS_NO``, derived two ways and cross-checked.

        include/global.h:666 defines it with a ternary that no expression
        parser here evaluates, so it is computed from
        ``POKEMON_SLOTS_NUMBER`` and checked against the real gap between
        ``Pokedex.owned`` and ``Pokedex.seen``. A mismatch means the header
        and the parsed layout disagree, which must not be papered over.
        """
        gap = self._pokedex["seen"] - self._pokedex["owned"]
        env = cconst.parse_defines(str(paths.INCLUDE / "global.h"))
        slots = env.get("POKEMON_SLOTS_NUMBER")
        if not slots:
            raise ValueError("POKEMON_SLOTS_NUMBER is not defined in include/global.h")
        expected = -(-slots // 8)
        if gap != expected:
            raise ValueError(
                f"struct Pokedex has {gap} bytes between owned and seen, but "
                f"POKEMON_SLOTS_NUMBER={slots} implies {expected}"
            )
        return gap

    # ---- exclusions, for a caller that wants to explain itself --------

    def out_of_reach_by_reason(self) -> dict[str, tuple[OutOfReach, ...]]:
        out: dict[str, list[OutOfReach]] = {}
        for item in self.out_of_reach:
            out.setdefault(item.reason, []).append(item)
        return {k: tuple(v) for k, v in out.items()}

    @property
    def event_only(self) -> tuple[DexEntry, ...]:
        """Inside the achievable set on paper, but only ever handed out by an
        external distribution.

        The engine concedes the same point: ``src/birch_pc.c:94-102``
        discounts Jirachi and Deoxys when rating dex completion, so 200 of
        202 counts as complete.
        """
        return tuple(
            e for e in self.achievable
            if e.event_only or (not e.encounters and not self._any_route_exists(e))
        )

    def _any_route_exists(self, entry: DexEntry) -> bool:
        if entry.species in self.wild.species:
            return True
        if self.evolutions.pre_evolutions(entry.species):
            return True
        return bool(self.evolutions.evolutions(entry.species))

    def area_to_map(self, area_slug_or_name: str) -> AreaMap:
        """See :meth:`AreaAtlas.area_to_map`."""
        return self.atlas.area_to_map(area_slug_or_name)

    def unmapped_areas(self) -> tuple[AreaMap, ...]:
        """Every dataset area this harness cannot pin to a single map.

        Includes both the genuinely map-less ones and the ones that resolve
        to a group, because a caller sweeping routes needs to know which
        rows it is about to be vague about.
        """
        out = []
        for slug in sorted({e.area_slug for x in self.entries for e in x.encounters}):
            resolved = self.area_to_map(slug)
            if not resolved.exact:
                out.append(resolved)
        return tuple(out)

    # ---- the live Pokedex --------------------------------------------

    def _dex_bits(self, state) -> tuple[bytes, bytes, bytes, bytes]:
        """``(owned, seen, dexSeen2, dexSeen3)``.

        The engine keeps three copies of the seen bits and treats a
        disagreement as tampering, zeroing the flag
        (``src/pokedex.c:3999-4030``). Reading all four lets
        :meth:`progress` report the same answer the game would, and say so
        when the mirrors disagree.
        """
        n = self.dex_flag_bytes
        dex = self.emu.resolve("gSaveBlock2") + self._sb2["pokedex"]
        sb1 = self.emu.resolve("gSaveBlock1")
        return (
            self.emu.read(dex + self._pokedex["owned"], n),
            self.emu.read(dex + self._pokedex["seen"], n),
            self.emu.read(sb1 + self._sb1["dexSeen2"], n),
            self.emu.read(sb1 + self._sb1["dexSeen3"], n),
        )

    def dex_flags(self, state=None) -> tuple[frozenset[int], frozenset[int]]:
        """``(caught, seen)`` as sets of national dex numbers.

        Reproduces ``GetSetPokedexFlag`` (src/pokedex.c:3986-4041) exactly,
        including its mirror checks -- minus the side effect, since a reader
        has no business clearing the player's flags.
        """
        owned, seen, mirror2, mirror3 = self._dex_bits(state)
        caught_set, seen_set, desynced = set(), set(), []
        for natdex in range(1, self.dex_flag_bytes * 8 + 1):
            index, mask = (natdex - DEX_FLAG_BIAS) // 8, 1 << ((natdex - DEX_FLAG_BIAS) % 8)
            o, s = owned[index] & mask, seen[index] & mask
            m2, m3 = mirror2[index] & mask, mirror3[index] & mask
            if s and s == m2 and s == m3:
                seen_set.add(natdex)
            elif s or m2 or m3:
                desynced.append(natdex)
            if o and o == s and o == m2 and o == m3:
                caught_set.add(natdex)
        if desynced:
            self.warnings.append(
                f"{len(desynced)} national dex numbers have desynced seen "
                f"mirrors (gSaveBlock2.pokedex.seen vs gSaveBlock1.dexSeen2/3); "
                f"the game would clear them on read: {desynced[:8]}"
            )
        return frozenset(caught_set), frozenset(seen_set)

    def progress(self, state=None) -> dict:
        """How far the objective has got, from the live dex bitfields.

        ``caught``/``seen`` count regional-dex slots, the same population the
        game's own Birch rating counts. ``percent`` and ``remaining`` are
        against the *achievable* set, because that is the objective -- so
        ``caught`` and ``caught_achievable`` differ if a version exclusive
        ever arrived by trade.
        """
        caught, seen = self.dex_flags(state)
        regional = {e.natdex for e in self.entries}
        caught_regional = caught & regional
        locked = self.choice_locked(state)
        target = self.achievable_natdex - locked
        caught_achievable = caught & target
        # A pending either/or gift makes the raw target unreachable: both
        # Route 111 fossil lines are still routable, but only one of them can
        # ever be registered, so two of these slots are structurally dead.
        surplus = self.exclusive_surplus(state)
        achievable = len(target) - surplus
        self.last_progress_reason = (
            f"{len(caught_achievable)}/{achievable} achievable owned; "
            f"{len(self.out_of_reach)} slots out of reach"
            + (f"; {surplus} more unreachable behind a pending either/or "
               f"choice" if surplus else "")
        )
        return {
            "caught": len(caught_regional),
            "seen": len(seen & regional),
            "achievable": achievable,
            "caught_achievable": len(caught_achievable),
            "percent": round(100.0 * len(caught_achievable) / achievable, 1),
            "remaining": achievable - len(caught_achievable),
        }

    def missing(self, state=None) -> tuple[DexEntry, ...]:
        """Achievable species whose caught flag is not set."""
        caught, _ = self.dex_flags(state)
        locked = self.choice_locked(state)
        return tuple(
            e for e in self.achievable
            if e.natdex not in caught and e.natdex not in locked
        )

    @functools.cached_property
    def starters(self) -> tuple[int, ...]:
        """``sStarterMons`` (src/starter_choose.c:50), read not assumed.

        The plan needs it because the dataset lists all three starters as
        Route 101 gifts -- correctly, since each of them is one -- while a
        solo run receives exactly one. AGENTS.md gotcha 14 is the same table
        for the opposite reason.
        """
        size = self.emu.sym.size("sStarterMons")
        if not size or size % 2:
            raise ValueError(
                f"sStarterMons is {size} bytes, not a whole number of u16 species"
            )
        raw = self.emu.read("sStarterMons", size)
        return tuple(
            int.from_bytes(raw[i:i + 2], "little") for i in range(0, size, 2)
        )

    # ---- what the player physically has ------------------------------

    def box_free_slots(self) -> int:
        """Empty slots across every PC box.

        Occupancy, not identity: `owned_species` returns a SET, so it cannot
        answer "is there room". Catching needs this because a full party is not
        a refusal in Gen 3 -- `GiveMonToPlayer` redirects to `SendMonToPC`
        (src/pokemon_2.c:964-983) -- so the only real limit is total storage.
        """
        base = self.emu.resolve("gPokemonStorage") + self._storage["boxes"]
        span = self._storage["boxNames"] - self._storage["boxes"]
        slots = span // pokemon.BOX_SIZE
        free = 0
        for i in range(slots):
            blob = self.emu.read(base + i * pokemon.BOX_SIZE, pokemon.BOX_SIZE)
            if not any(blob):
                free += 1
        return free

    def owned_species(self, state) -> frozenset[int]:
        """Species ids in the live party and in the PC boxes.

        The party comes from ``gPlayerParty`` through the state reader (the
        save block's mirror is stale after any battle); the boxes are read
        straight out of ``gPokemonStorage``, whose slot count is derived
        from the gap between ``boxes`` and ``boxNames``
        (include/pokemon.h:323-329) rather than multiplied out by hand.
        """
        out = {mon.species for mon in state.party()
               if mon.species and not mon.is_egg}
        out.update(mon.species for _, mon in self.boxed())
        return frozenset(out)

    def boxed(self) -> tuple[tuple[int, object], ...]:
        """``(slot, mon)`` for every real Pokemon in the PC boxes.

        Split out of :meth:`owned_species` because the evolution half of the
        dex needs the MONS, not just their species: a boxed mon carries EXP
        but NO level (the box format has no level field at all), so deciding
        "raise MARILL to 18" needs the level derived per mon.
        """
        base = self.emu.resolve("gPokemonStorage") + self._storage["boxes"]
        span = self._storage["boxNames"] - self._storage["boxes"]
        if span % pokemon.BOX_SIZE:
            raise ValueError(
                f"gPokemonStorage.boxes spans {span} bytes, not a whole number "
                f"of {pokemon.BOX_SIZE}-byte box slots"
            )
        blob = self.emu.read(base, span)
        found, bad = [], 0
        for i in range(span // pokemon.BOX_SIZE):
            raw = blob[i * pokemon.BOX_SIZE:(i + 1) * pokemon.BOX_SIZE]
            mon = pokemon.parse_mon(raw)
            if mon is None:
                continue
            if not mon.checksum_ok:
                bad += 1
                continue
            # parse_mon leaves the name blank on purpose -- it has no charmap
            # ("filled by the caller", pokemon.py:194) -- and this caller never
            # did, so EVERY boxed mon read as nickname ''. That is not cosmetic:
            # it silently defeats matching a mon by name (a withdraw by nickname
            # failed for a boxed PELIPPER earlier in this run) and it makes
            # auditing names impossible, which is worse -- a scan for mons
            # wrongly named "A" returned zero from the boxes no matter what was
            # in them, so a real one hid there until it was withdrawn and
            # evolved. A BoxPokemon is the first 80 bytes of a Pokemon, so the
            # party's own offsets apply with box == 0.
            mon.nickname = self.emu.charmap.decode(
                raw[0x08:0x08 + pokemon.NICKNAME_LEN])
            mon.ot_name = self.emu.charmap.decode(
                raw[0x14:0x14 + pokemon.OT_NAME_LEN])
            if mon.species and not mon.is_egg:
                found.append((i, mon))
        if bad:
            self.warnings.append(
                f"{bad} PC box slots failed their checksum and were skipped"
            )
        return tuple(found)

    def boxed_level(self, mon) -> int:
        """The level of a boxed mon, derived from its EXP the way the game does."""
        return self.names.level_from_exp(mon.species, mon.experience)

    # ---- routing ------------------------------------------------------

    def _wild_steps(self, entry: DexEntry) -> list[Step]:
        out = []
        for slot in self.wild.for_species(entry.species):
            surcharge = METHOD_SURCHARGE.get(slot.kind, 1.0)
            cost = (
                ROUTE_BASE_COST[ROUTE_WILD]
                + surcharge
                + 100.0 / max(slot.slot_chance, 1.0) / 10.0
            )
            out.append(Step(
                dex=entry.dex, natdex=entry.natdex, species=entry.species,
                name=entry.rom_name, label=entry.name, route=ROUTE_WILD,
                map_name=slot.map_name, maps=(slot.map_name,),
                area=slot.map_name, method=slot.kind,
                min_level=slot.min_level, max_level=slot.max_level,
                chance=round(slot.slot_chance, 2),
                encounter_rate=slot.encounter_rate,
                detail=(
                    f"{_verb(slot.kind)} on {slot.map_name} "
                    f"(L{slot.min_level}-{slot.max_level}, "
                    f"{slot.slot_chance:.0f}% of encounters)"
                ),
                cost=cost, group=slot.map_name,
            ))
        return out

    def _dataset_steps(self, entry: DexEntry, taken: int = 0) -> list[Step]:
        """Everything a wild table cannot express: gifts, NPC trades,
        fossils, statics, the roamer, and the external events."""
        out = []
        for enc in entry.encounters:
            route = DATASET_ROUTE.get(enc.method)
            if enc.is_event:
                route = ROUTE_EVENT
            elif enc.is_fossil:
                route = ROUTE_FOSSIL
            if route is None:
                self.warnings.append(
                    f"{entry.name}: dataset method {enc.method!r} at "
                    f"{enc.area_slug!r} has no route kind"
                )
                continue
            # The ROM's own table already covers ordinary wild encounters,
            # with exact maps and exact levels. Only keep a dataset wild row
            # when the ROM has nothing -- otherwise it is a worse duplicate.
            if route == ROUTE_WILD and entry.species in self.wild.species:
                continue
            resolved = self.area_to_map(enc.area_slug)
            blocked = None
            if route == ROUTE_EVENT:
                blocked = f"{enc.method_label} is an external distribution"
            elif enc.conditions:
                blocked = "; ".join(enc.conditions)
            elif taken and entry.species in self.starters and entry.species != taken:
                # The dataset lists all three starters as Route 101 gifts and
                # is right to -- but Birch's bag opens once. Offering the two
                # you did not take would be the harness lying, so it is read
                # off sStarterMons (src/starter_choose.c:50) instead.
                blocked = (
                    f"the starter choice is one of three and you took "
                    f"{self.names.species(taken)}"
                )
            if not resolved.maps and route != ROUTE_EVENT:
                blocked = blocked or resolved.reason
            level = (
                f" (L{enc.min_level})" if enc.min_level == enc.max_level
                else f" (L{enc.min_level}-{enc.max_level})"
            )
            where = resolved.primary or enc.area
            out.append(Step(
                dex=entry.dex, natdex=entry.natdex, species=entry.species,
                name=entry.rom_name, label=entry.name, route=route,
                map_name=resolved.primary, maps=resolved.maps, area=enc.area,
                method=enc.method, min_level=enc.min_level,
                max_level=enc.max_level, chance=float(enc.chance),
                detail=f"{enc.method_label} at {where}{level}"
                       + (f" -- {'; '.join(enc.conditions)}" if enc.conditions else "")
                       + ("" if resolved.exact or not resolved.reason
                          else f" [{resolved.reason}]"),
                cost=ROUTE_BASE_COST[route] + (0.0 if resolved.exact else 0.25),
                blocked=blocked,
                group=resolved.primary or enc.area,
            ))
        return out

    def _evolution_steps(self, entry: DexEntry, owned, guard, taken=0) -> list[Step]:
        out = []
        for evo in self.evolutions.pre_evolutions(entry.species):
            if evo.needs_trade:
                continue
            source_name = self.names.species(evo.from_species)
            how = self.evolutions.describe(evo)
            if evo.from_species in owned:
                out.append(Step(
                    dex=entry.dex, natdex=entry.natdex, species=entry.species,
                    name=entry.rom_name, label=entry.name, route=ROUTE_EVOLVE,
                    method=evo.method_name, source=evo.from_species,
                    min_level=evo.level or 0, max_level=evo.level or 0,
                    detail=f"you already own {source_name}: {how}",
                    cost=ROUTE_BASE_COST[ROUTE_EVOLVE], group=PARTY_GROUP,
                ))
                continue
            prior = self._best_step(evo.from_species, owned, guard, taken)
            if prior is None:
                continue
            out.append(Step(
                dex=entry.dex, natdex=entry.natdex, species=entry.species,
                name=entry.rom_name, label=entry.name, route=ROUTE_EVOLVE,
                map_name=prior.map_name, maps=prior.maps, area=prior.area,
                method=evo.method_name, min_level=evo.level or 0,
                max_level=evo.level or 0, source=evo.from_species,
                detail=f"{prior.detail}, then {how}",
                cost=prior.cost + 0.5, blocked=prior.blocked,
                group=prior.group,
            ))
        return out

    def _breed_steps(self, entry: DexEntry, owned, guard, taken=0) -> list[Step]:
        """A baby form with no catch location hatches from its own
        evolution's egg -- derived from ``gEvolutionTable``, so Azurill
        resolves to Marill without a table of baby forms living here."""
        out = []
        for evo in self.evolutions.evolutions(entry.species):
            for parent in self.evolutions.chain(evo.to_species):
                parent_name = self.names.species(parent)
                if parent in owned:
                    out.append(Step(
                        dex=entry.dex, natdex=entry.natdex, species=entry.species,
                        name=entry.rom_name, label=entry.name, route=ROUTE_BREED,
                        method=ROUTE_BREED, source=parent,
                        detail=f"breed {parent_name} at the Route 117 day care",
                        cost=ROUTE_BASE_COST[ROUTE_BREED],
                        group=PARTY_GROUP,
                    ))
                    continue
                prior = self._best_step(parent, owned, guard, taken)
                if prior is None:
                    continue
                out.append(Step(
                    dex=entry.dex, natdex=entry.natdex, species=entry.species,
                    name=entry.rom_name, label=entry.name, route=ROUTE_BREED,
                    map_name=prior.map_name, maps=prior.maps, area=prior.area,
                    method=ROUTE_BREED, source=parent,
                    detail=f"{prior.detail}, then breed {parent_name} at the "
                           f"Route 117 day care",
                    cost=prior.cost + ROUTE_BASE_COST[ROUTE_BREED],
                    blocked=prior.blocked, group=prior.group,
                ))
        return out

    def _best_step(self, species: int, owned: frozenset[int], guard: set[int],
                   taken: int = 0) -> Step | None:
        """Cheapest way to obtain ``species``, or None if nothing reaches it.

        ``guard`` breaks the cycle a baby form creates: Azurill's breeding
        parent is Marill, whose pre-evolution is Azurill.
        """
        if species in guard:
            return None
        entry = self.by_species.get(species)
        if entry is None:
            # Not a regional-dex slot (a National-only pre-evolution). Still
            # routable, so synthesise the minimum an inner step needs.
            entry = DexEntry(
                dex=0, natdex=self.evolutions.natdex(species), species=species,
                slug="", name=self.names.species(species),
                rom_name=self.names.species(species), types=(),
                obtainable=True, unobtainable=False, exclusive_to=None,
                needs_trade_partner=False, trade_evolution=None, encounters=(),
            )
        guard.add(species)
        try:
            options = self._wild_steps(entry) + self._dataset_steps(entry, taken)
            options += self._evolution_steps(entry, owned, guard, taken)
            if not [o for o in options if o.blocked is None]:
                options += self._breed_steps(entry, owned, guard, taken)
        finally:
            guard.discard(species)
        if not options:
            return None
        return min(options, key=lambda s: (s.blocked is not None, s.cost, s.dex))

    def held_starter(self, owned) -> int:
        """Which of ``sStarterMons`` this run took, 0 if none yet.

        Answered by looking for any member of a starter's evolution chain
        among the species actually held, rather than by reading
        ``VAR_STARTER_MON`` -- that var is 0 before the choice is made, which
        is indistinguishable from "took Treecko".
        """
        for starter in self.starters:
            if any(m in owned for m in self.evolutions.chain(starter)):
                return starter
        return 0

    def routes(self, species: int, owned=frozenset()) -> tuple[Step, ...]:
        """Every route to ``species``, cheapest first. For auditing a plan
        row that looks wrong."""
        entry = self.by_species.get(species)
        if entry is None:
            raise KeyError(f"species {species} is not in the {self.dex_id} regional dex")
        guard: set[int] = set()
        taken = self.held_starter(owned)
        options = (
            self._wild_steps(entry)
            + self._dataset_steps(entry, taken)
            + self._evolution_steps(entry, owned, guard, taken)
            + self._breed_steps(entry, owned, guard, taken)
        )
        return tuple(sorted(options, key=lambda s: (s.blocked is not None, s.cost)))

    def plan(self, state) -> list[Step]:
        """An ordered, sweepable list of what to get next.

        One step per missing species -- the cheapest route to it -- grouped
        so every step on a map is adjacent, and the groups ordered by their
        cheapest member. Steps that cannot be executed (an external
        distribution, a fossil you do not hold) sort last and carry
        ``blocked``, because dropping them would hide the shortfall.
        """
        owned = self.owned_species(state)
        taken = self.held_starter(owned)
        missing = self.missing(state)
        steps, unroutable = [], []
        for entry in missing:
            best = self._best_step(entry.species, owned, set(), taken)
            if best is None:
                unroutable.append(entry)
                steps.append(Step(
                    dex=entry.dex, natdex=entry.natdex, species=entry.species,
                    name=entry.rom_name, label=entry.name, route=ROUTE_EVENT,
                    detail="no route: no wild encounter, no dataset location, "
                           "no evolution and no breeding parent",
                    cost=ROUTE_BASE_COST[ROUTE_EVENT] + 1,
                    blocked="unreachable in this game",
                    group="UNREACHABLE",
                ))
                continue
            steps.append(best)

        cheapest: dict[str, float] = {}
        for s in steps:
            key = s.group or "UNGROUPED"
            s.group = key
            cheapest[key] = min(cheapest.get(key, s.cost), s.cost)
        steps.sort(key=lambda s: (
            s.blocked is not None,
            cheapest[s.group],
            s.group,
            s.cost,
            s.dex,
        ))

        blocked = sum(1 for s in steps if s.blocked)
        # The denominator honors choices already made: once a starter is
        # taken, the other six are not "missing", they are gone.
        target_now = len(self.achievable_natdex - self.choice_locked(state))
        self.last_plan_reason = (
            f"{len(missing)} of {target_now} achievable species "
            f"missing; {len(steps) - blocked} actionable, {blocked} blocked, "
            f"{len(unroutable)} with no route at all across "
            f"{len({s.group for s in steps})} groups"
        )
        return steps

    def sweep(self, state) -> dict[str, list[Step]]:
        """:meth:`plan`, folded into ``{group: steps}`` in plan order."""
        out: dict[str, list[Step]] = {}
        for step in self.plan(state):
            out.setdefault(step.group, []).append(step)
        return out

    def summary(self, state) -> str:
        p = self.progress(state)
        return (
            f"dex {p['caught_achievable']}/{p['achievable']} ({p['percent']}%), "
            f"{p['remaining']} to go; {len(self.out_of_reach)} out of reach "
            f"({', '.join(f'{k}x{len(v)}' for k, v in self.out_of_reach_by_reason().items())})"
        )


def _verb(kind: str) -> str:
    """What the player physically does for a wild encounter kind."""
    return {
        "land": "walk the grass",
        "water": "surf",
        "dive": "dive the seaweed",
        "rock_smash": "rock smash boulders",
        "old_rod": "fish with the OLD ROD",
        "good_rod": "fish with the GOOD ROD",
        "super_rod": "fish with the SUPER ROD",
    }.get(kind, kind)
