#!/usr/bin/env python
"""Throwaway: concrete tile path 4F (11,1) -> the (6,4)/(7,4) fall tiles.

Uses the LIVE nav grid and plain BFS with cracks passable, then reports the
path so the driver can be told exactly which cells to ride through. Also
reports the 3F pocket path (6,4)->(7,1) and the 4F west path (7,1)->(3,1),
both of which are crack-free walks.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402

CRACK = 0xD2
HOLE = 0x66
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()


def bfs(m, start, goal, allow_crack=True):
    g = d.nav.grid(m)
    H, W = len(g), len(g[0])

    def ok(x, y):
        if not (0 <= x < W and 0 <= y < H):
            return False
        c = g[y][x]
        if c.collision != 0 or c.behavior == HOLE:
            return False
        if c.behavior == CRACK and not allow_crack:
            return False
        return True

    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for k, (dx, dy) in DIRS.items():
            t = (cur[0] + dx, cur[1] + dy)
            if ok(*t) and t not in prev:
                prev[t] = (cur, k)
                q.append(t)
    if goal not in prev:
        return None
    out = []
    cur = goal
    while prev[cur]:
        at, k = prev[cur]
        out.append((at, k, cur))
        cur = at
    return out[::-1]


def show(label, m, a, b, allow_crack=True):
    path = bfs(m, a, b, allow_crack)
    if path is None:
        print(f"{label}: NO PATH {a}->{b} (crack_ok={allow_crack})")
        return
    g = d.nav.grid(m)
    moves = "".join(k for _at, k, _to in path)
    cracks = [to for _at, _k, to in path
              if g[to[1]][to[0]].behavior == CRACK]
    print(f"{label}: {a}->{b} len {len(path)} moves {moves}")
    print(f"    cracked tiles entered: {cracks}")


show("4F east -> fall(6,4)", "SkyPillar_4F", (11, 1), (6, 4))
show("4F east -> fall(7,4)", "SkyPillar_4F", (11, 1), (7, 4))
show("3F pocket (6,4)->(7,1)", "SkyPillar_3F", (6, 4), (7, 1), False)
show("3F pocket (7,4)->(7,1)", "SkyPillar_3F", (7, 4), (7, 1), False)
show("4F west (7,1)->(3,1)", "SkyPillar_4F", (7, 1), (3, 1), False)
show("2F (10,1)->(3,1)", "SkyPillar_2F", (10, 1), (3, 1))
show("1F (6,13)->(10,1)", "SkyPillar_1F", (6, 13), (10, 1), False)
show("5F (3,1)->(10,1)", "SkyPillar_5F", (3, 1), (10, 1), False)
show("Top (16,14)->(14,7)", "SkyPillar_Top", (16, 14), (14, 7), False)
