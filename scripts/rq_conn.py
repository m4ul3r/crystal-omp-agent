#!/usr/bin/env python
"""Throwaway: plain 4-connectivity on the LIVE nav grid, ignoring momentum.

Treat cracked tiles as passable (they are, at mach speed) and ask the pure
topology question per floor: which warps reach which. This separates
"collision blocks it" from "the momentum model in rq_plan.py refuses it".
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402

CRACK = 0xD2
HOLE = 0x66
DIRS = ((0, -1), (0, 1), (-1, 0), (1, 0))

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()


def region(m, start, allow_crack=True):
    g = d.nav.grid(m)
    H, W = len(g), len(g[0])

    def ok(x, y):
        if not (0 <= x < W and 0 <= y < H):
            return False
        c = g[y][x]
        if c.collision != 0:
            return False
        if c.behavior == HOLE:
            return False
        if c.behavior == CRACK and not allow_crack:
            return False
        return True

    if not ok(*start):
        return set()
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS:
            t = (x + dx, y + dy)
            if ok(*t) and t not in seen:
                seen.add(t)
                q.append(t)
    return seen


for m, pts in (
    ("SkyPillar_1F", [(6, 13), (10, 1)]),
    ("SkyPillar_2F", [(10, 1), (3, 1)]),
    ("SkyPillar_3F", [(3, 1), (11, 1), (7, 1)]),
    ("SkyPillar_4F", [(11, 1), (7, 1), (3, 1)]),
    ("SkyPillar_5F", [(3, 1), (10, 1)]),
    ("SkyPillar_Top", [(16, 14), (14, 7)]),
):
    print("===", m)
    for a in pts:
        r = region(m, a)
        rn = region(m, a, allow_crack=False)
        others = [b for b in pts if b != a]
        print(f"  from {a}: crack-ok region {len(r)} reaches "
              f"{[(b, b in r) for b in others]}")
        print(f"            no-crack region {len(rn)} reaches "
              f"{[(b, b in rn) for b in others]}")
