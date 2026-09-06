#!/usr/bin/env python
"""Throwaway: name the EXACT leg that cannot be driven, with coordinates.

Two measured facts:
  * one held leg builds the counter -- "DOWN:60" from 2F (11,2) reached
    (11,11) at speed 4, crossing cracks at (11,5) (11,6) (11,10);
  * a turn cannot keep it -- swapping the held key straight to LEFT with no
    released frame dropped speed 4 -> 1 and moved a single tile.

So every ride is "from rest, one direction, until a wall or a chosen
release". For each floor, enumerate the rest cells reachable under that rule
and report which cracked tiles could never be entered at counter 2, i.e. the
tiles that seal the floor.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

CRACK = 0xD2
HOLE = 0x66
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()


def analyse(m, start, goals):
    g = d.nav.grid(m)
    H, W = len(g), len(g[0])

    def cell(x, y):
        return g[y][x] if 0 <= x < W and 0 <= y < H else None

    def open_(x, y):
        c = cell(x, y)
        return c is not None and c.collision == 0 and c.behavior != HOLE

    def crack(x, y):
        c = cell(x, y)
        return c is not None and c.collision == 0 and c.behavior == CRACK

    def solid(x, y):
        return open_(x, y) and not crack(x, y)

    # Rest cells reachable by walks + single-direction rides from rest.
    rest = {start}
    q = deque([start])
    crossed = set()
    while q:
        x, y = q.popleft()
        for k, (dx, dy) in DIRS.items():
            t = (x + dx, y + dy)
            if solid(*t) and t not in rest:
                rest.add(t)
                q.append(t)
        for k, (dx, dy) in DIRS.items():
            cx, cy, tiles = x, y, 0
            while True:
                nx, ny = cx + dx, cy + dy
                if not open_(nx, ny):
                    break
                if crack(nx, ny) and min(tiles, 2) != 2:
                    break
                if crack(nx, ny):
                    crossed.add((nx, ny))
                tiles += 1
                cx, cy = nx, ny
                if solid(cx, cy) and (cx, cy) not in rest:
                    rest.add((cx, cy))
                    q.append((cx, cy))

    allcracks = {(x, y) for y in range(H) for x in range(W) if crack(x, y)}
    print(f"=== {m} from {start}")
    print(f"    rest cells reachable: {len(rest)}")
    print(f"    cracked tiles crossable at speed 4: {len(crossed)}/{len(allcracks)}")
    print(f"    NEVER crossable: {sorted(allcracks - crossed)}")
    for gname, gxy in goals.items():
        print(f"    goal {gname} {gxy}: reachable={gxy in rest}")
    return rest


analyse("SkyPillar_2F", (10, 2),
        {"stairs-approach (3,2)": (3, 2), "stairs (3,1)": (3, 1)})
analyse("SkyPillar_4F", (11, 1),
        {"corridor rest (8,4)": (8, 4), "corridor rest (5,4)": (5, 4),
         "west strip (3,1)": (3, 1)})
