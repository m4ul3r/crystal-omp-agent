"""Walkability grids and pathfinding, built from the disassembly's own data:
maps/<Name>.blk (block indices), data/tilesets/*_collision.asm (2x2 collision
cells per block), constants/map_constants.asm (sizes), data/maps/maps.asm
(tileset per map), data/maps/attributes.asm (CamelCase <-> CONST_NAME).

Coordinates match wXCoord/wYCoord (one cell = one walking step).
"""

import re
from collections import deque
from pathlib import Path

WALKABLE = {0x00, 0x14, 0x18}          # floor, long grass, tall grass
WATER = {0x29}                          # COLL_WATER: routable when surfing
                                        # (whirlpools/waterfalls stay walls)
WARPS = set(range(0x70, 0x80))         # doors, stairs, carpets, ladders, caves
HOPS = {0xA0: "R", 0xA1: "L", 0xA2: "U", 0xA3: "D"}  # one-way ledges
ICE = {0x23}                            # COLL_ICE: sliding floor
COLL_PIT = 0x60                         # COLL_PIT: fall-through hole
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

        # CamelCase -> CONST_NAME
        self.camel = {}
        for m in re.finditer(r"map_attributes\s+(\w+),\s+(\w+),",
                             (repo / "data/maps/attributes.asm").read_text()):
            self.camel[m.group(2)] = m.group(1)

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
        inv = {camel: const for const, camel in self.camel.items()}
        for camel, const in inv.items():
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

        self._coll_cache = {}
        self._grid_cache = {}
        self._cell_overrides = {}    # {(const, x, y): original collision}

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
        """Collision byte per walkable cell: grid[y][x], 2*w x 2*h cells."""
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
        grid = self.grid(const_name)
        if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
            return False
        c = grid[y][x]
        return c in WALKABLE or c in WARPS or c in HOPS or \
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

    def clear_cell(self, const_name, x, y):
        orig = self._cell_overrides.pop((const_name, x, y), None)
        if orig is not None:
            self.grid(const_name)[y][x] = orig

    def clear_overrides(self, const_name=None):
        """Restore every patched cell (optionally only one map's)."""
        for cn, x, y in [k for k in self._cell_overrides
                         if const_name in (None, k[0])]:
            self.clear_cell(cn, x, y)

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
        '>' warp, 'v<^' ledges, '@' the mark."""
        grid = self.grid(const_name)
        out = []
        for y, row in enumerate(grid):
            line = ""
            for x, c in enumerate(row):
                if mark == (x, y):
                    line += "@"
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
