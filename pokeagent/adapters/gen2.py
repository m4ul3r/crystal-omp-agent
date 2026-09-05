"""Gen 2 (Johto): Crystal, Gold, Silver, from the pokecrystal family.

Everything the Game Boy games do differently from the GBA lives behind this
adapter, so nothing above it branches on generation:

* **PyBoy, not mGBA.** Different core, different savestate format.
* **Banked memory.** A WRAM symbol carries a bank, and reading through the
  currently-mapped bank silently returns another bank's bytes. The vendored
  `gen2.emu` handles this; the GBA side has no such concept.
* **Flat party structs.** No substructure encryption, so reading a species is
  a struct field rather than the XOR-and-unshuffle dance Gen 3 needs.
* **A decodable screen.** The 20x18 tilemap maps losslessly back to text, so
  Gen 2 really can read dialog off the screen -- which is why the Crystal
  harness did it that way, and why Gen 3 cannot.
* **No elevation, no abilities, no natures, no physical/special split by
  type.** Those are advertised as absent rather than faked.

The data layer here is the Crystal harness's own, vendored (see
`pokeagent/gen2/__init__.py`). What is NOT carried over end-to-end is the
Gen-2 battle and menu driving, so `capabilities` omits `battle` and the
registry status stays honest.
"""

from pathlib import Path

from .. import paths
from .base import Backend, GameAdapter


class Gen2Adapter(GameAdapter):
    CAPABILITIES = {
        "flat_party",          # no substructure crypto
        "banked_memory",
        "decodable_screen",    # the tilemap really is text
        "held_items",
        "genders",
    }
    #: Deliberately absent, and checked by callers rather than assumed:
    #: "elevation", "abilities", "natures", "double_battles",
    #: "physical_special_by_type", "engine_task_symbols", "battle".

    def decomp_root(self) -> Path:
        """The pokecrystal checkout. `scripts/build_gen2.sh` puts it here."""
        return Path(paths.DECOMP_DIR / self.spec.decomp)

    def rom_path(self) -> Path:
        env = paths.rom_override(self.spec.id)
        if env:
            return env
        return self.decomp_root() / self.spec.rom

    def sym_path(self) -> Path:
        return self.decomp_root() / self.spec.sym

    def artifacts(self) -> dict:
        root = self.decomp_root()
        return {
            "ROM": self.rom_path(),
            "symbol table": self.sym_path(),
            "charmap": root / "constants" / "charmap.asm",
            "map constants": root / "constants" / "map_constants.asm",
            "battle constants": root / "constants" / "battle_constants.asm",
            "map data": root / "data" / "maps" / "maps.asm",
        }

    def open(self, state_path=None, fresh=False) -> Backend:
        self.require()
        root = self.decomp_root()

        from ..gen2 import charmap as g2charmap
        from ..gen2 import emu as g2emu
        from ..gen2 import names as g2names
        from ..gen2 import nav as g2nav
        from ..gen2 import state as g2state
        from ..gen2 import symfile as g2sym

        # The vendored reader parses battle_constants.asm lazily; point it at
        # this checkout before anything reads a status byte.
        g2state.DECOMP_ROOT = root

        sym = g2sym.Symbols(self.sym_path())
        cm = g2charmap.Charmap(root / "constants" / "charmap.asm")
        emu = g2emu.Crystal(
            self.rom_path(), sym, cm, None if fresh else state_path
        )
        names = g2names.Names(
            self.rom_path(), sym, cm, root / "constants" / "map_constants.asm"
        )
        nav = g2nav.MapData(root)

        backend = Backend(
            spec=self.spec,
            emu=emu,
            sym=sym,
            charmap=cm,
            consts=None,          # Gen 2 constants are read per-file on demand
            names=names,
            state=_Gen2State(emu, names),
            nav=nav,
            capabilities=set(self.CAPABILITIES),
        )
        backend.extra["decomp_root"] = root
        return backend


class _Gen2State:
    """Adapts the Gen-2 `game_state()` function to the reader shape the rest
    of the harness expects, without pretending to offer Gen-3-only reads."""

    def __init__(self, emu, names):
        self.emu = emu
        self.names = names

    def snapshot(self, include_party=True):
        from ..gen2.state import game_state

        return game_state(self.emu, self.names)

    def status_line(self):
        from ..gen2.state import status_line

        return status_line(self.snapshot())

    def location(self):
        snap = self.snapshot()
        loc = snap.get("location", {})
        return type(
            "Loc", (), {
                "map_name": loc.get("map", "?"),
                "x": loc.get("x", 0),
                "y": loc.get("y", 0),
                "map_group": loc.get("group", 0),
                "map_num": loc.get("num", 0),
            }
        )()

    def party(self):
        return self.snapshot().get("party", [])
