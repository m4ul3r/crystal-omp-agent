"""Gen 1 (Kanto): Red, Blue, Yellow, from the pokered decompilation.

Mechanically Gen 1 is close enough to Gen 2 that the data layer is the same
shape -- rgbds symbol table, banked memory, flat party structs, a decodable
tilemap -- so this adapter subclasses the Gen-2 one and changes what is
genuinely different rather than duplicating it.

What Gen 1 does NOT have, declared rather than faked:

* **No held items, no genders, no natures, no abilities.**
* **One Special stat**, not Sp. Atk and Sp. Def, so any damage model that
  assumes the Gen-2 split is wrong here.
* **No Dark or Steel types**, so a type chart read from Gen 2/3 does not
  apply.
* **The badge-boost and crit formulas differ substantially** from later
  generations.

This is `declared`: pokered is not checked out here and no Gen-1 ROM is
present, so nothing below has been exercised. It exists so that adding Kanto
is a build step plus a ROM, not a code project -- which is the point of the
adapter layer.
"""

from .base import Backend
from .gen2 import Gen2Adapter


class Gen1Adapter(Gen2Adapter):
    CAPABILITIES = {
        "banked_memory",
        "decodable_screen",
    }
    #: `flat_party` is deliberately NOT claimed even though Gen 1's party
    #: really is a flat struct: nothing here reads it. A capability describes
    #: what this adapter can DO, not what the cartridge happens to contain.
    #: Absent on purpose: "held_items", "genders", "abilities", "natures",
    #: "elevation", "double_battles", "physical_special_by_type",
    #: "engine_task_symbols", "battle", "special_split", "dark_steel_types".

    def artifacts(self) -> dict:
        root = self.decomp_root()
        return {
            "ROM": self.rom_path(),
            "symbol table": self.sym_path(),
            # pokered keeps its charmap and map constants in the same places
            # as pokecrystal, but the map data layout differs.
            "charmap": root / "constants" / "charmap.asm",
            "map constants": root / "constants" / "map_constants.asm",
        }

    def open(self, state_path=None, fresh=False):
        """Boot Kanto with the layers that are genuinely correct.

        pokered is an rgbds build like pokecrystal, so three layers port
        unchanged and are exercised rather than assumed: the SYMBOL table
        (21,128 entries), the CHARMAP -- `constants/charmap.asm` sits exactly
        where Gen 2 keeps it -- and the emulator, since both are Game Boy
        cartridges PyBoy runs natively.

        What does NOT port is refused rather than faked. pokered's party
        struct carries one Special stat instead of the Sp. Atk / Sp. Def
        split, no held item and no gender byte; its map data uses a different
        header and block format. Read through the Gen-2 readers either would
        parse and every value would be wrong -- the costliest failure class
        this project has -- so `state` and `nav` raise by name instead.
        """
        self.require()
        root = self.decomp_root()

        from ..gen2 import charmap as g2charmap
        from ..gen2 import emu as g2emu
        from ..gen2 import symfile as g2sym

        sym = g2sym.Symbols(self.sym_path())
        cm = g2charmap.Charmap(root / "constants" / "charmap.asm")
        emu = g2emu.Crystal(
            self.rom_path(), sym, cm, None if fresh else state_path
        )

        backend = Backend(
            spec=self.spec,
            emu=emu,
            sym=sym,
            charmap=cm,
            consts=None,
            names=None,          # pokered's ROM data tables are not ported
            state=_Gen1Unported("state"),
            nav=_Gen1Unported("nav"),
            capabilities=set(self.CAPABILITIES),
        )
        backend.extra["decomp_root"] = root
        return backend


class _Gen1Unported:
    """Refuses every read, by name, rather than misreading Gen-2 structs.

    A stub returning zeroes would be worse than no adapter: this project's
    retrospective names "confidently wrong numbers" as costlier than crashes,
    and a Gen-1 party read through Gen-2 offsets is precisely that -- it
    parses, and every stat is wrong.
    """

    __slots__ = ("_layer",)

    _WHY = {
        "state": "party struct (one Special stat, no held item, no gender)",
        "nav": "map header and block format",
    }

    def __init__(self, layer: str):
        self._layer = layer

    def __getattr__(self, name):
        why = self._WHY.get(self._layer, "layout")
        raise NotImplementedError(
            f"Gen-1 {self._layer}.{name} is not ported: pokered's {why} "
            f"differs from pokecrystal's, so the vendored Gen-2 reader would "
            f"return plausible, wrong values. Port "
            f"pokeagent/gen2/{self._layer}.py against pokered's own labels."
        )
