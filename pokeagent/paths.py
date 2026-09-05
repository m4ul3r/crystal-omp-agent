"""Default file locations.

Unlike the Crystal harness -- which derived everything from
``Path(__file__).parents[2]`` and therefore silently broke the moment the
tool was checked out anywhere but *inside* a pokecrystal clone -- every
location here is an explicit, overridable variable.  The decompilation is a
RUNTIME dependency (charmap, map layouts, constants are parsed on demand),
not merely a build-time one, so it gets a first-class env var.
"""

import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]      # sapphire-omarchy-widget/

#: pret/pokeruby checkout.  Ships as a git submodule at ``pret/``.
PRET = Path(os.environ.get("SAPPHIRE_PRET", TOOL_DIR / "pret"))

#: The Sapphire (Rev 2, US) ROM.  sha1 89b45fb172e6b55d51fc0e61989775187f6fe63c
ROM = Path(os.environ.get("SAPPHIRE_ROM", TOOL_DIR / "pokesapphire.gba"))

#: Symbol table emitted by `make ... syms` in the decomp.
#: This is the analog of pokecrystal.sym. Built by scripts/build_rom.sh.
SYM = Path(os.environ.get("SAPPHIRE_SYM", TOOL_DIR / "pokesapphire_rev2.sym"))

CHARMAP = PRET / "charmap.txt"
INCLUDE = PRET / "include"
CONSTANTS = INCLUDE / "constants"
DATA = PRET / "data"
MAPS = DATA / "maps"
LAYOUTS = DATA / "layouts"
TILESETS = DATA / "tilesets"

SAVES_DIR = Path(os.environ.get("SAPPHIRE_SAVES", TOOL_DIR / "saves"))
LIVE_DIR = Path(os.environ.get("SAPPHIRE_LIVE_DIR", TOOL_DIR / "live"))
DEFAULT_STATE = Path(os.environ.get("SAPPHIRE_STATE", SAVES_DIR / "default.state"))

#: Where non-Gen-3 decompilations live, one directory per pret repo.
#: scripts/build_gen2.sh clones and builds into here.
DECOMP_DIR = Path(os.environ.get("POKE_DECOMP_DIR", TOOL_DIR / "decomp"))


def rom_override(game_id):
    """Per-game ROM path override, e.g. POKE_ROM_CRYSTAL=/path/to.gbc.

    Lets a user point at their own dump for any registered game without
    touching the tree.
    """
    value = os.environ.get(f"POKE_ROM_{game_id.upper()}")
    return Path(value) if value else None


#: Vendored libmgba, used only when the system package is absent.  The
#: launcher puts this on LD_LIBRARY_PATH before importing mgba.
VENDOR_LIB = TOOL_DIR / "vendor" / "lib"


def require(path, what, hint):
    """Fail loudly at the boundary (DESIGN rule 6) instead of half-working."""
    if not Path(path).exists():
        raise FileNotFoundError(f"{what} not found at {path}\n  {hint}")
    return Path(path)
