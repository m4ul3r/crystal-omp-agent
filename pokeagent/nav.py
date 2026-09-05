"""Walkable maps and pathfinding: the analog of ``crystalagent/nav.py``.

Ground truth is the decompilation, never runtime discovery:

* ``data/maps/map_groups.json``       -- the 394 maps and their (group, num)
* ``data/maps/<Map>/map.json``        -- warps, NPCs, triggers, connections
* ``data/layouts/layouts.json``       -- geometry and which tilesets apply
* ``data/layouts/<Layout>/map.bin``   -- one u16 per tile: bits 0-9 metatile,
                                         10-11 collision, 12-15 elevation
                                         (include/global.fieldmap.h:1-18)
* ``data/tilesets/*/metatile_attributes.bin`` -- metatile -> behaviour byte

Gen 3 adds a dimension Crystal never had: **elevation**. A tile carries a
4-bit z, the player carries their own, and a step is refused when they
disagree unless either is the wildcard 0 or the bridge value 15
(src/event_object_movement.c:7528-7545). Bridges and the layered routes
around Fortree are unwalkable nonsense without it, so BFS state is
``(x, y, z)``.

Ledges are one-way and move **two** tiles, not one
(src/event_object_movement.c:5316-5319), so a ledge edge lands at
``dest + delta`` and never has a reverse.
"""

import json
import re
import time as _time
from collections import deque
from dataclasses import dataclass, field
from functools import cached_property

from . import paths
from .behaviors import Behaviors
from .cconst import Constants

#: include/global.fieldmap.h:1-18
METATILE_ID_MASK = 0x03FF
COLLISION_MASK = 0x0C00
COLLISION_SHIFT = 10
ELEVATION_MASK = 0xF000
ELEVATION_SHIFT = 12
#: include/fieldmap.h:7 -- ids below this index the primary tileset.
METATILES_IN_PRIMARY = 512
#: include/global.fieldmap.h:20-24 -- behaviour is the low byte of the u16.
METATILE_ATTR_BEHAVIOR_MASK = 0x00FF
#: Elevations that match anything: 0 is the wildcard, 15 the bridge value.
ELEVATION_ANY = (0, 15)
#: include/fieldmap.h:19 -- the runtime grid's border pad. Not applied to
#: gSaveBlock1.pos or to map.json coordinates; both are unpadded.
MAP_OFFSET = 7

DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
OPPOSITE = {"U": "D", "D": "U", "L": "R", "R": "L"}
#: src/event_object_movement.c:782-795 -- a directional wall blocks from both
#: sides, so leaving and entering consult different predicate sets.
_INCOMING = {"U": "North", "D": "South", "L": "West", "R": "East"}
_OUTGOING = {"U": "South", "D": "North", "L": "East", "R": "West"}
_JUMP = {"U": "North", "D": "South", "L": "West", "R": "East"}

_CONNECTION_DIR = {"up": "U", "down": "D", "left": "L", "right": "R"}
#: Water you can DIVE from (MetatileBehavior_IsDiveable,
#: src/metatile_behavior.c:927-935).
#: How many cells of a border get priced before crossing it. Each one costs
#: a reachability BFS on the far side; see `_crossings`.
_RANK_SAMPLE = 6

DIVEABLE = frozenset((0x11, 0x12, 0x14))     # SEMI_DEEP, UNUSED_DEEP, SOOTOPOLIS_DEEP
#: Underwater ceiling you may NOT surface through
#: (MetatileBehavior_IsNotSurfacable, :937-944).
NO_SURFACING = frozenset((0x19, 0x2A))       # NO_SURFACING, SEAWEED_NO_SURFACING
#: MB_WATERFALL (:1062-1068).
WATERFALL = 0x13
#: MB_MUDDY_SLOPE (metatile_behaviors.h:212). A slope is climbed NORTH and
#: slid SOUTH, and nothing else: `ForcedMovement_MuddySlope`
#: (field_player_avatar.c:494-506) pre-empts keypad input whenever the player
#: stands on one and forces DIR_SOUTH unless
#: `movementDirection == DIR_NORTH && GetPlayerSpeed() > 3`.
#:
#: Only ONE state in the game satisfies that: the MACH bike at full
#: acceleration (`bikeFrameCounter == 2` -> SPEED_FASTEST = 4, bike.c:121,
#: :1044-1059). The ACRO bike returns SPEED_FASTER = 3 and `3 > 3` is false,
#: so it slides back exactly like walking -- worth stating because "get a
#: bike" is the obvious fix and half of the obvious fix is wrong.
MUDDY_SLOPE = 0xD0
_INCBIN = re.compile(
    r"^gMetatileAttributes_(\w+)::[^\n]*\n\s*\.incbin\s+\"([^\"]+)\"", re.M
)


@dataclass(slots=True)
class Cell:
    metatile: int
    collision: int
    elevation: int
    behavior: int
    kind: str

    @property
    def passable(self):
        return self.collision == 0


@dataclass(slots=True)
class Warp:
    x: int
    y: int
    elevation: int
    dest_map: str
    dest_warp_id: int


@dataclass(slots=True)
class MapInfo:
    name: str
    const: str
    group: int
    num: int
    layout: str
    width: int
    height: int
    warps: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    triggers: list = field(default_factory=list)
    signs: list = field(default_factory=list)
    connections: list = field(default_factory=list)


class MapData:
    """Every map, decoded on demand and cached."""

    #: CLASS-level defaults. Test fakes and any other object built with
    #: `__new__` skip __init__ entirely, and every attribute added to the
    #: movement model since has broken them one at a time -- the same
    #: fragility that let a mismatched fake hide a real bug. Defaults here
    #: mean a bare instance answers "no field moves, empty caches" instead of
    #: raising AttributeError deep inside a BFS.
    surfing = False
    waterfall = False
    #: Mach bike, accelerated to SPEED_FASTEST. The ONLY state that climbs a
    #: muddy slope; see MUDDY_SLOPE. False until the run owns the bike AND the
    #: driver can guarantee the run-up, because a slope entered below full
    #: speed slides back and wipes the acceleration
    #: (Bike_UpdateBikeCounterSpeed(0), bike.c:1032-1036).
    mach_bike = False
    #: Why the last route_legs returned None, when it was not "no such road".
    last_route_reason = ""
    #: Seconds any single route search may spend. Sized so the play loop's
    #: cycle stays responsive: the loop must keep saving, keep its watchdog
    #: fed, and keep answering "where am I" while a search is running.
    plan_budget_s = 20.0
    #: Runtime refusals per map, `{map: {(x, y), ...}}` -- an NPC in a doorway,
    #: a coord_event whose guards still hold. Instances build their own in
    #: __init__; this default exists so a bare `__new__` instance answers
    #: `step()` instead of raising AttributeError from inside a BFS, which is
    #: what the note above promises and what `blocked` was missing.
    blocked: dict = {}

    def __init__(self, behaviors=None):
        self.beh = behaviors or Behaviors()
        self._grids: dict[str, list[list[Cell]]] = {}
        self._infos: dict[str, MapInfo] = {}
        self._reach_cache: dict[tuple, set] = {}
        # `exits` is a pure function of SHIPPED map data -- warps and
        # connections, plus each warp's landing cell. Computing a
        # landing decodes the destination map's grid, so an uncached
        # `exits` decodes one grid per warp, and `route_legs` asks
        # every map in the graph: hundreds of full decodes per journey.
        # That is the wedge the stall watchdog caught in pure Python
        # with the frame counter flat -- nav.exits <- usable_exits <-
        # route_legs, 150s without a single emulated frame, and the
        # same call this repo's journal has blamed three times.
        self._exits_cache: dict[str, list[dict]] = {}
        #: When True, surfable water counts as passable in step() and
        #: everything built on it (find_path, reachable, route_legs). The
        #: driver flips it exactly while the party can actually surf, and the
        #: reach cache is keyed on it.
        self.surfing = False
        #: dive_gates() results per (map, direction). Static map data, and
        #: scanning for it per BFS node cost the run half an hour.
        self._dive_cache: dict[tuple, list] = {}
        #: _landing_room() per landing cell and movement mode. Ranking a
        #: border re-asked the same question forty times per node.
        self._room_cache: dict[tuple, int] = {}
        #: When True, a NORTHWARD step onto an MB_WATERFALL tile is legal --
        #: the engine climbs it (GetInteractedWaterScript:503-517 requires
        #: badge 8 and IsPlayerSurfingNorth). Without this a waterfall is a
        #: wall and Ever Grande has no road.
        self.waterfall = False
        #: map name -> {(x, y): Cell} read from the running game, consulted
        #: ahead of the shipped .blk data.
        self._live: dict[str, dict] = {}
        #: Cells a caller marked impassable at runtime (an NPC in a doorway).
        #: Crystal's journal has three separate sessions lost to a STALE entry
        #: here (#11, #66), so these are per-map and explicitly clearable.
        self.blocked: dict[str, set] = {}

    # ---- the map index -------------------------------------------------

    @cached_property
    def _groups(self):
        return json.loads((paths.MAPS / "map_groups.json").read_text())

    @cached_property
    def index(self) -> dict[str, tuple[int, int]]:
        out = {}
        for gi, group in enumerate(self._groups["group_order"]):
            for ni, name in enumerate(self._groups[group]):
                out[name] = (gi, ni)
        return out

    @cached_property
    def by_number(self) -> dict[tuple[int, int], str]:
        return {v: k for k, v in self.index.items()}

    @cached_property
    def _layouts(self) -> dict[str, dict]:
        raw = json.loads((paths.LAYOUTS / "layouts.json").read_text())
        return {l["id"]: l for l in raw["layouts"] if l and l.get("id")}

    @cached_property
    def _attribute_files(self) -> dict[str, str]:
        """``gTileset_General`` -> its metatile_attributes.bin path."""
        text = (paths.TILESETS / "metatiles.inc").read_text()
        return {f"gTileset_{n}": p for n, p in _INCBIN.findall(text)}

    def _attributes(self, tileset) -> bytes:
        path = self._attribute_files.get(tileset or "")
        return (paths.PRET / path).read_bytes() if path else b""

    # ---- one map --------------------------------------------------------

    def info(self, map_name) -> MapInfo:
        if map_name in self._infos:
            return self._infos[map_name]
        path = paths.MAPS / map_name / "map.json"
        if not path.exists():
            raise KeyError(f"no map named {map_name!r} under data/maps/")
        j = json.loads(path.read_text())
        lay = self._layouts[j["layout"]]
        group, num = self.index.get(map_name, (-1, -1))
        info = MapInfo(
            name=map_name,
            const=j["id"],
            group=group,
            num=num,
            layout=j["layout"],
            width=lay["width"],
            height=lay["height"],
            warps=[
                Warp(
                    w["x"], w["y"], w["elevation"], w["dest_map"],
                    self._warp_id(w["dest_warp_id"]),
                )
                for w in (j.get("warp_events") or [])
            ],
            objects=list(j.get("object_events") or []),
            triggers=list(j.get("coord_events") or []),
            signs=list(j.get("bg_events") or []),
            connections=list(j.get("connections") or []),
        )
        self._infos[map_name] = info
        return info

    @cached_property
    def _map_consts(self):
        return Constants().ns("maps.h")

    def _warp_id(self, raw):
        """`dest_warp_id` is usually a number but may be a symbolic constant:
        WARP_ID_DYNAMIC (elevators, secret bases), WARP_ID_SECRET_BASE,
        WARP_ID_NONE (include/constants/maps.h:20-25)."""
        try:
            return int(raw)
        except (TypeError, ValueError):
            return self._map_consts.get(str(raw), -1)

    def grid(self, map_name) -> list[list[Cell]]:
        """``grid[y][x]``, in unpadded map coordinates -- the same space
        ``gSaveBlock1.pos`` and the map JSON events use."""
        if map_name in self._grids:
            return self._grids[map_name]
        info = self.info(map_name)
        lay = self._layouts[info.layout]
        blocks = (paths.PRET / lay["blockdata_filepath"]).read_bytes()
        primary = self._attributes(lay.get("primary_tileset"))
        secondary = self._attributes(lay.get("secondary_tileset"))

        def behavior_of(mid):
            # src/fieldmap.c:387-404
            if mid < METATILES_IN_PRIMARY:
                src, off = primary, mid * 2
            else:
                src, off = secondary, (mid - METATILES_IN_PRIMARY) * 2
            if off + 1 >= len(src):
                return 0xFF  # MB_INVALID
            return src[off] & METATILE_ATTR_BEHAVIOR_MASK

        want = info.width * info.height * 2
        if len(blocks) < want:
            raise ValueError(
                f"{info.layout}: blockdata is {len(blocks)} bytes but "
                f"{info.width}x{info.height} needs {want}"
            )
        rows = []
        for y in range(info.height):
            row = []
            for x in range(info.width):
                i = (y * info.width + x) * 2
                block = blocks[i] | (blocks[i + 1] << 8)
                mid = block & METATILE_ID_MASK
                coll = (block & COLLISION_MASK) >> COLLISION_SHIFT
                elev = (block & ELEVATION_MASK) >> ELEVATION_SHIFT
                beh = behavior_of(mid)
                row.append(Cell(mid, coll, elev, beh, self.beh.kind(beh, coll, elev)))
            rows.append(row)
        self._grids[map_name] = rows
        return rows

    def cell(self, map_name, x, y) -> Cell | None:
        live = self._live.get(map_name)
        if live is not None:
            hit = live.get((x, y))
            if hit is not None:
                return hit
        g = self.grid(map_name)
        if 0 <= y < len(g) and 0 <= x < len(g[0]):
            return g[y][x]
        return None

    # ---- live overrides ---------------------------------------------------
    #
    # The .blk files are the map as SHIPPED. Switch puzzles rewrite it at
    # runtime: Mauville's gym raises and lowers electric barriers, and with
    # only the static grid the leader sits in a component the pathfinder can
    # see and cannot enter. Crystal solved this by reading the live block map
    # out of WRAM; this is the Gen-3 half of the same idea.

    def cell_from_entry(self, map_name, entry: int) -> Cell:
        """Decode one live `gBackupMapLayout` word into a Cell.

        Bit layout from global.fieldmap.h:7-9 -- metatile 0-9, collision
        10-11, elevation 12-15. Behaviour comes from the tileset attributes
        the static decode already loaded, so a live cell and a shipped one are
        classified by exactly the same rule.
        """
        self.grid(map_name)          # ensure the tileset attributes are loaded
        mid = entry & 0x03FF
        coll = (entry & 0x0C00) >> 10
        elev = (entry & 0xF000) >> 12
        beh = self._behaviour_of(map_name, mid)
        return Cell(mid, coll, elev, beh, self.beh.kind(beh, coll, elev))

    def _behaviour_of(self, map_name, mid: int) -> int:
        info = self.info(map_name)
        lay = self._layouts[info.layout]
        primary = self._attributes(lay.get("primary_tileset"))
        secondary = self._attributes(lay.get("secondary_tileset"))
        if mid < METATILES_IN_PRIMARY:
            src, off = primary, mid * 2
        else:
            src, off = secondary, (mid - METATILES_IN_PRIMARY) * 2
        if off + 1 >= len(src):
            return 0xFF
        return src[off] & METATILE_ATTR_BEHAVIOR_MASK

    def set_live_cells(self, map_name, cells: dict) -> int:
        """Override decoded cells for one map. Returns how many changed.

        Clears the reachability memo, because a barrier that just opened
        changes exactly the answer that cache is holding.
        """
        book = self._live.setdefault(map_name, {})
        changed = sum(1 for k, v in cells.items() if book.get(k) != v)
        book.update(cells)
        if changed:
            self._reach_cache.clear()
        return changed

    def mark_blocked(self, map_name, cells, replace=False) -> None:
        """Set or extend the blocked set for a map, invalidating reachability.

        `blocked` was a plain dict mutated from outside, and nothing told the
        reachability cache. So the driver marked Route 111's ten shut desert
        gates, asked whether Route 113 was reachable, and got an answer
        computed BEFORE the marking -- the desert still open. The router
        planned straight through it every single time, the walker refused,
        and travel oscillated Mauville -> Route 111 -> Mauville until its leg
        budget ran out. A blocked set that does not invalidate the cache is
        not a blocked set.
        """
        book = self.blocked.setdefault(map_name, set())
        before = len(book)
        if replace:
            book.clear()
        book.update(cells)
        if replace or len(book) != before:
            self._reach_cache.clear()

    def clear_live_cells(self, map_name=None) -> None:
        if map_name is None:
            self._live.clear()
        else:
            self._live.pop(map_name, None)
        self._reach_cache.clear()

    def find_tiles(self, map_name, kind) -> list[tuple[int, int]]:
        """Absolute coordinates of every cell of a kind.

        This -- not an ASCII picture -- is the decision interface. Crystal's
        gotcha 11 is a session lost to miscounting characters in rendered map
        art, three times in one run.
        """
        return [
            (x, y)
            for y, row in enumerate(self.grid(map_name))
            for x, c in enumerate(row)
            if c.kind == kind
        ]

    def exits(self, map_name) -> list[dict]:
        """Every way off this map -- warps and edge connections -- with where
        each one lands.

        Memoised: nothing here depends on live state. Warps, connections and
        landing cells all come from the shipped map data, so the answer for a
        given map cannot change during a run.
        """
        hit = self._exits_cache.get(map_name)
        if hit is not None:
            return hit
        info = self.info(map_name)
        out = []
        for i, w in enumerate(info.warps):
            landing = self.warp_landing(w)
            out.append(
                {
                    "kind": "warp",
                    "id": i,
                    "x": w.x,
                    "y": w.y,
                    "dest": self.const_to_name(w.dest_map),
                    "dest_warp": w.dest_warp_id,
                    "lands_at": landing[1:] if landing else None,
                }
            )
        for conn in info.connections:
            d = _CONNECTION_DIR.get(conn["direction"])
            if conn["direction"] in ("dive", "emerge"):
                # A dive is a vertical seam. `SetDiveWarp`
                # (src/overworld.c:583-600) calls
                # Overworld_SetWarpDestination(..., -1, x, y): you arrive at
                # the SAME coordinates on the connected map. Dropping these
                # as "not a walkable seam" meant routing could never plan a
                # dive at all -- and Sootopolis, the Seafloor Cavern and every
                # underwater dex entry are behind one.
                out.append({
                    "kind": "dive",
                    "direction": conn["direction"],
                    "dest": self.const_to_name(conn["map"]),
                    "x": None, "y": None,
                })
                continue
            if d is None:
                continue
            out.append(
                {
                    "kind": "connection",
                    "direction": d,
                    "offset": conn["offset"],
                    "dest": self.const_to_name(conn["map"]),
                }
            )
        self._exits_cache[map_name] = out
        return out

    @cached_property
    def _const_index(self) -> dict[str, str]:
        out = {}
        for name in self.index:
            try:
                out[self.info(name).const] = name
            except (KeyError, OSError):
                continue
        return out

    def const_to_name(self, const):
        return self._const_index.get(const, const)

    # ---- movement rules --------------------------------------------------

    def _dir_blocked(self, here: Cell, there: Cell, d) -> bool:
        return (
            here.behavior in self.beh.blocked_sets[_OUTGOING[d]]
            or there.behavior in self.beh.blocked_sets[_INCOMING[d]]
        )

    def _is_ledge(self, there: Cell, d) -> bool:
        return there.behavior in self.beh.jump_sets[_JUMP[d]]

    #: The level a shoreline sits at. Mounting SURF crosses from here to the
    #: water's own level; the sea is 1 and its shores are 3, verified when the
    #: Route 117 pond stranded a probe with 46 cells in both modes.
    SHORE_ELEVATION = 3

    def _surf_seam(self, here, there, z) -> bool:
        """May a surfer cross this land/water elevation seam?

        The permissive version of this -- any land level to any water level --
        was mine, and it planned a river crossing off a CLIFF. Standing at
        (21,46) on Route 119's elevation-4 plateau with the channel three
        levels below at elevation 1, the planner offered `U` into the water as
        a shortcut to the Weather Institute; the walk then failed forty times
        with "could not mount SURF facing U", because the engine will not put
        you on water you cannot reach. The run sat at the same cell for half an
        hour.

        Mounting is therefore only legal FROM a shoreline, which is what the
        level-3 shore is. Dismounting stays unrestricted: a surfer takes the
        tile's own elevation (`ObjectEventUpdateZCoord`), so refusing that is
        what stranded the pond probe.
        """
        if not self.surfing:
            return False
        if self._is_water(here):
            return True                      # dismounting, or water to water
        # Mounting: only from the shore, never off a cliff.
        return self._is_water(there) and (
            z == self.SHORE_ELEVATION or z in ELEVATION_ANY
        )

    def step(self, map_name, x, y, z, d):
        """One step. Returns ``(nx, ny, nz)`` or None, mirroring
        ``GetCollisionAtCoords`` plus the player's ledge override
        (src/field_player_avatar.c:592-612)."""
        dx, dy = DIRS[d]
        here = self.cell(map_name, x, y)
        there = self.cell(map_name, x + dx, y + dy)
        if here is None or there is None:
            return None
        if (x + dx, y + dy) in self.blocked.get(map_name, ()):
            return None

        # A MUDDY SLOPE is a one-way ride DOWN unless you are on the Mach bike
        # at full throttle, which this party has never owned. The engine does
        # not "block" it -- it accepts the step and then forces the player one
        # tile SOUTH, which is worse than a wall for a planner: `find_path`
        # took the two-tile shortcut up (6,54)/(6,55), the walk slid straight
        # back to (6,56), goto replanned onto the identical path, and the run
        # spent forty replans and half an hour oscillating at the foot of a
        # slope. The map was never severed -- the decoded grid reaches the
        # Weather Institute door either way (1847 cells with these two tiles,
        # 1845 without; the delta IS the two tiles) -- so refusing the climb
        # costs nothing but the shortcut and makes the walker route the
        # plateau the way a player does.
        if there.behavior == MUDDY_SLOPE and d != "D" \
                and not self.mach_bike:
            return None
        # Standing on one, every direction but south is taken from you.
        if here.behavior == MUDDY_SLOPE and d != "D" and not self.mach_bike:
            return None

        # Ledges are checked AFTER collision and override it: a ledge tile is
        # flagged impassable, but a jump in the matching direction is legal.
        if self._is_ledge(there, d):
            land = self.cell(map_name, x + 2 * dx, y + 2 * dy)
            if land is None or not land.passable:
                return None
            return (x + 2 * dx, y + 2 * dy, self._next_z(z, land.elevation))

        # A WATERFALL IS ONE-WAY WITHOUT THE HM, NOT A WALL.
        #
        # `MetatileBehavior_IsWaterfall` sits in the forced-movement test table
        # at index 14, and the matching entry in `sForcedMovementFuncs` is
        # `ForcedMovement_RideCurrentSouth` (field_player_avatar.c:139, :159).
        # So stepping onto a waterfall while surfing carries you DOWN for
        # free -- the HM is only needed to climb.
        #
        # Modelling it as a wall in both directions severed Hoenn's rivers:
        # the run crossed north over Route 119, then could not get back south
        # to Route 118 for the GOOD ROD and reported "could not cross the D
        # seam to Route118" five times in a row. The descent is the road the
        # game gives you.
        #
        # The ride carries further than one tile, which is fine: `goto` walks
        # in chunks and re-plans from the position it actually reaches, the
        # same way it copes with ice and with the muddy slope.
        if not there.passable and self.surfing and d == "D" \
                and there.behavior == WATERFALL:
            return (x + dx, y + dy, self._next_z(z, there.elevation,
                                                 here.elevation))
        if not there.passable and self.waterfall and d == "U" \
                and there.behavior == WATERFALL:
            # Climbing is a step the walker may plan; the DRIVER performs it
            # with the A-press ritual (walk() intercepts it).
            return (x + dx, y + dy, self._next_z(z, there.elevation,
                                                 here.elevation))
        if not there.passable:
            # Water is a wall on foot and a road on a Pokemon's back -- but
            # BLOCKED water is still a wall. `passable` IS `collision == 0`
            # (Cell.passable), so this branch only ever runs for cells the
            # collision bits refuse, and letting a surfer through them let the
            # planner swim into rock.
            #
            # Measured on Route 122: standing on water at (8,10), the planned
            # 44-step route to Mt Pyre's door opened with D into (8,11), which
            # is water with collision=1. The engine refused every time --
            # `step_dir('D')` returned False without moving -- so `goto` logged
            # "stalled 12x at (8, 10)" and the badge-7 chain reported the door
            # unreachable while sitting 44 steps from it. `GetCollisionAtCoords`
            # checks collision for everyone, surfing or not.
            #
            # What surfing legitimately changes is WHICH cells a walker may
            # occupy, not whether collision applies, and that is already
            # handled: a collision-free water tile is `passable` and never
            # reaches this branch at all.
            return None
        if self._dir_blocked(here, there, d):
            return None
        # MOUNTING is checked before the elevation branch, because that branch
        # exempts the wildcard levels (0 and 15) and Route 119's channel is
        # elevation 0 -- so restricting the mount inside it let the cliff
        # crossing straight back in, and the walk stalled one cell further
        # west at (20,46) instead of (21,46).
        if self.surfing and self._is_water(there) and not self._is_water(here) \
                and z != self.SHORE_ELEVATION and z not in ELEVATION_ANY:
            return None
        if z and there.elevation not in ELEVATION_ANY and there.elevation != z \
                and not self._surf_seam(here, there, z):
            # Mount and DISMOUNT both cross an elevation seam: the sea is
            # level 1 and every shore is 3, so a surfer who could get onto
            # the water could never step off it -- the probe stranded at
            # Route 117's pond with 46 reachable cells in both modes.
            return None
        # NO water override. `ObjectEventUpdateZCoord`
        # (src/event_object_movement.c:7586-7598) has one rule for everyone:
        # unless a bridge is involved you TAKE the tile's elevation, surfing
        # or not. Pinning a surfer to their old level was mine, from tonight's
        # surf patch, and it cost the river its wildcard: Route 119's channel
        # is elevation 0, so the engine makes a surfer z=0 -- able to step
        # onto any level -- while nav kept them at 3 and the northern
        # elevation-4 road stayed unreachable.
        return (x + dx, y + dy, self._next_z(z, there.elevation, here.elevation))

    def _is_water(self, cell) -> bool:
        try:
            return self.beh.is_surfable(cell.behavior)
        except Exception:  # noqa: BLE001 - no behavior table, no water
            return False

    @staticmethod
    def _next_z(z, dest_elevation, from_elevation=None):
        """The elevation you are ON after stepping onto `dest_elevation`.

        `ObjectEventUpdateZCoord` (src/event_object_movement.c:7586-7598) is
        the authority, and 0 and 15 are NOT the same case:

            if (z == 0xF || z2 == 0xF) return;   // bridge: keep what you had
            objEvent->currentElevation = z;      // otherwise TAKE the tile's

        So stepping onto an elevation-0 tile makes you elevation 0, and
        `IsZCoordMismatchAt` (:7528) returns FALSE for z == 0 -- from a 0-tile
        you may step onto ANY level. That is how the game changes elevation at
        all, and treating 0 like 15 (keep your old level) meant the walker
        could never leave the level it started on. Route 114's southern half
        is elevation 4 and its northern half 3, joined by two elevation-0
        cells at (14,49) and (15,49): with 0 mishandled, 567 of 3200 cells
        were reachable, Meteor Falls' door sat in a 52-cell "pocket", and the
        run concluded SURF was required for a road it could already walk.
        """
        # The engine tests BOTH tiles: `if (z == 0xF || z2 == 0xF) return;`
        # -- stepping OFF a bridge preserves your level too, not just
        # stepping onto one.
        if dest_elevation == 15 or from_elevation == 15:
            return z
        return dest_elevation

    def elevation_at(self, map_name, x, y):
        c = self.cell(map_name, x, y)
        return 0 if c is None or c.elevation in ELEVATION_ANY else c.elevation

    def warp_cells(self, map_name) -> set:
        """Every warp-event tile on this map.

        Memoised on the map info, which is already cached: routing asks this
        on every BFS expansion.
        """
        info = self.info(map_name)
        cached = getattr(info, "_warp_cells", None)
        if cached is None:
            cached = {(w.x, w.y) for w in info.warps}
            try:
                info._warp_cells = cached
            except Exception:  # noqa: BLE001 - a frozen dataclass just re-derives
                pass
        return cached

    def find_path(self, map_name, start, goal, start_z=None, max_nodes=60000):
        """BFS within one map. Returns direction letters, or None.

        `start_z` is the player's live elevation; None reads it off the
        starting tile, which is right for a player standing still.

        A warp tile is a DESTINATION, never a through-cell. Stepping onto one
        fires it -- that is the whole of gotcha 15 -- so a path that crosses
        one does not lead where it says. Granite Cave is the worked example:
        the player stood at (17,12) on 1F with a warp to B1F at (17,11)
        directly above, and the plain BFS opened its route to the Route 106
        exit with `U`. The step fired the warp, the run landed on B1F, routed
        back up, and oscillated between two floors indefinitely -- moving the
        whole time, so nothing that watches for a STOPPED run could see it.
        """
        sx, sy = start
        gx, gy = goal
        if self.cell(map_name, gx, gy) is None:
            return None
        z0 = self.elevation_at(map_name, sx, sy) if start_z is None else start_z
        # Standing ON a warp is normal -- every door arrival leaves you there
        # -- so the start is exempt. Only cells entered mid-path are barred.
        blocked = self.warp_cells(map_name) - {(sx, sy), (gx, gy)}
        seen = {(sx, sy)}
        queue = deque([((sx, sy, z0), [])])
        nodes = 0
        while queue and nodes < max_nodes:
            (x, y, z), path = queue.popleft()
            nodes += 1
            if (x, y) == (gx, gy):
                return path
            for d in "URDL":
                nxt = self.step(map_name, x, y, z, d)
                # Key the closed set on (x, y, Z), not (x, y). Elevation-15
                # bridge cells accept ANY level and preserve it, so whichever
                # wave reaches one first used to close it to every other
                # level: on Route 119 the z=3 wave coming down the river shut
                # the (21..23, 84..85) bridge against the z=4 road wave and
                # severed the northern half of the map. A cell visited at one
                # elevation is not the same state as the same cell at another.
                if nxt is None or nxt in seen:
                    continue
                seen.add(nxt)
                if nxt[:2] in blocked:
                    continue
                queue.append((nxt, path + [d]))
        return None

    def reachable(self, map_name, start, start_z=None) -> set:
        """Cells reachable from `start`, memoised.

        The walkable grid is static, so this is a pure function of (map, cell,
        elevation) -- and routing asks it constantly: `usable_exits` runs one
        BFS per map per routing call, and the play loop routes every step. Left
        uncached it dropped a live run from 47k frames a minute to 8.7k.

        Cached per CELL, which is why the run still looked frozen after the
        first two fixes: `route_legs` asks `usable_exits` once per BFS node,
        every node is a different cell, and so every node paid for its own
        full component fill. A faulthandler dump named this line directly.

        Sharing one component between its members is the obvious fix and it
        is WRONG here; it was written, measured against the uncached BFS, and
        removed. Reachability on these maps is not symmetric: from an
        elevation-15 bridge cell the fill spans both banks, from a bank cell it
        spans one. Aliasing over-reported Route 110 as 1730 cells when the
        truth from those cells is 797 -- routing would have planned through
        road that is not there. Ledges break it too. Planning is bounded by
        time instead; see `route_legs`.
        """
        key = (map_name, tuple(start), start_z, self.surfing,
               self.waterfall)
        hit = self._reach_cache.get(key)
        if hit is not None:
            return hit
        triples = self._reachable_triples(map_name, start, start_z)
        out = {(x, y) for x, y, _ in triples}
        if len(self._reach_cache) > 200_000:
            self._reach_cache.clear()
        self._reach_cache[key] = out
        return out

    def _reachable_uncached(self, map_name, start, start_z=None) -> set:
        return {(x, y) for x, y, _ in
                self._reachable_triples(map_name, start, start_z)}

    def _reachable_triples(self, map_name, start, start_z=None) -> set:
        sx, sy = start
        z0 = self.elevation_at(map_name, sx, sy) if start_z is None else start_z
        # Visit on (x, y, Z); RETURN only cells. Same reason as find_path: an
        # elevation-15 bridge cell preserves whatever level arrives on it, so
        # closing it against other levels severs real roads -- Route 119's
        # northern half hung off exactly that. The returned set stays flat
        # because every caller asks "can I stand here", not "at what level".
        visited: set = {(sx, sy, z0)}
        queue = deque([(sx, sy, z0)])
        while queue:
            x, y, z = queue.popleft()
            for d in "URDL":
                nxt = self.step(map_name, x, y, z, d)
                if nxt is None or nxt in visited:
                    continue
                visited.add(nxt)
                queue.append(nxt)
        return visited

    # ---- cross-map routing -----------------------------------------------

    def _cache(self, name) -> dict:
        """A per-instance dict, created on first use.

        Not a class attribute: a mutable default would be SHARED by every
        MapData in the process, so one map's dive gates would answer for
        another's. Not __init__ either, because objects built with `__new__`
        never run it.
        """
        got = self.__dict__.get(name)
        if got is None:
            got = self.__dict__[name] = {}
        return got

    def dive_gates(self, map_name, direction="dive") -> list:
        """Cells a dive can be performed from, per the engine's own test.

        Going DOWN: `MetatileBehavior_IsDiveable` -- semi-deep, unused-deep or
        Sootopolis-deep water. Coming UP: any underwater tile that is not
        `MetatileBehavior_IsNotSurfacable` (no-surfacing or seaweed).

        CACHED, because this is static map data and the first version was not:
        it scanned every cell of the map, `usable_exits` called it for every
        dive edge, and `route_legs` calls `usable_exits` once per BFS node. On
        Route 119 that is 5,600 cells per node, hundreds of nodes per journey,
        and the live run spent THIRTY-TWO MINUTES at 99.6% CPU inside one
        travel() call with the emulator never once advancing. Caught with
        faulthandler; the stack was dive_gates <- usable_exits <- route_legs.
        """
        cache = self._cache("_dive_cache")
        key = (map_name, direction)
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            info = self.info(map_name)
        except Exception:  # noqa: BLE001
            cache[key] = []
            return []
        out = []
        for y in range(info.height):
            for x in range(info.width):
                c = self.cell(map_name, x, y)
                if c is None:
                    continue
                if direction == "dive":
                    if c.behavior in DIVEABLE:
                        out.append((x, y))
                elif c.behavior not in NO_SURFACING and c.kind != "blocked":
                    out.append((x, y))
        cache[key] = out
        return out

    def dive_landing(self, map_name, edge, cell):
        """Where a dive puts you: the same cell on the connected map."""
        dest = edge.get("dest")
        if not dest or dest not in self.index:
            return None
        return (dest, cell[0], cell[1])

    def warp_landing(self, warp: Warp):
        """Where a warp puts you: the destination's own ``dest_warp_id``
        (src/overworld.c:425-430)."""
        dest = self.const_to_name(warp.dest_map)
        if dest not in self.index:
            return None
        info = self.info(dest)
        if 0 <= warp.dest_warp_id < len(info.warps):
            w = info.warps[warp.dest_warp_id]
            return dest, w.x, w.y
        return dest, None, None

    def connection_landing(self, map_name, direction, x, y, dest_name=None):
        """Coordinates after crossing a seam (src/fieldmap.c:583-604).

        `dest_name` matters whenever a map has more than one connection on the
        same side, and several do: Route 111's left edge borders BOTH Route 112
        and Route 113, at different offsets. Matching on direction alone
        returned the first one, so every landing computed for the Route 112
        seam was actually a cell on Route 113 -- which is how "cross west into
        Route 112" became "no route to the cable car" and badge 4 stalled.
        """
        for conn in self.info(map_name).connections:
            if _CONNECTION_DIR.get(conn["direction"]) != direction:
                continue
            if dest_name is not None and \
                    self.const_to_name(conn["map"]) != dest_name:
                continue
            dest = self.const_to_name(conn["map"])
            if dest not in self.index:
                return None
            other = self.info(dest)
            off = conn["offset"]
            if direction == "U":
                land = (x - off, other.height - 1)
            elif direction == "D":
                land = (x - off, 0)
            elif direction == "L":
                land = (other.width - 1, y - off)
            else:
                land = (0, y - off)
            # Naming the destination is not enough to disambiguate: a seam
            # spans only as many rows/columns as the neighbour HAS, and the
            # offset arithmetic happily returns a coordinate outside it.
            # Route 111's west edge is 140 tall; Route 113 (100x20, offset 0)
            # covers rows 0-19 and Route 112 (40x60, offset 20) covers 20-79.
            # Unbounded, row 66 "landed" on Route 113 at y=66 -- off a 20-row
            # map -- so the router planned a Route 113 crossing the walker
            # could only perform as a Route 112 one. It crossed to the wrong
            # map, re-planned from there, walked back, and oscillated until
            # the leg budget ran out. A landing off the destination is not a
            # landing.
            if not (0 <= land[0] < other.width and 0 <= land[1] < other.height):
                continue
            return dest, land[0], land[1]
        return None

    def edge_cells(self, map_name, direction) -> list[tuple[int, int]]:
        """Walkable cells on one border of a map -- where a seam is crossed."""
        g = self.grid(map_name)
        if not g:
            return []
        h, w = len(g), len(g[0])
        if direction == "U":
            cells = [(x, 0) for x in range(w)]
        elif direction == "D":
            cells = [(x, h - 1) for x in range(w)]
        elif direction == "L":
            cells = [(0, y) for y in range(h)]
        elif direction == "R":
            cells = [(w - 1, y) for y in range(h)]
        else:
            return []
        out = []
        for x, y in cells:
            c = self.cell(map_name, x, y)
            if c is not None and c.passable:
                out.append((x, y))
        return out

    def usable_exits(self, map_name, from_cell) -> list[dict]:
        """The exits you can actually WALK to from here.

        A map is not one connected place. Route 104 is 40x80 in two halves
        joined only through Petalburg Woods: standing in the south half, 540
        cells are reachable and not one of them touches the northern border,
        so the "U connection to RustboroCity" that `exits()` lists is real and
        unusable. Map-level routing planned that seam anyway and the journey
        failed 12 times in a row with nothing to say but "could not cross the
        U seam".

        Reachability is asked FORWARD from the current cell rather than as a
        symmetric component, because ledges are one-way: where you can get to
        is the question, and it is not the same as what shares a region.
        """
        reach = self.reachable(map_name, tuple(from_cell))
        out = []
        for e in self.exits(map_name):
            if e["kind"] == "dive":
                # The "border cells" of a vertical seam are the tiles you can
                # actually dive from (or surface through, going up).
                gates = [c for c in self.dive_gates(map_name, e["direction"])
                         if c in reach]
                if gates:
                    out.append(dict(e, cross_at=gates[0],
                                    cross_candidates=gates))
                continue
            if e["kind"] == "warp":
                # A door is SOLID in the collision data and still enterable:
                # Rustboro's gym door (27,19) has collision 1, so asking
                # "can I reach the warp cell" answered no for every building
                # in the game and routing could never plan an indoor leg.
                # take_warp enters from an adjacent cell anyway (a warp fires
                # on the step that ENTERS it, never by standing on it), so the
                # honest test is whether we can stand beside it.
                cell = (e["x"], e["y"])
                # A live BODY on the warp tile is not a door being solid --
                # it is someone standing in the doorway, and no entry step
                # can complete. Lavaridge's gym parks a trainer directly on
                # the spring tile at (10,19); the router chose that hole
                # every time (nearest), take_warp failed every time, and the
                # gym challenge looped for a quarter of an hour. `blocked`
                # carries live bodies (marked per-plan by the driver), never
                # architecture, so skipping it here reroutes through the
                # next hole and costs doors nothing.
                if cell in self.blocked.get(map_name, ()):
                    continue
                if cell in reach or any(
                    (cell[0] + dx, cell[1] + dy) in reach
                    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                ):
                    out.append(e)
            else:
                border = self.edge_cells(map_name, e["direction"])
                cross = [c for c in border if c in reach]
                if cross:
                    # EVERY reachable crossing cell, not a representative one.
                    # A seam is a whole border, and two cells a few tiles apart
                    # land in different places: crossing Route 104's north edge
                    # at x=22 lands in a 36-cell pocket of Rustboro with no way
                    # to the gym, while the road at x=12-19 lands on the road.
                    #
                    # Picking the MIDDLE cell was the same bug wearing a
                    # different hat. Verdanturf's east border and Route 117's
                    # west border are both walkable at y=7, so the middle
                    # candidate crossed there -- and Route 117 (0,7) is a
                    # one-cell pocket. The player arrived unable to move in any
                    # direction, raw d-pad included, with no scene and no
                    # dialog to blame. Rows 9-12 cross onto the open road.
                    #
                    # So rank by where each candidate LANDS: the size of the
                    # reachable component on the far side. A pocket scores 1
                    # and loses to anything.
                    e = dict(e, cross_at=self._best_crossing(map_name, e, cross),
                             cross_candidates=cross)
                    out.append(e)
        return out

    def _rank_probe(self, candidates) -> list:
        """The subset of a border worth pricing.

        Ranking a crossing costs one reachability BFS PER CANDIDATE on the far
        side, and a border can offer forty. `route_legs` asks per BFS node, so
        the planner was doing thousands of component fills -- half of the
        32-minute journey, and still the top of every faulthandler dump after
        the results were memoised (each candidate lands somewhere different,
        so a memo only pays off on repeats).

        An even spread is enough for what the ranking is FOR: not choosing the
        roomiest cell, just not landing in a one-cell pocket. Neighbouring
        border cells nearly always land in the same component. The tradeoff is
        real and worth stating: a border where only an unsampled cell reaches
        open map is now missed, and per-leg replanning is what recovers.
        """
        if len(candidates) <= _RANK_SAMPLE:
            return list(candidates)
        stride = len(candidates) / _RANK_SAMPLE
        return [candidates[int(i * stride)] for i in range(_RANK_SAMPLE)]

    def _best_crossing(self, map_name, edge, candidates):
        """The border cell that lands somewhere with room to walk.

        Ties keep the earlier candidate, which makes the choice deterministic;
        a route that changes between identical calls is impossible to debug.
        """
        best, best_room = candidates[len(candidates) // 2], -1
        for cell in self._rank_probe(candidates):
            room = self._landing_room(map_name, edge, cell)
            if room > best_room:
                best, best_room = cell, room
        return best

    def exit_landing(self, map_name, edge) -> tuple[str, int, int] | None:
        """Where an exit puts you, as (map, x, y). None when unknown."""
        if edge["kind"] == "warp":
            lands = edge.get("lands_at")
            if lands and lands[0] is not None:
                return (edge["dest"], int(lands[0]), int(lands[1]))
            return None
        cell = edge.get("cross_at")
        if cell is None:
            border = self.edge_cells(map_name, edge["direction"])
            if not border:
                return None
            cell = border[len(border) // 2]
        if edge.get("kind") == "dive":
            return self.dive_landing(map_name, edge, cell)
        return self.connection_landing(map_name, edge["direction"], *cell,
                                       dest_name=edge.get("dest"))

    def route_legs(self, start_map, start_cell, dest_map, max_hops=40,
                   dest_cell=None, deadline=None):
        """A route as EDGES, honouring what is reachable at each step.

        Returns a list of legs, each ``{from_map, edge, to_map, lands_at}``, or
        None. Nodes are (map, landing cell) rather than map names, so a map may
        legitimately appear twice -- Route 104's two halves are two different
        places to be, and the way between them is through the Woods.

        `deadline` is an absolute `time.time()` after which the search returns
        the best answer it has instead of the true one. It exists because this
        search is genuinely expensive and cannot be memoised away: every node
        needs `usable_exits`, which needs a reachability fill for that node's
        cell, and those fills are NOT shareable between cells of one map
        (asymmetric elevation and one-way ledges -- the component alias that
        would have made this cheap over-reported Route 110 as 1730 cells
        against a true 797 and was removed). Unbounded, a Route 119 plan spun
        for over half an hour while the emulator advanced zero frames and the
        run looked, from outside, like a player standing still.
        """
        # Bounded even when the CALLER passes nothing. Six call sites ask for
        # a route -- two of them in the play loop -- and threading a deadline
        # through each is how one gets missed; the one that gets missed is the
        # one that hangs. The caller's deadline only ever tightens this.
        own = _time.time() + self.plan_budget_s if self.plan_budget_s else None
        stamps = [t for t in (deadline, own) if t is not None]
        deadline = min(stamps) if stamps else None
        start = (start_map, tuple(start_cell))
        # Asking for a CELL is a different question from asking for a MAP
        # whenever a map is not one connected place. Lavaridge's gym forced
        # it: the hot-spring floor splits 1F into pockets joined only by
        # falling through holes to B1F and climbing back, so Flannery stands
        # 36 walkable cells and two floors from the door. Asking for the map
        # answers "you are already there"; asking for the cell finds the holes.
        if dest_cell is None:
            if start_map == dest_map:
                return []
        else:
            dest_cell = tuple(dest_cell)
            if start_map == dest_map and \
                    dest_cell in self.reachable(start_map, tuple(start_cell)):
                return []

        def arrived(here, cell):
            if here != dest_map:
                return False
            if dest_cell is None:
                return True
            return dest_cell in self.reachable(here, cell)

        seen = {start}
        queue = deque([(start_map, tuple(start_cell), [])])
        while queue:
            here, cell, legs = queue.popleft()
            if len(legs) >= max_hops:
                continue
            if deadline is not None and _time.time() > deadline:
                # Out of time. Returning None says "no route", which is the
                # same answer a genuinely unreachable destination gives, and
                # the caller already knows how to fall back from it. Saying so
                # in last_route_reason keeps the two distinguishable in a log.
                self.last_route_reason = (
                    f"search timed out after {len(seen)} nodes"
                )
                return None
            try:
                exits = self.usable_exits(here, cell)
            except (KeyError, OSError, IndexError):
                continue
            for e in exits:
                dest = e["dest"]
                if dest not in self.index:
                    continue
                for cand in self._crossings(here, e):
                    land = self.exit_landing(here, cand)
                    leg = {"from_map": here, "edge": cand, "to_map": dest,
                           "lands_at": land[1:] if land else None}
                    # A crossing with no landing is not a crossing. This
                    # accepted one anyway whenever it happened to name the
                    # destination, so `route_legs(.., 'Route113')` returned a
                    # one-leg plan whose `lands_at` was None while
                    # `route_legs(.., 'FallarborTown')` -- one hop further
                    # along the same road -- correctly returned nothing. The
                    # bogus leg then went to travel, which crossed somewhere
                    # it had not planned.
                    if land is None:
                        continue
                    if arrived(dest, (land[1], land[2])):
                        return legs + [leg]
                    # NOT `cell`: that name is the position this whole
                    # expansion is planned from, and rebinding it here
                    # silently re-pointed every LATER exit of the same map at
                    # the previous candidate's landing. One exit planned
                    # correctly and the rest were drawn from the wrong place.
                    nxt_cell = (land[1], land[2])
                    # An arrival cell the grid calls solid is a modelling
                    # artefact, not a dead end: the bottom row of a town is
                    # border art and the walker lands a tile inside it. Snap
                    # to the nearest walkable cell so the COMPONENT is right.
                    nxt_cell = self._snap(dest, nxt_cell)
                    if nxt_cell is None:
                        continue
                    key = (dest, nxt_cell)
                    if key in seen:
                        continue
                    seen.add(key)
                    queue.append((dest, nxt_cell, legs + [leg]))
        return None

    def _crossings(self, map_name, edge) -> list[dict]:
        """One edge variant per DISTINCT landing, so routing sees real choices.

        Deduped by landing cell: a forty-cell border usually lands in one or
        two places, and enqueueing forty identical states would swamp the BFS.
        """
        cands = edge.get("cross_candidates")
        if not cands:
            return [edge]
        # Best landing first. The BFS is breadth-first over LEGS, so several
        # crossings of the same border tie on cost and the first one offered
        # wins -- and in border order the first is often the worst. Verdanturf's
        # east edge offers y=5 and y=7 before y=8; the first two land in
        # one-cell pockets of Route 117 and the third lands on 698 cells of
        # open road. The run crossed into the pocket and could not move at all.
        # Only a SAMPLE of the border gets priced -- see _rank_probe for why
        # and for the tradeoff. Unpriced cells sort last but stay offered.
        room = {
            cell: self._landing_room(map_name, edge, cell)
            for cell in self._rank_probe(cands)
        }
        ranked = sorted(cands, key=lambda cell: -room.get(cell, 0))
        out, seen_land = [], set()
        for cell in ranked:
            variant = dict(edge, cross_at=cell)
            land = self.exit_landing(map_name, variant)
            key = land[1:] if land else None
            if key in seen_land:
                continue
            seen_land.add(key)
            out.append(variant)
        return out

    def _landing_room(self, map_name, edge, cell) -> int:
        """How much walkable map a crossing lands on. A pocket scores 1.

        MEMOISED on the landing, not the crossing. `_crossings` and
        `_best_crossing` both rank every candidate on a border -- up to forty
        of them -- and each call was a full reachability BFS on the far side.
        `route_legs` then does that per BFS node. Together with the uncached
        dive scan this is what made a one-leg journey take half an hour: the
        planner was recomputing the same handful of landing components
        thousands of times.

        The key includes the movement mode, because SURF and WATERFALL change
        what a component contains -- caching across a field-move change would
        answer with a map the party can no longer walk.
        """
        landing = self.exit_landing(map_name, dict(edge, cross_at=cell))
        if landing is None:
            return 0
        dest_map, x, y = landing
        cache = self._cache("_room_cache")
        key = (dest_map, x, y, self.surfing, self.waterfall)
        hit = cache.get(key)
        if hit is not None:
            return hit
        try:
            room = len(self.reachable(dest_map, (x, y)))
        except Exception:  # noqa: BLE001 - an unreadable map is not a crash
            room = 0
        if len(cache) > 8192:
            cache.clear()
        cache[key] = room
        return room

    def _snap(self, map_name, cell, radius: int = 3):
        """The nearest walkable cell to `cell`, or None within `radius`."""
        x, y = cell
        c = self.cell(map_name, x, y)
        if c is not None and c.passable:
            return (x, y)
        best = None
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                cc = self.cell(map_name, nx, ny)
                if cc is None or not cc.passable:
                    continue
                dist = abs(dx) + abs(dy)
                if best is None or dist < best[0]:
                    best = (dist, (nx, ny))
        return best[1] if best else None

    def neighbours(self, map_name) -> list[tuple[str, dict]]:
        out = []
        for e in self.exits(map_name):
            dest = e["dest"]
            if dest in self.index:
                out.append((dest, e))
        return out

    def route(self, start_map, dest_map, max_hops=60) -> list[str] | None:
        """Map-level route: BFS over the warp/connection graph."""
        if start_map == dest_map:
            return [start_map]
        seen = {start_map}
        queue = deque([(start_map, [start_map])])
        while queue:
            here, path = queue.popleft()
            if len(path) > max_hops:
                continue
            try:
                nbrs = self.neighbours(here)
            except (KeyError, OSError):
                continue
            for nxt, _edge in nbrs:
                if nxt in seen:
                    continue
                if nxt == dest_map:
                    return path + [nxt]
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
        return None

    # ---- rendering (for humans only) --------------------------------------

    GLYPHS = {
        "floor": ".", "grass": "%", "water": "~", "warp": "O",
        "ledge": "^", "forced": "=", "blocked": "#",
    }

    def render(self, map_name, here=None, reachable=None) -> str:
        """An ASCII picture. Art for humans -- decide from `find_tiles`,
        `exits` and `cell`, which answer by absolute coordinate."""
        g = self.grid(map_name)
        info = self.info(map_name)
        warps = {(w.x, w.y) for w in info.warps}
        npcs = {(o["x"], o["y"]) for o in info.objects}
        lines = [f"{info.name} {info.width}x{info.height} layout={info.layout}"]
        lines.append("    " + "".join(str(x % 10) for x in range(info.width)))
        for y, row in enumerate(g):
            out = []
            for x, c in enumerate(row):
                if here == (x, y):
                    out.append("@")
                elif (x, y) in npcs:
                    out.append("N")
                elif (x, y) in warps:
                    out.append("O")
                elif reachable is not None and (x, y) not in reachable and c.passable:
                    out.append(",")
                else:
                    out.append(self.GLYPHS.get(c.kind, "?"))
            lines.append(f"{y:3} {''.join(out)}")
        for e in self.exits(map_name):
            if e["kind"] == "warp":
                lines.append(
                    f"  warp {e['id']} ({e['x']},{e['y']}) -> {e['dest']} "
                    f"warp {e['dest_warp']} {e['lands_at'] or ''}"
                )
            else:
                lines.append(
                    f"  connection {e['direction']} offset {e['offset']} -> {e['dest']}"
                )
        return "\n".join(lines)
