#!/usr/bin/env python
"""Throwaway: which Route133->Route134 seam ROW reaches the dive tiles?"""
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver  # noqa: E402

d = Driver(sys.argv[1])
d.nav.surfing = True
PUSH = {0x50: (1, 0), 0x51: (-1, 0), 0x52: (0, -1), 0x53: (0, 1)}
GATES = {(60, 30), (61, 30), (59, 31), (60, 31), (61, 31), (62, 31),
         (59, 32), (60, 32), (61, 32), (62, 32)}


def mk(M):
    grid = d.nav.grid(M)
    H = len(grid)
    W = len(grid[0])

    def free(x, y):
        return 0 <= x < W and 0 <= y < H and not grid[y][x].collision

    def slide(x, y):
        for _ in range(W + H):
            p = PUSH.get(grid[y][x].behavior)
            if p is None:
                return (x, y)
            nx, ny = x + p[0], y + p[1]
            if not free(nx, ny):
                return (x, y)
            x, y = nx, ny
        return (x, y)

    def reach(starts):
        seen = set()
        q = deque()
        for s in starts:
            if not free(*s):
                continue
            land = slide(*s)
            if land not in seen:
                seen.add(land)
                q.append(land)
        while q:
            cx, cy = q.popleft()
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = cx + dx, cy + dy
                if not free(nx, ny):
                    continue
                land = slide(nx, ny)
                if land in seen:
                    continue
                seen.add(land)
                q.append(land)
        return seen
    return W, H, free, slide, reach


W4, H4, free4, slide4, reach4 = mk("Route134")
good = []
for y in range(H4):
    if not free4(W4 - 1, y):
        continue
    if GATES & reach4([(W4 - 1, y)]):
        good.append(y)
print("Route134 entry rows at x=%d that reach the dive tiles: %s"
      % (W4 - 1, good))
print("  (60,7) component reaches gates:", bool(GATES & reach4([(60, 7)])))

W3, H3, free3, slide3, reach3 = mk("Route133")
comp3 = reach3([(W3 - 1, y) for y in range(H3)])
usable = [y for y in good if (0, y) in comp3]
print("Route133 rest cells reachable from its east seam: %d" % len(comp3))
print("Route133 x=0 rest cells reachable:", sorted(y for x, y in comp3 if x == 0))
print("USABLE seam rows (Route133 (0,y) reachable AND Route134 (79,y) works):",
      usable)
# also: cells adjacent to x=0 whose westward step crosses the seam
adj = sorted(y for x, y in comp3 if x == 1)
print("Route133 x=1 rest cells reachable:", adj)
