"""Gen 3 (Hoenn): Sapphire and Ruby, from the pokeruby decompilation.

This is the generation the harness was built against, so the adapter is thin --
it wires up the modules that already exist. What it adds is the ability to do
that for *either* Hoenn game from one code path, because Ruby and Sapphire
share a decompilation and a memory layout and differ only in the ROM.
"""

from .. import paths
from .base import Backend, GameAdapter


class Gen3Adapter(GameAdapter):
    CAPABILITIES = {
        "encrypted_party",     # substructures are XOR'd and shuffled
        "elevation",           # tiles carry a 4-bit z
        "abilities",
        "held_items",
        "natures",
        "double_battles",
        "physical_special_by_type",
        "engine_task_symbols",  # gTasks/callback2 name the current screen
        "flat_address_space",
    }

    def rom_path(self):
        """The user's dump. Falls back to the built ROM when it is the same
        game -- useful for Ruby, where the build is the only copy present."""
        if paths.ROM.exists():
            return paths.ROM
        return paths.PRET / self.spec.rom

    def sym_path(self):
        built = paths.PRET / self.spec.sym
        return built if built.exists() else paths.TOOL_DIR / self.spec.sym

    def artifacts(self) -> dict:
        return {
            "ROM": self.rom_path(),
            "symbol table": self.sym_path(),
            "charmap": paths.CHARMAP,
            "map index": paths.MAPS / "map_groups.json",
            "layouts": paths.LAYOUTS / "layouts.json",
        }

    def open(self, state_path=None, fresh=False) -> Backend:
        self.require()

        from ..behaviors import Behaviors
        from ..cconst import Constants
        from ..charmap import Charmap
        from ..emu import Sapphire
        from ..names import Names
        from ..nav import MapData
        from ..state import GameState
        from ..symbols import Symbols

        sym = Symbols(self.sym_path())
        charmap = Charmap(paths.CHARMAP)
        consts = Constants()
        emu = Sapphire(
            rom=self.rom_path(),
            sym=sym,
            charmap=charmap,
            state_path=None if fresh else state_path,
        )
        names = Names(emu, charmap, consts)
        state = GameState(emu, names, consts)
        behaviors = Behaviors(consts)
        nav = MapData(behaviors)

        backend = Backend(
            spec=self.spec,
            emu=emu,
            sym=sym,
            charmap=charmap,
            consts=consts,
            names=names,
            state=state,
            nav=nav,
            capabilities=set(self.CAPABILITIES),
        )
        backend.extra["behaviors"] = behaviors
        return backend
