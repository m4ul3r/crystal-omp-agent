#!/usr/bin/env python
"""Throwaway: is there a SOLID cell to stand on beside 4F (6,4)/(7,4)?

A deliberate fall needs a cell we can come to REST on, adjacent to the
cracked tile, then one slow step in. `rq_entry.py` counted mid-ride tiles,
which inflates reachability: a cracked tile crossed at speed is not a place
to stand.
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

print("row y=4:", [(x, p.behavior(M, x, 4), "solid" if p.solid(M, x, 4)
                   else ("crack" if p.cracked(M, x, 4) else "wall"))
                   for x in range(14)])
print("row y=3:", [(x, "solid" if p.solid(M, x, 3)
                   else ("crack" if p.cracked(M, x, 3) else "wall"))
                   for x in range(14)])
print("row y=5:", [(x, "solid" if p.solid(M, x, 5)
                   else ("crack" if p.cracked(M, x, 5) else "wall"))
                   for x in range(14)])


def rest_reach(start, max_turns=2):
    """Cells we can come to REST on: solid walks + ride ENDPOINTS only."""
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


r = rest_reach((11, 1))
print()
print("4F restable from (11,1):", len(r))
for f in ((6, 4), (7, 4)):
    adj = [((f[0] - dx, f[1] - dy), d) for d, (dx, dy) in DIRS
           if (f[0] - dx, f[1] - dy) in r and p.solid(M, f[0] - dx, f[1] - dy)]
    print(f"  fall {f}: standable neighbours -> {adj}")
for c in ((5, 4), (8, 4), (5, 3), (8, 3), (6, 3), (7, 3), (6, 5), (7, 5)):
    print("   cell", c, "solid?", p.solid(M, *c), "restable-reach?", c in r)
