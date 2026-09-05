"""Metatile behaviour semantics, parsed from ``src/metatile_behavior.c``.

Which tiles are grass, water, ledges or one-way walls is not something to
write down in Python -- the engine answers it from a 256-entry bit table and
a set of small predicate functions, and both are in the decomp:

* ``sTileBitAttributes[]`` (src/metatile_behavior.c:7+) -- bit 0 is "wild
  encounters happen here", bit 1 is "surfable".
* ``MetatileBehavior_IsEastBlocked`` and friends (:1000-1057) -- the
  directional wall sets.
* ``MetatileBehavior_IsJumpSouth`` and friends (:265-296) -- the ledges.

Crystal's equivalent knowledge lived as hand-maintained tile-id sets in
nav.py, and its journal has repeated entries where one of those sets was
wrong or incomplete (ICE missing from ``_enterable`` sealed every ice map,
#56). Parsing removes the category.
"""

import re
from functools import cached_property

from . import paths
from .cconst import Constants

_ATTR_ROW = re.compile(
    r"TILE_ATTRIBUTES\(\s*(TRUE|FALSE)\s*,\s*(TRUE|FALSE)\s*,\s*(TRUE|FALSE)\s*\)"
)
_PREDICATE = re.compile(
    r"bool8\s+MetatileBehavior_(\w+)\s*\([^)]*\)\s*\{(.*?)\n\}", re.S
)
_MB_REF = re.compile(r"\bMB_[A-Z0-9_]+\b")

ATTR_ENCOUNTER = 1
ATTR_SURFABLE = 2


class Behaviors:
    """The engine's own opinion about every metatile behaviour byte."""

    def __init__(self, consts=None):
        self.consts = consts or Constants()
        self.source = paths.require(
            paths.PRET / "src" / "metatile_behavior.c",
            "metatile_behavior.c",
            "is the pret/ submodule checked out?",
        ).read_text(encoding="utf-8", errors="replace")

    @cached_property
    def names(self) -> dict[int, str]:
        return self.consts.inverse("metatile_behaviors.h", "MB_")

    @cached_property
    def ids(self) -> dict[str, int]:
        return {k: v for k, v in self.consts.behaviors.items() if k.startswith("MB_")}

    @cached_property
    def bit_attributes(self) -> list[int]:
        """``sTileBitAttributes`` as a list indexed by behaviour byte."""
        start = self.source.index("sTileBitAttributes")
        end = self.source.index("};", start)
        body = self.source[start:end]
        out = []
        for unused, surfable, encounter in _ATTR_ROW.findall(body):
            out.append(
                (1 if encounter == "TRUE" else 0) | (2 if surfable == "TRUE" else 0)
            )
        if len(out) < 0x100:
            out += [0] * (0x100 - len(out))
        return out

    def _predicate_set(self, func_name) -> frozenset[int]:
        """The MB_* values a ``MetatileBehavior_Is*`` function accepts."""
        for name, body in _PREDICATE.findall(self.source):
            if name != func_name:
                continue
            return frozenset(
                self.ids[m] for m in _MB_REF.findall(body) if m in self.ids
            )
        raise KeyError(f"MetatileBehavior_{func_name} not found in metatile_behavior.c")

    # ---- terrain classification ---------------------------------------

    def is_encounter(self, behavior) -> bool:
        return bool(self.bit_attributes[behavior] & ATTR_ENCOUNTER)

    def is_surfable(self, behavior) -> bool:
        return bool(self.bit_attributes[behavior] & ATTR_SURFABLE)

    def is_land_encounter(self, behavior) -> bool:
        return self.is_encounter(behavior) and not self.is_surfable(behavior)

    def is_water_encounter(self, behavior) -> bool:
        return self.is_encounter(behavior) and self.is_surfable(behavior)

    # ---- directional walls ---------------------------------------------

    @cached_property
    def blocked_sets(self) -> dict[str, frozenset[int]]:
        return {
            d: self._predicate_set(f"Is{d}Blocked")
            for d in ("North", "South", "East", "West")
        }

    @cached_property
    def jump_sets(self) -> dict[str, frozenset[int]]:
        return {
            d: self._predicate_set(f"IsJump{d}")
            for d in ("North", "South", "East", "West")
        }

    @cached_property
    def door_behaviors(self) -> frozenset[int]:
        """Behaviours that a warp actually fires on.

        src/field_control_avatar.c:735-763 -- a warp_event only triggers when
        the tile is also one of these, which is why "standing on a warp"
        is not the same as "the warp will fire" (Crystal gotcha 15 has the
        same shape).
        """
        names = (
            "MB_NON_ANIMATED_DOOR", "MB_LADDER", "MB_ANIMATED_DOOR",
            "MB_EAST_ARROW_WARP", "MB_WEST_ARROW_WARP",
            "MB_NORTH_ARROW_WARP", "MB_SOUTH_ARROW_WARP",
            "MB_CRACKED_FLOOR_HOLE", "MB_AQUA_HIDEOUT_WARP",
            "MB_LAVARIDGE_GYM_B1F_WARP", "MB_LAVARIDGE_GYM_1F_WARP",
            "MB_MT_PYRE_HOLE", "MB_ESCALATOR_UP", "MB_ESCALATOR_DOWN",
            "MB_WATER_DOOR", "MB_DEEP_SOUTH_WARP", "MB_STAIRS_OUTSIDE_ABANDONED_SHIP",
            "MB_SHOAL_CAVE_ENTRANCE", "MB_UNION_ROOM_WARP",
        )
        return frozenset(self.ids[n] for n in names if n in self.ids)

    @cached_property
    def forced_movement(self) -> frozenset[int]:
        """Ice, conveyors, currents and slopes -- tiles where a step does not
        end where you aimed it. A planner must either model or avoid them."""
        prefixes = (
            "MB_ICE", "MB_THIN_ICE", "MB_CRACKED_ICE",
            "MB_WALK_", "MB_SLIDE_", "MB_WATER_CURRENT_",
            "MB_MUDDY_SLOPE", "MB_BUMPY_SLOPE", "MB_ROCK_STAIRS",
        )
        return frozenset(
            v for k, v in self.ids.items() if any(k.startswith(p) for p in prefixes)
        )

    def kind(self, behavior, collision, elevation) -> str:
        """One word for what a decoded cell is, for map rendering and for
        `find_tiles(kind)`. Semantics first, then passability."""
        if behavior in self.jump_sets["South"] | self.jump_sets["North"] \
                | self.jump_sets["East"] | self.jump_sets["West"]:
            return "ledge"
        if behavior in self.door_behaviors:
            return "warp"
        if self.is_water_encounter(behavior) or self.is_surfable(behavior):
            return "water"
        if self.is_land_encounter(behavior):
            return "grass"
        if behavior in self.forced_movement:
            return "forced"
        if collision:
            return "blocked"
        return "floor"
