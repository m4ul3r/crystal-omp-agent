#!/usr/bin/env python
"""Throwaway: how is 4F (6,4)/(7,4) ENTERED at mach speed?

`rq_fall.py` asked only about single adjacent steps and found none. But a
cracked tile may be entered mid-RIDE: the run passes over it at callback 2
and keeps going. A fall happens when the tile is entered at ANY speed
(VAR_ICE_STEP_COUNT is zeroed unless GetPlayerSpeed()==4) OR when the run
rests on it. So to fall DELIBERATELY we want the opposite of a clean cross:
enter (6,4)/(7,4) SLOWLY -- on foot, or at callback < 2.

So the real question is just: is (6,4)/(7,4) adjacent to any cell reachable
from 4F (11,1) by ride-or-walk, ignoring the "must be solid" step rule?
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


def reach(start, max_turns=2):
    """Cells reachable by solid walks + legal rides (ride endpoints)."""
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
            for (ex, ey, _c) in cells:          # EVERY tile entered, not just last
                if (ex, ey) not in seen:
                    seen.add((ex, ey))
                    q.append((ex, ey))
    return seen


for start in ((11, 1), (7, 1), (3, 1)):
    r = reach(start)
    print(f"4F reach from {start}: {len(r)} cells")
    for f in ((6, 4), (7, 4)):
        adj = [(f[0] - dx, f[1] - dy) for _d, (dx, dy) in DIRS
               if (f[0] - dx, f[1] - dy) in r]
        print(f"   fall {f} in reach? {f in r} | neighbours in reach: {adj}")
    print("   (3,1) in reach?", (3, 1) in r)
