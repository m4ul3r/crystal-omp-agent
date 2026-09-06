#!/usr/bin/env python
"""Throwaway: the y=4 corridor on 4F, and which side each region owns.

Row y=4 is  x: 2 3 [4] 5 [6][7] 8 [9] 11 12 13   ([]=cracked, 10=wall)
with y=3/y=5 walled across x=4..9, so x=2..9 is a 1-tall corridor. The
question is which 4F region can START a run into it, and where a clean
crossing ENDS.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq_plan import Pillar  # noqa: E402

p = Pillar()
M = "SkyPillar_4F"
DIRS = (("U", (0, -1)), ("D", (0, 1)), ("L", (-1, 0)), ("R", (1, 0)))


def rest_reach(start, max_turns=2):
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for _d, (dx, dy) in DIRS:
            t = (x + dx, y + dy)
            if p.solid(M, *t) and t not in seen:
                seen.add(t)
                q.append(t)
        for legs in p._ride_shapes(M, x, y, max_turns):
            cells, ok, _why = p.run(M, x, y, legs)
            if not ok or not cells:
                continue
            ex, ey, _c = cells[-1]
            if p.solid(M, ex, ey) and (ex, ey) not in seen:
                seen.add((ex, ey))
                q.append((ex, ey))
    return seen


west = rest_reach((7, 1))
east = rest_reach((11, 1))
print("west region (from 7,1):", len(west), sorted(west))
print("east region (from 11,1):", len(east))
print()
for c in ((2, 4), (3, 4), (5, 4), (8, 4), (11, 4)):
    print("corridor cell", c, "solid", p.solid(M, *c),
          "| west?", c in west, "east?", c in east)

# Try every continuous run that starts in the west region and heads into the
# corridor, to see where it can legally end.
print()
for start in sorted(west):
    for legs in p._ride_shapes(M, *start, 2):
        cells, ok, why = p.run(M, start[0], start[1], legs)
        if not ok or not cells:
            continue
        ex, ey, _c = cells[-1]
        crossed = [c for c in cells if p.cracked(M, c[0], c[1])]
        if crossed and ey == 4:
            print(f"  RUN from {start} legs={legs} -> ({ex},{ey}) {why}"
                  f" crossed={[(c[0], c[1]) for c in crossed]}")
