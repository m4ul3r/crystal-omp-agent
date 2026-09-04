"""Walkability grids and pathfinding, built from the disassembly's own data:
maps/<Name>.blk (block indices), data/tilesets/*_collision.asm (2x2 collision
cells per block), constants/map_constants.asm (sizes), data/maps/maps.asm
(tileset per map), data/maps/attributes.asm (CamelCase <-> CONST_NAME).

Coordinates match wXCoord/wYCoord (one cell = one walking step).
"""

import json
import re
from collections import deque
from pathlib import Path

from . import paths

WALKABLE = {0x00, 0x14, 0x18}          # floor, long grass, tall grass
WATER = {0x29}                          # COLL_WATER: routable when surfing
                                        # (NOT 0x27 COLL_BUOY -- buoys block surf)
                                        # (whirlpools/waterfalls stay walls)
WARPS = set(range(0x70, 0x80))         # doors, stairs, carpets, ladders, caves
HOPS = {0xA0: "R", 0xA1: "L", 0xA2: "U", 0xA3: "D"}  # one-way ledges
ICE = {0x23}                            # COLL_ICE: sliding floor
COLL_PIT = 0x60                         # COLL_PIT: fall-through hole
DYNAMIC = {0x12, 0x15, 0x24, 0x33}      # cut tree, headbutt tree, whirlpool,
                                        # waterfall: removable in-game, so
                                        # they never SPLIT a region (regions
                                        # only split at permanent walls);
                                        # still walls for cell-level pathing
CONN_NAME = {"R": "east", "L": "west", "U": "north", "D": "south"}
STEP = {"R": (1, 0), "L": (-1, 0), "U": (0, -1), "D": (0, 1)}


class MapData:
    def __init__(self, repo):
        repo = Path(repo)
        self._repo = repo

        # COLL_* name -> value
        self.coll = {}
        for m in re.finditer(r"DEF COLL_(\w+)\s+EQU \$([0-9a-fA-F]+)",
                             (repo / "constants/collision_constants.asm").read_text()):
            self.coll[m.group(1)] = int(m.group(2), 16)

        # CONST_NAME -> (group, num, width, height) in blocks
        self.consts = {}
        group = 0
        for line in (repo / "constants/map_constants.asm").read_text().splitlines():
            if m := re.match(r"\tnewgroup", line):
                group += 1
                num = 0
            elif m := re.match(r"\tmap_const\s+(\w+),\s*(\d+),\s*(\d+)", line):
                num += 1
                self.consts[m.group(1)] = (group, num, int(m.group(2)), int(m.group(3)))

        # CONST_NAME -> CamelCase map source stem.
        self.camel = {}
        for m in re.finditer(r"map_attributes\s+(\w+),\s+(\w+),",
                             (repo / "data/maps/attributes.asm").read_text()):
            self.camel[m.group(2)] = m.group(1)
        self.const_by_camel = {
            camel: const for const, camel in self.camel.items()
        }
        self._const_by_normalized = {}
        for const, camel in self.camel.items():
            self._const_by_normalized[self._normalize_name(const)] = const
            self._const_by_normalized[self._normalize_name(camel)] = const
        # CamelCase -> tileset constant
        self.tileset = {}
        for m in re.finditer(r"\tmap (\w+),\s+TILESET_(\w+),",
                             (repo / "data/maps/maps.asm").read_text()):
            self.tileset[m.group(1)] = m.group(2).lower()

        # CamelCase -> .blk file (several maps can alias one INCBIN)
        self.blk = {}
        pending = []
        for line in (repo / "data/maps/blocks.asm").read_text().splitlines():
            if m := re.match(r"(\w+)_Blocks:", line):
                pending.append(m.group(1))
            elif m := re.search(r'INCBIN "([^"]+)"', line):
                for name in pending:
                    self.blk[name] = m.group(1)
                pending = []

        # CONST_NAME -> {"north": (dest_const, offset), ...}; offsets are the
        # `connection` macro's 4th arg: target map origin relative to current
        # map. Landing math verified against engine/overworld/warp_connection.asm
        # EnterMapConnection + data/maps/attributes.asm connection macro.
        self.conns = {}
        cur = None
        for line in (repo / "data/maps/attributes.asm").read_text().splitlines():
            if m := re.match(r"\tmap_attributes\s+\w+,\s*(\w+),", line):
                cur = m.group(1)
                self.conns.setdefault(cur, {})
                continue
            m = re.match(r"\tconnection\s+(north|south|west|east),\s*"
                         r"(\w+),\s*(\w+),\s*(.+?)\s*$", line)
            if m and cur:
                d, _camel, dest, off = m.groups()
                self.conns[cur][d] = (dest, self._offset(off))

        # CONST_NAME -> {(x,y): (dest_const, warp_id)} and
        # CONST_NAME -> [(x,y) | None] indexed by warp_id-1 (None = back-warp
        # with id -1: returns you where you came from; not routable).
        # Parsed from maps/<CamelCase>.asm def_warp_events sections.
        self.warps = {}
        self.warp_cells = {}
        for camel, const in self.const_by_camel.items():
            path = repo / "maps" / f"{camel}.asm"
            if not path.exists():
                continue
            warps, cells, section = {}, [], False
            for line in path.read_text().splitlines():
                if re.match(r"\tdef_warp_events", line):
                    section = True
                    continue
                if section and re.match(r"\tdef_\w+", line):
                    break
                m = re.match(r"\twarp_event\s+(-?\d+),\s*(-?\d+),\s*"
                             r"([A-Z0-9_]+),\s*(-?\d+)", line)
                if m and section:
                    x, y, dest, wid = (m.group(i) for i in range(1, 5))
                    x, y, wid = int(x), int(y), int(wid)
                    if wid >= 1:
                        cells.append((x, y))
                        warps[(x, y)] = (dest, wid)
                    else:
                        cells.append(None)
            if cells:
                self.warps[const] = warps
                self.warp_cells[const] = cells

        # CONST_NAME -> {(bx, by): {block, ...}} declared changeblock
        # variants from maps/<CamelCase>.asm: hidden doors/stairs/boulders
        # the map script can swap in. Coords in the script are STEP coords
        # of the block's top-left cell (the engine adds the +4 border then
        # halves), so block = (x//2, y//2). Macro-generated changeblocks
        # with symbolic coords (Goldenrod underground doors) are not
        # scanned -- literal-int declarations only.
        self.changeblocks = {}
        for camel, const in self.const_by_camel.items():
            path = repo / "maps" / f"{camel}.asm"
            if not path.exists():
                continue
            for m in re.finditer(r"\tchangeblock\s+(\d+),\s*(\d+),\s*"
                                 r"\$([0-9a-fA-F]+)", path.read_text()):
                x, y, b = (int(m.group(1)), int(m.group(2)),
                           int(m.group(3), 16))
                self.changeblocks.setdefault(const, {}) \
                    .setdefault((x // 2, y // 2), set()).add(b)

        self._coll_cache = {}
        self._grid_cache = {}
        self._cell_overrides = {}    # {(const, x, y): original collision}

    @staticmethod
    def _normalize_name(name):
        return re.sub(r"[\s_]+", "", str(name)).casefold()

    def resolve(self, name):
        """Resolve CONST, CamelCase, or case/space/underscore-insensitive name."""
        if name in self.consts:
            return name
        if name in self.const_by_camel:
            return self.const_by_camel[name]
        return self._const_by_normalized.get(self._normalize_name(name))

    def _tileset_coll(self, tileset):
        """block id -> [UL, UR, LL, LR] collision bytes for a tileset."""
        if tileset not in self._coll_cache:
            table = []
            path = self._repo / f"data/tilesets/{tileset}_collision.asm"
            for m in re.finditer(r"tilecoll\s+(\w+),\s*(\w+),\s*(\w+),\s*(\w+)",
                                 path.read_text()):
                table.append([self.coll[g] for g in m.groups()])
            self._coll_cache[tileset] = table
        return self._coll_cache[tileset]

    def grid(self, const_name):
        """Collision byte per walkable cell: grid[y][x], 2*w x 2*h cells.

        Decodes the DEFAULT blockdata (verified byte-exact against the
        built ROM and GetCoordTileCollision's block*4 + (y&1)*2 + (x&1)
        quadrant math). Cells a map script can swap via changeblock are
        listed by conditional() -- their static byte is only the
        pre-event state."""
        if const_name in self._grid_cache:
            return self._grid_cache[const_name]
        camel = self.camel[const_name]
        _, _, w, h = self.consts[const_name]
        blocks = (self._repo / self.blk[camel]).read_bytes()
        table = self._tileset_coll(self.tileset[camel])
        grid = [[0x07] * (2 * w) for _ in range(2 * h)]
        for by in range(h):
            for bx in range(w):
                cells = table[blocks[by * w + bx]]
                for i, c in enumerate(cells):  # UL, UR, LL, LR
                    grid[by * 2 + (i // 2)][bx * 2 + (i % 2)] = c
        self._grid_cache[const_name] = grid
        return grid

    def conditional(self, const_name):
        """Event-conditional cells: the map script declares changeblock(s)
        for their block (hidden doors, uncovered stairs, falling boulders),
        so the static grid() byte is only the pre-event state -- probe live
        before trusting wall/floor there. Returns {(x, y): (byte, ...)}
        of the possible collision bytes (sorted, default included),
        restricted to cells whose byte actually differs between the
        default blockdata and a declared variant: the B2F transmitter
        door only flags its (14,12)/(15,12) top half; the (14,13)/(15,13)
        half is floor in both states."""
        cache = self.__dict__.setdefault("_conditional_cache", {})
        if const_name in cache:
            return cache[const_name]
        out = {}
        decls = getattr(self, "changeblocks", {}).get(const_name)
        if decls:
            camel = self.camel[const_name]
            _, _, w, h = self.consts[const_name]
            blocks = (self._repo / self.blk[camel]).read_bytes()
            table = self._tileset_coll(self.tileset[camel])
            for (bx, by), variants in decls.items():
                if not (0 <= bx < w and 0 <= by < h):
                    continue
                cand = [table[blocks[by * w + bx]]] + \
                    [table[b] for b in sorted(variants) if b < len(table)]
                for i in range(4):
                    vals = {c[i] for c in cand}
                    if len(vals) > 1:
                        out[(bx * 2 + i % 2, by * 2 + i // 2)] = \
                            tuple(sorted(vals))
        cache[const_name] = out
        return out

    def cell_kind(self, const_name, x, y):
        """Coarse class for planners/renders: 'conditional' when a declared
        changeblock can swap this cell's collision (never trust wall/floor
        there statically), else 'floor'/'grass'/'ice'/'water'/'warp'/
        'ledge'/'wall'."""
        if (x, y) in self.conditional(const_name):
            return "conditional"
        c = self.grid(const_name)[y][x]
        if c in WALKABLE:
            return "floor" if c == 0x00 else "grass"
        if c in ICE:
            return "ice"
        if c in WATER:
            return "water"
        if c in WARPS:
            return "warp"
        if c in HOPS:
            return "ledge"
        return "wall"

    @staticmethod
    def _offset(expr):
        """`connection` 4th arg: plain int, negative int, or legacy
        `(<a>) - (<b>)` form."""
        expr = expr.replace(" ", "")
        if m := re.fullmatch(r"\((-?\w+)\)-\((-?\w+)\)", expr):
            return int(m.group(1), 0) - int(m.group(2), 0)
        return int(expr, 0)

    surf = False   # set True (Driver.enable_surf) to route across WATER

    def _enterable(self, const_name, x, y):
        """Can a step land here? ICE counts: stepping onto it starts a
        deterministic slide (see slide()), and leaving it out sealed every
        ice map -- Ice Path's 1F read as an 81-cell dead end with "582
        walkable cells NOT reachable", and Mahogany Gym as four rows."""
        grid = self.grid(const_name)
        if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
            return False
        c = grid[y][x]
        return c in WALKABLE or c in WARPS or c in HOPS or c in ICE or \
            (self.surf and c in WATER)

    def slide(self, const_name, x, y, d):
        """Deterministic ice-slide resolution: step in direction `d` from
        (x,y); while the entered cell is COLL_ICE the slide continues in
        that direction until a non-ice walkable/warp/pit cell (stops ON
        it) or a wall/ledge/map edge (stays on the last ice cell). Pure
        function of (grid, entry cell, direction) -- precompute-friendly,
        replaces savestate-BFS for routine ice crossings. Boulder objects
        are NOT modeled; block their cells via avoid/set_cell."""
        grid = self.grid(const_name)
        hgt, wid = len(grid), len(grid[0])
        dx, dy = STEP[d]
        cx, cy = x, y
        while True:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < wid and 0 <= ny < hgt):
                return cx, cy
            nc = grid[ny][nx]
            if nc in ICE:
                cx, cy = nx, ny
                continue
            if nc in WALKABLE or nc in WARPS or nc == COLL_PIT:
                return nx, ny          # step on and stop
            return cx, cy              # wall/ledge: blocked, stay put

    def set_cell(self, const_name, x, y, coll):
        """Live-state patch: force a cell's collision byte over the
        decoded DEFAULT blockdata -- changeblock doors once opened,
        boulder positions, cut trees. The original byte is remembered so
        clear_cell/clear_overrides can restore it."""
        grid = self.grid(const_name)
        key = (const_name, x, y)
        if key not in self._cell_overrides:
            self._cell_overrides[key] = grid[y][x]
        grid[y][x] = coll
        self.__dict__.setdefault("_region_cache", {}).pop(const_name, None)

    def clear_cell(self, const_name, x, y):
        orig = self._cell_overrides.pop((const_name, x, y), None)
        if orig is not None:
            self.grid(const_name)[y][x] = orig
            self.__dict__.setdefault("_region_cache", {}).pop(const_name, None)

    def clear_overrides(self, const_name=None):
        """Restore every patched cell (optionally only one map's)."""
        for cn, x, y in [k for k in self._cell_overrides
                         if const_name in (None, k[0])]:
            self.clear_cell(cn, x, y)

    def region_map(self, const_name):
        """Connected components of the static passable grid: (ids, count).
        ids[y][x] = component id >= 0 for passable cells, -1 for walls and
        warp-EVENT tiles (you cannot walk THROUGH a live warp -- stepping
        on fires it; warp-collision tiles WITHOUT an event are plain
        floor). Passability is optimistic: grass, ice, water, ledge lips
        count, a ledge hop merges its two sides, and REMOVABLE obstacles
        (cut/headbutt trees, whirlpools, waterfalls -- cleared live via
        set_cell) do not split. Components therefore only split at
        permanent architecture -- the disconnected-floor case where warps
        are the sole link between areas of one map (Sprout Tower floors)."""
        cache = self.__dict__.setdefault("_region_cache", {})
        if const_name in cache:
            return cache[const_name]
        grid = self.grid(const_name)
        hgt, wid = len(grid), len(grid[0])
        events = self.warps.get(const_name, {})

        def passable(x, y):
            if (x, y) in events:
                return False
            c = grid[y][x]
            return c in WALKABLE or c in HOPS or c in ICE or c in WATER \
                or c in DYNAMIC or c in WARPS

        ids = [[-1] * wid for _ in range(hgt)]
        count = 0
        for sy in range(hgt):
            for sx in range(wid):
                if ids[sy][sx] != -1 or not passable(sx, sy):
                    continue
                q = deque([(sx, sy)])
                ids[sy][sx] = count
                while q:
                    x, y = q.popleft()
                    nbrs = [(x + dx, y + dy) for dx, dy in STEP.values()]
                    # ledge hops jump the cliff tile: merge lip and landing
                    # (undirected on purpose -- optimistic like WATER above)
                    for d, (dx, dy) in STEP.items():
                        if grid[y][x] in HOPS and HOPS[grid[y][x]] == d:
                            nbrs.append((x + 2 * dx, y + 2 * dy))
                        lx, ly = x - 2 * dx, y - 2 * dy
                        if 0 <= lx < wid and 0 <= ly < hgt and \
                                grid[ly][lx] in HOPS and HOPS[grid[ly][lx]] == d:
                            nbrs.append((lx, ly))
                    for nx, ny in nbrs:
                        if 0 <= nx < wid and 0 <= ny < hgt and \
                                ids[ny][nx] == -1 and passable(nx, ny):
                            ids[ny][nx] = count
                            q.append((nx, ny))
                count += 1
        cache[const_name] = (ids, count)
        return cache[const_name]

    def regions_at(self, const_name, x, y):
        """Region ids reachable standing at (x,y): the cell's own component,
        or -- for warp tiles / non-passable landing cells -- the components
        you can step off into. Sorted tuple; empty when sealed all around."""
        ids, _ = self.region_map(const_name)
        hgt, wid = len(ids), len(ids[0])
        if not (0 <= x < wid and 0 <= y < hgt):
            return ()
        if ids[y][x] >= 0:
            return (ids[y][x],)
        return tuple(sorted({ids[y + dy][x + dx]
                             for dx, dy in STEP.values()
                             if 0 <= x + dx < wid and 0 <= y + dy < hgt
                             and ids[y + dy][x + dx] >= 0}))

    def plan_route(self, edges, start_map, start, goal_map, goal=None):
        """Region-aware map-level plan over data/mapgraph.json edges:
        BFS on (map, region) nodes using each edge's from_regions /
        to_regions (written by scripts/build_mapgraph.py), so a warp on a
        disconnected part of the current map is never planned -- Sprout
        Tower's 2F->3F stairs are unreachable from the 2F east arrival
        area; the real route detours over the 1F walkway. Returns the
        ordered list of edge dicts to take ([] when already there), or
        None. `goal` (x,y) pins the goal region (needed when goal_map is
        itself multi-region); omitted = any region of goal_map. Edges
        missing region fields are treated permissively (wildcard)."""
        adj = {}
        for e in edges:
            if e.get("routable"):
                adj.setdefault(e["from_map"], []).append(e)
        for lst in adj.values():
            lst.sort(key=lambda e: (e["to_map"], e["kind"], str(e["cells"])))

        goal_regions = None if goal is None else \
            set(self.regions_at(goal_map, *goal))

        def done(m, r):
            return m == goal_map and (
                goal_regions is None or r is None or r in goal_regions)

        starts = [(start_map, r)
                  for r in self.regions_at(start_map, *start)]
        prev = {s: None for s in starts}
        q = deque(starts)
        while q:
            node = q.popleft()
            m, r = node
            if done(m, r):
                legs = []
                while prev[node]:
                    node, e = prev[node]
                    legs.append(e)
                return legs[::-1]
            for e in adj.get(m, ()):
                frm = e.get("from_regions")
                if frm is not None and r is not None and r not in frm:
                    continue
                tos = e.get("to_regions")
                for nr in ([None] if tos is None else tos):
                    nxt = (e["to_map"], nr)
                    if nxt not in prev:
                        prev[nxt] = (node, e)
                        q.append(nxt)
        return None

    def _warp_landing(self, const_name, edge):
        """Where stepping onto warp cell `edge` on `const_name` puts you:
        (dest_const, (x, y)) -- the destination's own warp event of that id.
        Back-warps (id -1) and dangling ids are not routable."""
        dest, wid = self.warps[const_name][edge]
        cells = self.warp_cells.get(dest)
        if not cells or wid > len(cells) or cells[wid - 1] is None:
            return None
        return dest, cells[wid - 1]

    def _conn_landing(self, const_name, d, x, y):
        """Walking off map `const_name` in direction `d` from edge cell
        (x,y): (dest_const, (x, y)) on the connected map, per
        EnterMapConnection (new coord = strip offset + old coord)."""
        dest, off = self.conns[const_name][d]
        _, _, w, h = self.consts[dest]
        W, H = 2 * w, 2 * h
        if d == "north":
            nx, ny = x - 2 * off, H - 1
        elif d == "south":
            nx, ny = x - 2 * off, 0
        elif d == "west":
            nx, ny = W - 1, y - 2 * off
        else:  # east
            nx, ny = 0, y - 2 * off
        if self._enterable(dest, nx, ny):
            return dest, (nx, ny)
        return None

    def find_path(self, const_name, start, goal, avoid=(), cross=False):
        """BFS from start to goal (both (x,y) on `const_name`); returns
        'R','L','U','D' moves. Handles one-way ledge hops (landing cell is
        2 steps away). `avoid` is extra temporarily-blocked cells on the
        START map (e.g. an NPC standing in the way).

        Warp-event tiles can never be stood on, so they are only ever a
        path's FINAL cell unless cross=True, which additionally routes
        across warps (to their landing cell) and map-edge connections --
        i.e. whole-journey routing between maps; see find_route."""
        return self._bfs((const_name, tuple(start)), (const_name, tuple(goal)),
                         const_name, avoid, cross)

    def find_route(self, start_map, start, goal_map, goal, avoid=()):
        """Cross-map find_path: routes between any two maps via warp events
        and edge connections (`avoid`: blocked cells on the START map).
        Plain moves expand before warp/connection exits, so equal-length
        routes prefer staying on one map."""
        return self._bfs((start_map, tuple(start)), (goal_map, tuple(goal)),
                         start_map, avoid, True)

    def _bfs(self, start_state, goal_state, avoid_map, avoid, cross):
        avoid = set(avoid)

        def expand(state):
            M, (x, y) = state
            grid = self.grid(M)
            hgt, wid = len(grid), len(grid[0])
            here = grid[y][x]
            plain, exits = [], []
            for d, (dx, dy) in STEP.items():
                # standing on a ledge lip and moving in its hop direction
                # jumps over the cliff tile, landing 2 cells away (the game
                # checks the tile you stand ON: engine .TryJump)
                if here in HOPS and HOPS[here] == d:
                    lx, ly = x + 2 * dx, y + 2 * dy
                    if M == avoid_map and (lx, ly) in avoid:
                        continue
                    if self._enterable(M, lx, ly):
                        plain.append(((M, (lx, ly)), d))
                    continue
                nx, ny = x + dx, y + dy
                nxt = (M, (nx, ny))
                if 0 <= nx < wid and 0 <= ny < hgt:
                    if M == avoid_map and (nx, ny) in avoid:
                        continue
                    if nxt == goal_state:
                        plain.append((nxt, d))
                        continue
                    w = self.warps.get(M, {}).get((nx, ny))
                    if w is not None:
                        # stepping ONTO a warp tile fires it (arrival never
                        # re-triggers, so landing on a warp tile is fine --
                        # gate doors land on exactly such tiles); you can
                        # never stand on one mid-path otherwise
                        if cross:
                            land = self._warp_landing(M, (nx, ny))
                            if land:
                                exits.append(((land[0], land[1]), d))
                        continue
                    c = grid[ny][nx]
                    if c in WALKABLE or c in WARPS or c in HOPS or \
                            (self.surf and c in WATER):
                        plain.append((nxt, d))
                elif cross and CONN_NAME[d] in self.conns.get(M, {}):
                    land = self._conn_landing(M, CONN_NAME[d], x, y)
                    if land:
                        exits.append(((land[0], land[1]), d))
            return plain + exits

        prev = {start_state: None}
        q = deque([start_state])
        while q:
            cur = q.popleft()
            if cur == goal_state:
                moves = []
                while prev[cur]:
                    pcur, mv = prev[cur]
                    moves.append(mv)
                    cur = pcur
                return moves[::-1]
            for nxt, mv in expand(cur):
                if nxt not in prev:
                    prev[nxt] = (cur, mv)
                    q.append(nxt)
        return None

    def render(self, const_name, mark=None):
        """Debug view: '.' floor, '"' grass, '#' blocked, '~' water,
        '>' warp, 'v<^' ledges, '?' event-conditional (changeblock door/
        stairs -- probe live), '@' the mark."""
        grid = self.grid(const_name)
        cond = self.conditional(const_name)
        out = []
        for y, row in enumerate(grid):
            line = ""
            for x, c in enumerate(row):
                if mark == (x, y):
                    line += "@"
                elif (x, y) in cond:
                    line += "?"
                elif c == 0x00:
                    line += "."
                elif c in (0x14, 0x18):
                    line += '"'
                elif c in WARPS:
                    line += "W"
                elif c in HOPS:
                    line += {"R": ">", "L": "<", "U": "^", "D": "v"}[HOPS[c]]
                elif c == 0x29:
                    line += "~"
                else:
                    line += "#"
            out.append(line)
        return out


# -- map routing, rendering, and scene-script truth -------------------------

# EnterMapConnection: stepping off `letter`'s edge from (x, y) with the
# connection's `off` lands on the destination at -- (dw = dest width in
# cells, dh = dest height). Same math as nav._conn_landing.
_CONN_LAND = {
    "U": lambda dw, dh, off, x, y: (x - 2 * off, dh - 1),
    "D": lambda dw, dh, off, x, y: (x - 2 * off, 0),
    "L": lambda dw, dh, off, x, y: (dw - 1, y - 2 * off),
    "R": lambda dw, dh, off, x, y: (0, y - 2 * off),
}
_CONN_LETTER = {"north": "U", "south": "D", "west": "L", "east": "R"}

_mapgraph_json = None


def mapgraph():
    """data/mapgraph.json (validated warp/connection edges between maps),
    parsed once per process. Side-effect free."""
    global _mapgraph_json
    if _mapgraph_json is None:
        p = Path(__file__).parent.parent / "data" / "mapgraph.json"
        _mapgraph_json = json.loads(p.read_text())
    return _mapgraph_json


# Side-wall collision bytes ($b0-$b7, hi nybble $b0; buoys $c0-$c7 behave
# alike but sit on water). Engine home/map.asm GetMovementPermissions sets
# a BLOCKING bit per facing: each byte refuses entry from specific sides
# only -- e.g. COLL_UP_WALL ($b2) can be entered moving up/left/right but
# never down. Verified live: Route 32 (4,71)->D onto (4,72)$b2 bumps, and
# Union Cave 1F's corridors cross $b2 rows upward. Base nav treats the
# whole family as solid, which sealed Union Cave's lower floor.
_SIDE_WALL_BLOCKED = {
    0xB0: {"L"},           # COLL_RIGHT_WALL
    0xB1: {"R"},           # COLL_LEFT_WALL
    0xB2: {"D"},           # COLL_UP_WALL
    0xB3: {"U"},           # COLL_DOWN_WALL
    0xB4: {"U", "L"},      # COLL_DOWN_RIGHT_WALL
    0xB5: {"U", "R"},      # COLL_DOWN_LEFT_WALL
    0xB6: {"D", "L"},      # COLL_UP_RIGHT_WALL
    0xB7: {"D", "R"},      # COLL_UP_LEFT_WALL
}


# A side-wall tile also blocks LEAVING it across its own wall edge, for any
# terrain -- not just a slide start. Derived from the engine instead of
# guessed: home/map.asm GetMovementPermissions indexes
# .MovementPermissionsData by `collision & 7` for any tile whose hi nybble
# is $b0/$c0 and ORs the entry into wTilePermissions, and
# engine/overworld/player_movement.asm .CheckLandPerms refuses the step
# when `wFacingDirection & wTilePermissions`. The two encodings are
# reversed (DOWN_MASK=1/UP_MASK=2/LEFT_MASK=4/RIGHT_MASK=8 vs
# FACE_DOWN=8/FACE_UP=4/FACE_LEFT=2/FACE_RIGHT=1), so the table's
# LEFT_MASK on $b2 forbids moving *UP* off the tile -- exactly the mirror
# of its entry rule.
#
# Proven live twice, both on plain floor (not ice): ICE_PATH_1F (28,10)
# and VICTORY_ROAD (5,58), where U off the $b2 tile onto $00 floor never
# moves while D and R both do. The old ICE-only narrowing let goto plan
# that step, the engine refused it, the cell was marked live-blocked and
# the walk died in a replan-storm (FUCK_I_MESSED_UP #56, #75).
_SIDE_WALL_EXIT_BLOCKED = {
    b: {{"U": "D", "D": "U", "L": "R", "R": "L"}[d] for d in dirs}
    for b, dirs in _SIDE_WALL_BLOCKED.items()
}


def _tile_kind(b):
    """Terrain word for a collision byte (observe()'s tiles{}). Field
    obstacles ($24 whirlpool, $33 waterfall, $27/$c0-$c7 buoys) and the
    $b0-$b7 side walls get their own words instead of generic 'blocked'
    so the deciding loop knows a bump there is clearable/directional."""
    if b in (0x14, 0x18):
        return "grass"
    if b == 0x00:
        return "floor"
    if b in (0x12, 0x1A):               # COLL_CUT_TREE / COLL_CUT_TREE_1A
        return "cut-tree"               # a wall until someone CUTs it
    if b == 0x15:                       # COLL_HEADBUTT_TREE
        return "headbutt-tree"
    if b == 0x24:                       # COLL_WHIRLPOOL
        return "whirlpool"
    if b == 0x33:                       # COLL_WATERFALL
        return "waterfall"
    if b == 0x93:                       # COLL_PC: the box terminal you
        return "pc"                     # face and press A on (journal #45)
    if b == 0x27 or 0xC0 <= b <= 0xC7:  # COLL_BUOY + water side walls
        return "buoy"
    if b in WATER:
        return "water"
    if b in WARPS:
        return "warp"
    if b in HOPS:
        return "ledge-" + HOPS[b].lower()
    if b in ICE:
        return "ice"
    if b == COLL_PIT:
        return "pit"
    if b in _SIDE_WALL_BLOCKED:
        return "sidewall-" + "".join(
            d for d in "UDLR" if d in _SIDE_WALL_BLOCKED[b]).lower()
    return "blocked"


class TrekNav(MapData):
    """MapData plus directional-wall and live scene-block routing:

    - side walls expand directionally per _SIDE_WALL_BLOCKED;
    - `blocked[map]` cells (live coord_event triggers, refreshed by
      Driver._refresh_nav_blocks) are never routed through -- not even as
      the goal. Without this, BFS happily plans over e.g. Route 32 (18,8),
      where the Cooltrainer scene re-fires forever until Elm's aide's
      Togepi-egg scene flips the map's scene id."""

    def __init__(self, repo):
        super().__init__(repo)
        self.blocked = {}

    def _bfs(self, start_state, goal_state, avoid_map, avoid, cross):
        avoid = set(avoid)

        def blocked(m, x, y):
            return (x, y) in self.blocked.get(m, ())

        def expand(state):
            M, (x, y) = state
            grid = self.grid(M)
            hgt, wid = len(grid), len(grid[0])
            here = grid[y][x]
            plain, exits = [], []
            exit_walls = _SIDE_WALL_EXIT_BLOCKED.get(here, ())
            for d, (dx, dy) in STEP.items():
                if d in exit_walls:
                    continue     # crossing this tile's own wall edge
                # standing on a ledge lip and moving in its hop direction
                # jumps over the cliff tile, landing 2 cells away (the game
                # checks the tile you stand ON: engine .TryJump)
                if here in HOPS and HOPS[here] == d:
                    lx, ly = x + 2 * dx, y + 2 * dy
                    if M == avoid_map and (lx, ly) in avoid:
                        continue
                    if blocked(M, lx, ly):
                        continue
                    if self._enterable(M, lx, ly):
                        plain.append(((M, (lx, ly)), d))
                    continue
                if here in ICE:
                    # sliding floor: direction choice is only free on the
                    # entry cell; the slide carries you to its terminal
                    # cell (deterministic, see MapData.slide)
                    sx, sy = self.slide(M, x, y, d)
                    if (sx, sy) == (x, y):
                        continue
                    nx, ny = sx, sy
                else:
                    nx, ny = x + dx, y + dy
                nxt = (M, (nx, ny))
                if 0 <= nx < wid and 0 <= ny < hgt:
                    if M == avoid_map and (nx, ny) in avoid:
                        continue
                    if blocked(M, nx, ny):
                        continue
                    if nxt == goal_state:
                        plain.append((nxt, d))
                        continue
                    c = grid[ny][nx]
                    w = self.warps.get(M, {}).get((nx, ny))
                    if w is not None and (c in WARPS or c == COLL_PIT):
                        # stepping ONTO a live warp tile fires it (arrival
                        # never re-triggers, so landing on one is fine --
                        # gate doors land on exactly such tiles); you can
                        # never stand on one mid-path otherwise
                        if cross:
                            land = self._warp_landing(M, (nx, ny))
                            if land:
                                exits.append(((land[0], land[1]), d))
                        continue
                    # w is not None but collision says plain floor: DORMANT
                    # leftover warp_event that can never fire (sealed
                    # Burned Tower B1F corridors) -- walk across it
                    if c in WALKABLE or c in WARPS or c in HOPS or \
                            (self.surf and c in WATER):
                        plain.append((nxt, d))
                    elif c in _SIDE_WALL_BLOCKED and \
                            d not in _SIDE_WALL_BLOCKED[c]:
                        # one-way wall: enterable from every facing except
                        # its blocked side(s)
                        plain.append((nxt, d))
                    elif c in ICE:
                        # ice: the step becomes a deterministic slide; the
                        # edge lands on the slide's terminal cell
                        tx, ty = self.slide(M, x, y, d)
                        if (tx, ty) != (x, y) and \
                                not (M == avoid_map and (tx, ty) in avoid) \
                                and not blocked(M, tx, ty):
                            plain.append(((M, (tx, ty)), d))
                elif cross and CONN_NAME[d] in self.conns.get(M, {}):
                    land = self._conn_landing(M, CONN_NAME[d], x, y)
                    if land:
                        exits.append(((land[0], land[1]), d))
            return plain + exits

        prev = {start_state: None}
        q = deque([start_state])
        while q:
            cur = q.popleft()
            if cur == goal_state:
                moves = []
                while prev[cur]:
                    pcur, mv = prev[cur]
                    moves.append(mv)
                    cur = pcur
                return moves[::-1]
            for nxt, mv in expand(cur):
                if nxt not in prev:
                    prev[nxt] = (cur, mv)
                    q.append(nxt)
        return None


# --- ASCII map view ---------------------------------------------------------

_GLYPH_LEGEND = [
    ("@", "player"),
    (".", "floor / walkable"),
    ("%", "tall grass"),
    ("~", "water (surf to cross)"),
    ("O", "warp: door, stairs, ladder"),
    ("^", "one-way ledge hop"),
    ("=", "ice floor (slides)"),
    ("x", "pit"),
    ("!", "sealed or live-blocked cell"),
    ("N", "NPC"),
    ("#", "solid wall"),
    (",", "walkable, NOT reachable from here (needs another entrance)"),
    ("o", "warp in a region you cannot reach from here"),
    (" ", "wall / off-map"),
]


def _cell_glyph(c):
    if c == 0x14:
        return "%"
    if c in WATER:
        return "~"
    if c in WARPS:
        return "O"
    if c in HOPS:
        return "^"
    if c in ICE:
        return "="
    if c == COLL_PIT:
        return "x"
    if c in WALKABLE:
        return "."
    return "#"


def _reach(nav, map_name, start, surf):
    """Cells reachable with plain moves + ledge hops + ice slides; warps
    and map edges are walls for view purposes."""
    grid = nav.grid(map_name)
    hgt, wid = len(grid), len(grid[0])
    blocked = getattr(nav, "blocked", {}).get(map_name, ())
    seen = {tuple(start)}
    q = deque([tuple(start)])
    while q:
        x, y = q.popleft()
        here = grid[y][x]
        for d, (dx, dy) in STEP.items():
            if here in HOPS and HOPS[here] == d:
                nx, ny = x + 2 * dx, y + 2 * dy
            elif here in ICE:
                nx, ny = nav.slide(map_name, x, y, d)
            else:
                nx, ny = x + dx, y + dy
            if not (0 <= nx < wid and 0 <= ny < hgt):
                continue
            if (nx, ny) in seen or (nx, ny) in blocked:
                continue
            if nav._enterable(map_name, nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def render_map_view(nav, map_name, pos, npcs=(), surf=False):
    """ASCII view centred on the region reachable on foot/surf from `pos`,
    cropped to its bounding box (+1). Global coordinates in the rulers so
    the deciding LLM can quote them straight back to goto()/talk_to().

    Cells inside the window that are WALKABLE but belong to another
    connected component draw as `,` (and `o` for their warps) instead of
    blank: a blank used to be indistinguishable from wall, so a whole
    wing of a map -- Rocket base B3F's west half, reachable only from
    B2F's left ladders -- rendered as void and read as "nothing there".
    Components with no cell in the window are named by the `offregion:`
    annotation line instead."""
    grid = nav.grid(map_name)
    hgt, wid = len(grid), len(grid[0])
    reach = _reach(nav, map_name, pos, surf)
    blocked = set(getattr(nav, "blocked", {}).get(map_name, ()))
    npc_set = {tuple(c) for c in npcs}
    xs = [x for x, _ in reach | {tuple(pos)}]
    ys = [y for _, y in reach | {tuple(pos)}]
    ox, oy = max(min(xs) - 1, 0), max(min(ys) - 1, 0)
    ex, ey = min(max(xs) + 1, wid - 1), min(max(ys) + 1, hgt - 1)
    ids, _ = nav.region_map(map_name)
    mine = set(nav.regions_at(map_name, *pos))
    lines = [f"map={map_name} origin=({ox},{oy}) "
             f"pos=({pos[0]},{pos[1]})"]
    # two-row ruler: tens only where they change, units everywhere
    tens_row = "".join(
        str((x // 10) % 10) if (x - ox) % 10 == 0 else " "
        for x in range(ox, ex + 1))
    units_row = "".join(str(x % 10) for x in range(ox, ex + 1))
    lines.append("     " + tens_row)
    lines.append("     " + units_row)
    for y in range(oy, ey + 1):
        row = []
        for x in range(ox, ex + 1):
            if (x, y) == tuple(pos):
                row.append("@")
            elif (x, y) in npc_set:
                row.append("N")
            elif (x, y) in blocked:
                row.append("!")
            elif (x, y) in reach:
                row.append(_cell_glyph(grid[y][x]))
            else:
                row.append(_offregion_glyph(nav, map_name, ids, mine, x, y))
        lines.append(f"{y:4d} " + "".join(row))
    used = {ch for line in lines[3:] for ch in line.split(" ", 1)[-1]}
    legend = [f"  {g} {desc}" for g, desc in _GLYPH_LEGEND
              if g != " " and g in used]
    if legend:
        lines.append("legend:")
        lines += legend
    return "\n".join(lines)


def _offregion_glyph(nav, map_name, ids, mine, x, y):
    """Glyph for a cell outside `_reach`: `o` for a warp that opens onto a
    component the player cannot reach, `,` for that component's walkable
    floor, blank otherwise.

    Only OTHER components are revealed. A cell in the player's own
    component that simply is not walk-reachable (water without SURF, a CUT
    tree) keeps the old blank: it is already named by the annotation block
    when it matters, and drawing it would claim reachability the view
    cannot promise. What was genuinely missing is architecture the player
    can never touch from here -- Rocket base B3F's western wing rendered
    as void, and a session read that as "nothing there"."""
    coll = nav.grid(map_name)[y][x]
    here = ids[y][x]
    if here >= 0:
        return "o" if (coll in WARPS and here not in mine) else \
            ("," if here not in mine else " ")
    # warp-EVENT tiles belong to no component (stepping on one fires it):
    # name them by what they open onto, so a ladder into an unreachable
    # wing shows as `o`. regions_at() answers for ANY cell, so this must
    # be gated on the cell really being a warp -- otherwise every wall
    # beside the wing would draw as a door.
    if coll not in WARPS and (x, y) not in nav.warps.get(map_name, {}):
        return " "
    touches = set(nav.regions_at(map_name, x, y))
    return "o" if touches and not (touches & mine) else " "


_FILE_CONSTS = {}
_shared_nav_md = None


def _file_const(stem):
    """maps/<Camel>.asm file stem -> map CONST (via MapData's pairing);
    falls back to the stem itself when there is no attributes entry."""
    if stem not in _FILE_CONSTS:
        try:
            const = _shared_nav().resolve(stem)
        except Exception:
            const = None
        _FILE_CONSTS[stem] = const or stem
    return _FILE_CONSTS[stem]


def _shared_nav():
    global _shared_nav_md
    if _shared_nav_md is None:
        _shared_nav_md = TrekNav(paths.REPO_ROOT)
    return _shared_nav_md


_DISRUPTIVE_KEYWORDS = ("applymovement", "follow")
_script_body_cache = {}
_script_lines_cache = {}
# The leading guard chain of a coord_event script: `checkevent X` /
# `checkflag X` immediately followed by `iftrue LABEL` / `iffalse LABEL`.
# Those pairs run BEFORE anything happens, so if the live flag sends the
# script to a label that does nothing, the trigger is SPENT and its cell
# is walkable -- which is the whole difference between "the rival ambush
# is armed" and "I already fought him".
_GUARD_CHECK = re.compile(r"^(checkevent|checkflag)\s+([A-Z0-9_]+)\s*$")
_GUARD_JUMP = re.compile(r"^(iftrue|iffalse)\s+([A-Za-z_][\w.]*)\s*$")


def _script_body(repo, camel_file, script_label):
    """The instruction lines of one script label in maps/<Camel>.asm,
    comments and blanks dropped, stopping at the next label. None when
    the file or the label is not there."""
    key = (camel_file, script_label)
    if key not in _script_lines_cache:
        body = None
        p = Path(repo, "maps", f"{camel_file}.asm")
        try:
            lines = p.read_text(errors="replace").splitlines()
            start = None
            for i, line in enumerate(lines):
                if line.split(";")[0].strip().rstrip(":") == script_label \
                        and ":" in line:
                    start = i + 1
                    break
            if start is not None:
                body = []
                for line in lines[start:]:
                    s = line.split(";")[0].strip()
                    if not s:
                        continue
                    if s.endswith(":"):     # next label -> end of body
                        break
                    body.append(s)
        except OSError:
            pass
        _script_lines_cache[key] = body
    return _script_lines_cache[key]


def script_is_disruptive(repo, camel_file, script_label):
    """True if the coord_event's script moves the player or makes an NPC
    follow them (applymovement/follow) -- i.e. it would physically undo
    progress, like Route 32's Cooltrainer push-back. Pure-dialog triggers
    (the Slowpoke Tail pitch) are left routable: walk/goto already flush
    textboxes."""
    key = (camel_file, script_label)
    if key not in _script_body_cache:
        body = _script_body(repo, camel_file, script_label)
        _script_body_cache[key] = (
            False if body is None
            else any(kw in instr for instr in body
                     for kw in _DISRUPTIVE_KEYWORDS))
    return _script_body_cache[key]


_SETSCENE = re.compile(r"^setscene\s+([A-Za-z0-9_]+)")
_SCRIPT_JUMP = re.compile(
    r"^(?:scall|sjump|jump|farsjump|farscall|iftrue|iffalse|ifequal|"
    r"ifnotequal|ifgreater|ifless)\s+(?:[^,]+,\s*)?(\.?[A-Za-z_][\w.]*)\s*$")
# instructions after which control does NOT reach the next label
_SCRIPT_TERMINATORS = ("end", "endall", "endcallback", "sjump", "jump",
                       "farsjump", "returnafterbattle", "halloffame",
                       "reloadmapafterbattle", "warpfacing", "warp")
_scene_advance_cache = {}
_label_order_cache = {}


def _label_order(repo, camel_file):
    """Labels of maps/<Camel>.asm in file order -- needed because scripts
    FALL THROUGH into the label below them (`MeetCopScript2` steps left,
    then walks straight into `MeetCopScript`, then into `CopScript`, which
    is where the setscene lives)."""
    if camel_file not in _label_order_cache:
        out = []
        try:
            for line in Path(repo, "maps",
                             f"{camel_file}.asm").read_text(
                                 errors="replace").splitlines():
                s = line.split(";")[0].rstrip()
                if s.endswith(":") and not s[:1].isspace():
                    out.append(s[:-1].strip())
                elif s.strip().endswith(":") and s.strip().startswith("."):
                    out.append(s.strip()[:-1])
        except OSError:
            pass
        _label_order_cache[camel_file] = out
    return _label_order_cache[camel_file]


def _fallthrough_label(repo, camel_file, script_label, body):
    """The label control reaches when `script_label`'s body just runs off
    its end (no end/jump), or None."""
    if body and body[-1].split()[0] in _SCRIPT_TERMINATORS:
        return None
    labels = _label_order(repo, camel_file)
    try:
        i = labels.index(script_label)
    except ValueError:
        return None
    return labels[i + 1] if i + 1 < len(labels) else None


def script_advances_scene(repo, camel_file, script_label, token, depth=8):
    """True when a coord_event's own script advances the map's scene to a
    DIFFERENT id, i.e. it is a ONE-SHOT cutscene: crossing the cell fires
    it once and the trigger is then gone for good.

    Such a cell must not be a permanent wall. Elm's lab hands you the
    aide's POTION on the way out (SCENE_ELMSLAB_AIDE_GIVES_POTION at
    (4,8)/(5,8), `setscene SCENE_ELMSLAB_NOOP`) and those two cells are
    the lab's ONLY corridor to its door -- blocking them made every fresh
    game's first journey unroutable ("no path from (5,3) to (5,10)"), and
    the officer scene at (4,5)/(5,5) does the same to the corridor back to
    Elm with the egg.

    Two shapes stay blocked, and both are why this is a token comparison
    over the reachable script rather than "contains setscene":
      * Route 32's Cooltrainer push-back never sets a scene at all, so it
        re-fires forever;
      * the Indigo Plateau rival sets the scene back to its OWN id.
    `scall`/`sjump`/`jump`, branch targets AND fallthrough into the next
    label are followed inside the same file (the lab's two setscenes live
    one scall and two fallthroughs away)."""
    key = (camel_file, script_label, token)
    if key in _scene_advance_cache:
        return _scene_advance_cache[key]
    seen, queue, found = set(), [(script_label, 0)], False
    while queue and not found:
        label, d = queue.pop()
        if label in seen or d > depth:
            continue
        seen.add(label)
        body = _script_body(repo, camel_file, label)
        for instr in body or ():
            m = _SETSCENE.match(instr)
            if m:
                if m.group(1) != token:
                    found = True
                    break
                continue
            j = _SCRIPT_JUMP.match(instr)
            if j:
                queue.append((j.group(1), d + 1))
        nxt = _fallthrough_label(repo, camel_file, label, body)
        if nxt:
            queue.append((nxt, d + 1))
    _scene_advance_cache[key] = found
    return found


def script_guards(repo, camel_file, script_label):
    """``[(check, NAME, jump, target), ...]`` for the script's leading
    guard chain, in order:

        PlateauRivalBattle1:
            checkevent EVENT_BEAT_RIVAL_IN_MT_MOON
            iffalse PlateauRivalScriptDone
            checkflag ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT
            iftrue PlateauRivalScriptDone
        -> [('checkevent', 'EVENT_BEAT_RIVAL_IN_MT_MOON', 'iffalse',
             'PlateauRivalScriptDone'),
            ('checkflag', 'ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT', 'iftrue',
             'PlateauRivalScriptDone')]

    Only the uninterrupted prefix of check/jump PAIRS is reported -- the
    part that provably runs before the script does anything -- so a
    caller can evaluate the live flags and find out whether this trigger
    still has teeth. Anything else (readvar/ifequal weekday tests,
    actual instructions) ends the chain."""
    body = _script_body(repo, camel_file, script_label) or []
    out = []
    i = 0
    while i + 1 < len(body):
        chk = _GUARD_CHECK.match(body[i])
        jmp = _GUARD_JUMP.match(body[i + 1])
        if not chk or not jmp:
            break
        out.append((chk.group(1), chk.group(2), jmp.group(1), jmp.group(2)))
        i += 2
    return out


_coord_event_cache = None


def coord_events(repo):
    """{map_const: [(x, y, scene_token, script_label)]} parsed from every
    maps/<Camel>.asm def_coord_events table. Cached per process."""
    global _coord_event_cache
    if _coord_event_cache is not None:
        return _coord_event_cache
    out = {}
    for path in Path(repo, "maps").glob("*.asm"):
        section = None
        for line in path.read_text(errors="replace").splitlines():
            s = line.strip()
            if s.startswith("def_"):
                section = s[4:].rstrip("s")
                continue
            if section != "coord_event" or not s.startswith("coord_event"):
                continue
            args = [a.strip() for a in s[len("coord_event"):].split(",")]
            if len(args) >= 4:
                out.setdefault(_file_const(path.stem), []).append(
                    (int(args[0]), int(args[1]), args[2], args[3]))
    _coord_event_cache = out
    return out


_scene_const_cache = None


def scene_consts(repo):
    """{map_const: [SCENE_* names in declaration order]} from every map's
    def_scene_scripts table (position = runtime scene id, matching the
    scene_const macro's const_def). Cached per process."""
    global _scene_const_cache
    if _scene_const_cache is not None:
        return _scene_const_cache
    out = {}
    for path in Path(repo, "maps").glob("*.asm"):
        names, in_scenes = [], False
        for line in path.read_text(errors="replace").splitlines():
            s = line.strip()
            if s.startswith("def_"):
                in_scenes = s == "def_scene_scripts"
                continue
            if in_scenes and s.startswith("scene_script"):
                names.append(s.split(",")[1].strip() if "," in s else None)
        if names:
            out[_file_const(path.stem)] = names
    _scene_const_cache = out
    return out


_scene_var_cache = None


def scene_vars(repo):
    """{map_const: WRAM symbol} from data/maps/scenes.asm scene_var lines
    (maps absent from that table have no persistent scene id). Cached."""
    global _scene_var_cache
    if _scene_var_cache is None:
        import re as _re
        p = Path(repo, "data", "maps", "scenes.asm")
        out = {}
        for line in p.read_text(errors="replace").splitlines():
            m = _re.match(r"\s*scene_var\s+([A-Z0-9_]+)\s*,\s*([A-Za-z0-9_]+)",
                          line)
            if m:
                out[m.group(1)] = m.group(2)
        _scene_var_cache = out
    return _scene_var_cache
