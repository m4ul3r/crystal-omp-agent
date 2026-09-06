#!/usr/bin/env python
"""Throwaway: can 4F (11,1) reach the fall tiles (6,4)/(7,4)?

The planner's `plan()` refuses to route THROUGH a fall to a specific goal on
another floor, so ask the narrower question directly: from 4F (11,1), which
cells are reachable by ride/walk, and is a cracked fall tile among the
entered-tile sets of any legal ride?
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq_plan import Pillar, FALL_TO  # noqa: E402

p = Pillar()
M = "SkyPillar_4F"
start = (11, 1)

# Reachable-by-solid-walk set, plus every ride endpoint, from 4F (11,1).
from collections import deque

seen = {start}
q = deque([start])
rides = []
while q:
    x, y = q.popleft()
    for d, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)), ("L", (-1, 0)), ("R", (1, 0))):
        tx, ty = x + dx, y + dy
        if p.solid(M, tx, ty) and (tx, ty) not in seen:
            seen.add((tx, ty))
            q.append((tx, ty))
    for legs in p._ride_shapes(M, x, y, 2):
        cells, ok, why = p.run(M, x, y, legs)
        if not ok or not cells:
            continue
        ex, ey, _c = cells[-1]
        if (ex, ey) not in seen:
            seen.add((ex, ey))
            q.append((ex, ey))
        rides.append(((x, y), legs, cells, why))

print("reachable cells from 4F", start, ":", len(seen))
print(sorted(seen))

# Which reachable cell can step INTO a fall tile (adjacent, cracked)?
FALLS = [(6, 4), (7, 4), (4, 4), (9, 4)]
print()
for f in FALLS:
    if not p.cracked(M, *f):
        continue
    land = (FALL_TO[M], f[0], f[1])
    adj = []
    for d, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)), ("L", (-1, 0)), ("R", (1, 0))):
        n = (f[0] - dx, f[1] - dy)
        if n in seen:
            adj.append((n, d))
    print("fall", f, "lands", land, "open?", p.open(*land),
          "| step-in from:", adj)
