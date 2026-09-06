#!/usr/bin/env python
"""Throwaway: plan crossings where EVERY leg starts from a standstill.

`Emulator.run_sequence` clears every key before each step (emu.py:342), so a
multi-leg string cannot hold a turn: each leg begins at counter 0. Confirmed
empirically -- "DOWN:60" alone crossed 2F's whole column 11 including five
cracked tiles, while "DOWN:60 LEFT:40 ..." fell, because the LEFT leg
restarted from rest with only 40 frames (6 tiles) and stopped ON (5,13).

So the constraint is: a leg may only enter a cracked tile as its THIRD tile
or later, and must not END on a cracked tile. Search for routes obeying that,
per floor, on the live grid.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

CRACK = 0xD2
HOLE = 0x66
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
FRAMES = (16, 8, 4)

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()


def make(m):
    g = d.nav.grid(m)
    H, W = len(g), len(g[0])

    def cell(x, y):
        if not (0 <= x < W and 0 <= y < H):
            return None
        return g[y][x]

    def open_(x, y):
        c = cell(x, y)
        return c is not None and c.collision == 0 and c.behavior != HOLE

    def crack(x, y):
        c = cell(x, y)
        return c is not None and c.collision == 0 and c.behavior == CRACK

    def solid(x, y):
        return open_(x, y) and not crack(x, y)

    return open_, crack, solid


def legs_from(m, x, y):
    """Every legal single-leg ride from (x,y): one held press, from rest.

    Yields (dir, tiles, end, frames). A leg is legal when every cracked tile
    it enters is its third tile or later, and it does not stop on a crack.
    """
    open_, crack, solid = make(m)
    out = []
    for k, (dx, dy) in DIRS.items():
        cx, cy = x, y
        tiles = 0
        frames = 0
        while True:
            nx, ny = cx + dx, cy + dy
            if not open_(nx, ny):
                break                      # wall: the ride stops here
            counter = min(tiles, 2)
            if crack(nx, ny) and counter != 2:
                break                      # would fall entering it
            tiles += 1
            frames += FRAMES[counter]
            cx, cy = nx, ny
            if solid(cx, cy):
                out.append((k, tiles, (cx, cy), frames))
    return out


def plan(m, start, goal):
    """BFS over rest cells using single-leg rides plus plain walks."""
    open_, crack, solid = make(m)
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for k, (dx, dy) in DIRS.items():
            t = (cur[0] + dx, cur[1] + dy)
            if solid(*t) and t not in prev:
                prev[t] = (cur, ("walk", k, 1, t, 0))
                q.append(t)
        for k, tiles, end, frames in legs_from(m, *cur):
            if end not in prev:
                prev[end] = (cur, ("ride", k, tiles, end, frames))
                q.append(end)
    if goal not in prev:
        return None
    out = []
    cur = goal
    while prev[cur]:
        at, op = prev[cur]
        out.append(op)
        cur = at
    return out[::-1]


for m, a, b in (
    ("SkyPillar_2F", (10, 2), (3, 2)),
    ("SkyPillar_2F", (10, 1), (3, 2)),
    ("SkyPillar_4F", (11, 1), (8, 4)),
    ("SkyPillar_4F", (11, 1), (5, 4)),
):
    print("===", m, a, "->", b)
    p = plan(m, a, b)
    if p is None:
        print("   NO ROUTE")
        continue
    for kind, k, tiles, end, frames in p:
        if kind == "walk":
            print(f"   walk  {k}         -> {end}")
        else:
            print(f"   ride  {k} x{tiles:<2d} {frames:3d}f -> {end}")
