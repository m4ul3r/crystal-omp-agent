#!/usr/bin/env python
"""Throwaway: where on Underwater1 can the player surface?

`Driver.dive()` refuses when the STANDING cell's behaviour is in
`nav.NO_SURFACING` = {0x19, 0x2A} (nav.py:70). The save is banked at
Underwater1 (10,33), and both the leg's `to_open_air` and `fly_to`'s own
dive() failed there -- so that cell is presumably a no-surfacing ceiling.
Find the nearest cell that is NOT, so the leg can walk there and surface.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent import nav as nav_mod  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()

m = d.map_name()
here = d.pos()
print("map", m, "pos", here, "can_dive", d.can_dive(), "underwater", d.underwater())
cell = d.nav.cell(m, *here)
print("standing cell:", cell)
print("NO_SURFACING:", sorted(hex(b) for b in nav_mod.NO_SURFACING))
print("standing behaviour in NO_SURFACING?",
      cell.behavior in nav_mod.NO_SURFACING if cell else "no-cell")

g = d.nav.grid(m)
H, W = len(g), len(g[0])
print("grid", W, "x", H)

# BFS over walkable underwater cells for the nearest surfacable one.
DIRS = ((0, -1), (0, 1), (-1, 0), (1, 0))


def walkable(x, y):
    if not (0 <= x < W and 0 <= y < H):
        return False
    return g[y][x].collision == 0


seen = {here}
q = deque([(here, 0)])
found = []
while q:
    (x, y), dist = q.popleft()
    c = g[y][x]
    if c.behavior not in nav_mod.NO_SURFACING and c.collision == 0:
        found.append(((x, y), dist, hex(c.behavior)))
        if len(found) >= 12:
            break
    for dx, dy in DIRS:
        t = (x + dx, y + dy)
        if walkable(*t) and t not in seen:
            seen.add(t)
            q.append((t, dist + 1))

print("reachable underwater cells:", len(seen))
print("nearest surfacable candidates (cell, dist, behaviour):")
for f in found:
    print("   ", f)
