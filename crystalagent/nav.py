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
WARPS = set(range(0x70, 0x80))         # doors, stairs, carpets, ladders, caves
HOPS = {0xA0: "R", 0xA1: "L", 0xA2: "U", 0xA3: "D"}  # one-way ledges
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

        self._coll_cache = {}
        self._grid_cache = {}

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

    def find_path(self, const_name, start, goal, avoid=()):
        """BFS from start to goal (both (x,y)); returns 'R','L','U','D' moves.
        Handles one-way ledge hops (landing cell is 2 steps away). `avoid` is
        extra temporarily-blocked cells (e.g. an NPC standing in the way)."""
        grid = self.grid(const_name)
        hgt, wid = len(grid), len(grid[0])
        avoid = set(avoid)

        def ok(x, y):
            return 0 <= x < wid and 0 <= y < hgt and (x, y) not in avoid

        def enterable(x, y):
            c = grid[y][x]
            return c in WALKABLE or c in WARPS or c in HOPS

        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                moves = []
                while prev[cur]:
                    pcur, mv = prev[cur]
                    moves.append(mv)
                    cur = pcur
                return moves[::-1]
            x, y = cur
            here = grid[y][x]
            for d, (dx, dy) in STEP.items():
                # standing on a ledge lip and moving in its hop direction
                # jumps over the cliff tile, landing 2 cells away (the game
                # checks the tile you stand ON: engine .TryJump)
                if here in HOPS and HOPS[here] == d:
                    lx, ly = x + 2 * dx, y + 2 * dy
                    if ok(lx, ly) and enterable(lx, ly) and (lx, ly) not in prev:
                        prev[(lx, ly)] = (cur, d)
                        q.append((lx, ly))
                    continue
                nx, ny = x + dx, y + dy
                if ok(nx, ny) and enterable(nx, ny) and (nx, ny) not in prev:
                    prev[(nx, ny)] = (cur, d)
                    q.append((nx, ny))
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
