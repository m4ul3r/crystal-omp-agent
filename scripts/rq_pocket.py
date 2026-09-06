#!/usr/bin/env python
"""Throwaway: which 3F landing cells can walk to the (7,1) staircase?

The fall lands at the SAME (x,y) one floor down. 3F has zero cracked tiles,
so reachability there is plain walking. Question: from each candidate 4F
fall tile, does the 3F landing cell reach 3F (7,1) -- the warp back up to
4F (7,1), the western strip that holds (3,1) -> 5F?
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq_plan import Pillar  # noqa: E402

p = Pillar()
DIRS = (("U", (0, -1)), ("D", (0, 1)), ("L", (-1, 0)), ("R", (1, 0)))


def walk_region(m, start):
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for _d, (dx, dy) in DIRS:
            t = (x + dx, y + dy)
            if p.solid(m, *t) and t not in seen:
                seen.add(t)
                q.append(t)
    return seen


# 3F regions
r_from_3 = walk_region("SkyPillar_3F", (3, 1))   # arrive from 2F
print("3F region from (3,1):", len(r_from_3), "contains (7,1)?", (7, 1) in r_from_3,
      "contains (11,1)?", (11, 1) in r_from_3)

for f in ((4, 4), (6, 4), (7, 4), (9, 4)):
    if not p.solid("SkyPillar_3F", *f):
        print("3F", f, "not solid")
        continue
    reg = walk_region("SkyPillar_3F", f)
    print("3F landing", f, "region", len(reg),
          "-> (7,1)?", (7, 1) in reg, "-> (11,1)?", (11, 1) in reg,
          "-> (3,1)?", (3, 1) in reg)

# 4F: region reachable from the 7,1 entrance (the western strip)
r4_7 = walk_region("SkyPillar_4F", (7, 1))
print()
print("4F region from (7,1):", len(r4_7), "contains (3,1)?", (3, 1) in r4_7)
r4_11 = walk_region("SkyPillar_4F", (11, 1))
print("4F walk-region from (11,1):", len(r4_11), "contains (3,1)?", (3, 1) in r4_11)
