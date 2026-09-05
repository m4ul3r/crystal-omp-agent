"""The game registry: which games this harness can drive, and how.

The point of this module is that adding a game is *data*, not a code change to
anything that already works. A `GameSpec` says which decompilation supplies
the ground truth, which emulator core runs it, what the ROM must hash to, and
which adapter knows how to read that generation's memory.

Two honest statuses, borrowed from the regional-dex-buddy registry because it
is the right idea:

* ``live``     -- implemented and exercised against a real ROM here.
* ``declared`` -- the spec is real and the adapter handles the generation, but
  nothing has been run because the decompilation or the ROM is not present on
  this machine. A declared game must never *pretend* to work: `Game.open()`
  raises with exactly what is missing.

Generation differences that actually matter to an adapter, and where they bite:

| | Gen 1 | Gen 2 | Gen 3 |
|---|---|---|---|
| core | PyBoy | PyBoy | mGBA |
| address space | banked | banked | flat |
| symbols | `.sym` from rgbds | `.sym` from rgbds | `.sym` from the ELF |
| party mon | flat struct | flat struct | **encrypted + shuffled** |
| map grid | `.blk` + tileset collision | same | `.bin` metatile/collision/**elevation** |
| text | charmap.asm, one byte per char | same | charmap.txt, **multi-byte tokens** |
| screen text | flat tilemap, decodable | same | windows; read the engine's buffers |
"""

import hashlib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

LIVE = "live"
DECLARED = "declared"


@dataclass(frozen=True, slots=True)
class GameSpec:
    id: str
    name: str
    short_name: str
    generation: int
    region: str
    #: Emulator core: "mgba" for GBA, "pyboy" for GB/GBC.
    core: str
    #: The pret decompilation that supplies ground truth.
    decomp: str
    #: Dotted path to the adapter class that understands this generation.
    adapter: str
    #: sha1 of an acceptable ROM dump. Several revisions may be listed.
    rom_sha1: tuple[str, ...] = ()
    #: Extra make variables needed to build the matching ROM.
    build: dict = field(default_factory=dict)
    #: Basename of the symbol table the build emits.
    sym: str = ""
    #: Basename of the ROM the build emits.
    rom: str = ""
    status: str = DECLARED
    #: Matching game id in the regional-dex-buddy dataset, for the dex goal.
    dex_id: str | None = None
    #: The starter this harness picks, by species constant name.
    starter: str | None = None
    #: Paired version, whose exclusives are unobtainable solo.
    paired_with: str | None = None

    @property
    def platform(self):
        return "gba" if self.core == "mgba" else "gbc"

    @property
    def live(self):
        return self.status == LIVE


#: Every game the harness knows about. `live` entries are exercised here.
REGISTRY: dict[str, GameSpec] = {}


def _add(spec: GameSpec):
    REGISTRY[spec.id] = spec
    return spec


# ---- Gen 3 / Hoenn -- pokeruby ------------------------------------------------

_add(GameSpec(
    id="sapphire",
    name="Pokémon Sapphire",
    short_name="Sapphire",
    generation=3,
    region="Hoenn",
    core="mgba",
    decomp="pokeruby",
    adapter="pokeagent.adapters.gen3:Gen3Adapter",
    # Rev 2 (US) is what this harness was built and verified against.
    rom_sha1=("89b45fb172e6b55d51fc0e61989775187f6fe63c",),
    build={"GAME_VERSION": "SAPPHIRE", "GAME_REVISION": "2"},
    sym="pokesapphire_rev2.sym",
    rom="pokesapphire_rev2.gba",
    status=LIVE,
    dex_id="sapphire",
    starter="SPECIES_TORCHIC",
    paired_with="ruby",
))

_add(GameSpec(
    id="ruby",
    name="Pokémon Ruby",
    short_name="Ruby",
    generation=3,
    region="Hoenn",
    core="mgba",
    decomp="pokeruby",
    adapter="pokeagent.adapters.gen3:Gen3Adapter",
    rom_sha1=("5b64eacf892920518db4ec664e62a086dd5f5bc8",),
    build={"GAME_VERSION": "RUBY", "GAME_REVISION": "2"},
    sym="pokeruby_rev2.sym",
    rom="pokeruby_rev2.gba",
    # The adapter is identical -- same decomp, same memory layout. Only the
    # ROM is absent, so this is declared rather than live.
    status=DECLARED,
    dex_id="ruby",
    starter="SPECIES_TORCHIC",
    paired_with="sapphire",
))

# ---- Gen 2 / Johto -- pokecrystal --------------------------------------------

_add(GameSpec(
    id="crystal",
    name="Pokémon Crystal",
    short_name="Crystal",
    generation=2,
    region="Johto",
    core="pyboy",
    decomp="pokecrystal",
    adapter="pokeagent.adapters.gen2:Gen2Adapter",
    rom_sha1=(),  # any matching build; the .meta stamp is what is enforced
    build={},
    sym="pokecrystal.sym",
    rom="pokecrystal.gbc",
    # Live: pokecrystal builds here and PyBoy boots the result, so the Gen-2
    # data layer (symbols, charmap, constants, party, maps, BFS) is exercised.
    # The Gen-2 battle/menu stack is NOT, which is why the adapter's
    # capabilities omit "battle".
    status=LIVE,
    dex_id="crystal",
    starter="CYNDAQUIL",
))

_add(GameSpec(
    id="gold",
    name="Pokémon Gold",
    short_name="Gold",
    generation=2,
    region="Johto",
    core="pyboy",
    decomp="pokegold",
    adapter="pokeagent.adapters.gen2:Gen2Adapter",
    sym="pokegold.sym",
    rom="pokegold.gbc",
    status=DECLARED,
    dex_id="gold",
    starter="CYNDAQUIL",
    paired_with="silver",
))

# ---- Gen 1 / Kanto -- pokered ------------------------------------------------

_add(GameSpec(
    id="red",
    name="Pokémon Red",
    short_name="Red",
    generation=1,
    region="Kanto",
    core="pyboy",
    decomp="pokered",
    adapter="pokeagent.adapters.gen1:Gen1Adapter",
    sym="pokered.sym",
    rom="pokered.gbc",
    # Boots, decodes its screen and drives its menus here (pokered builds
    # with the vendored rgbds). state and nav are NOT ported and refuse by
    # name -- see Gen1Adapter -- which the capability set reflects.
    status=LIVE,
    dex_id="red",
    starter="CHARMANDER",
    paired_with="blue",
))


def get(game_id) -> GameSpec:
    try:
        return REGISTRY[game_id]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown game {game_id!r}; known games: {known}") from None


def live_games() -> list[GameSpec]:
    return [g for g in REGISTRY.values() if g.live]


def by_generation() -> dict[int, list[GameSpec]]:
    out: dict[int, list[GameSpec]] = {}
    for g in REGISTRY.values():
        out.setdefault(g.generation, []).append(g)
    return out


def sha1_of(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def identify(rom_path) -> GameSpec | None:
    """Which game is this ROM? Answers by hash, so it cannot be fooled by a
    filename."""
    digest = sha1_of(rom_path)
    for spec in REGISTRY.values():
        if digest in spec.rom_sha1:
            return spec
    return None
