"""Default file locations, resolved relative to the pokecrystal checkout.

The checkout is a RUNTIME dependency (charmap, maps, constants are parsed on
demand). It is found, in order: ``CRYSTAL_REPO``; the parent directory (the
original layout, harness nested inside pokecrystal); ``decomp/pokecrystal``
(what ``scripts/build_gen2.sh crystal`` clones and builds).
"""

import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]   # crystal-agent/


def _repo_root():
    override = os.environ.get("CRYSTAL_REPO")
    if override:
        return Path(override)
    for cand in (TOOL_DIR.parent, TOOL_DIR / "decomp" / "pokecrystal"):
        if (cand / "constants" / "charmap.asm").exists():
            return cand
    return TOOL_DIR.parent


REPO_ROOT = _repo_root()                         # pokecrystal/

ROM = Path(os.environ.get("CRYSTAL_ROM", REPO_ROOT / "pokecrystal.gbc"))
SYM = Path(os.environ.get("CRYSTAL_SYM", REPO_ROOT / "pokecrystal.sym"))
CHARMAP = REPO_ROOT / "constants" / "charmap.asm"
MAP_CONSTANTS = REPO_ROOT / "constants" / "map_constants.asm"
SAVES_DIR = Path(os.environ.get("CRYSTAL_SAVES", TOOL_DIR / "saves"))
LIVE_DIR = Path(os.environ.get("CRYSTAL_LIVE_DIR", TOOL_DIR / "live"))
DEFAULT_STATE = Path(os.environ.get("CRYSTAL_STATE", SAVES_DIR / "default.state"))
