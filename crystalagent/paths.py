"""Default file locations, resolved relative to the pokecrystal checkout."""

import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]   # crystal-agent/

#: The pokecrystal checkout. Defaults to the parent directory, which is where
#: the tool is designed to live, but it is now OVERRIDABLE -- the decompilation
#: is a runtime dependency (charmap, map layouts and constants are parsed on
#: demand), so a checkout anywhere else could not import the package at all.
REPO_ROOT = Path(os.environ.get("CRYSTAL_REPO", TOOL_DIR.parent))

ROM = Path(os.environ.get("CRYSTAL_ROM", REPO_ROOT / "pokecrystal.gbc"))
SYM = Path(os.environ.get("CRYSTAL_SYM", REPO_ROOT / "pokecrystal.sym"))
CHARMAP = REPO_ROOT / "constants" / "charmap.asm"
MAP_CONSTANTS = REPO_ROOT / "constants" / "map_constants.asm"
SAVES_DIR = Path(os.environ.get("CRYSTAL_SAVES", TOOL_DIR / "saves"))
LIVE_DIR = Path(os.environ.get("CRYSTAL_LIVE_DIR", TOOL_DIR / "live"))
DEFAULT_STATE = Path(os.environ.get("CRYSTAL_STATE", SAVES_DIR / "default.state"))
