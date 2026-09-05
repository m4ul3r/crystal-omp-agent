#!/usr/bin/env python
"""Check a slide-floor movement model against the emulator, transition by transition.

A wrong movement model has cost this project days, so this does not ask whether
the model looks plausible -- it walks the real floor and compares every single
landing the game produces against what the model claims.

Model under test: a step onto an arrow tile slides you along, the tile you are
STANDING ON choosing the direction each square, until you stop on a tile that
is not an arrow.
"""
import argparse
import logging
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("slide")

#: Forced movement, from pret/include/constants/metatile_behaviors.h.
#: MB_WALK_* (0x40-0x43) step you one tile; MB_SLIDE_* (0x44-0x47) carry you.
#: Both pick the direction the same way, so the planner treats them alike.
ARROWS = {0x40: (1, 0), 0x41: (-1, 0), 0x42: (0, -1), 0x43: (0, 1),
          0x44: (1, 0), 0x45: (-1, 0), 0x46: (0, -1), 0x47: (0, 1)}
#: Water currents carry a surfer exactly like a slide carries a walker.
#: MB_UNUSED_EASTWARD_CURRENT/WESTWARD/NORTHWARD/SOUTHWARD, 0x50-0x53
#: (pret/include/constants/metatile_behaviors.h:84-87). Seafloor Cavern Room6
#: is built entirely from them, which is why nav -- reading them as plain
#: water -- planned a route the engine refused twelve times at (14,16).
ARROWS.update({0x50: (1, 0), 0x51: (-1, 0), 0x52: (0, -1), 0x53: (0, 1)})
#: MB_TRICK_HOUSE_PUZZLE_8_FLOOR. Not a stopping tile: a slide crossing one
#: keeps its current direction. This single tile is why (2,22) LEFT lands on
#: (8,17) -- the run read that jump as a teleport and built a whole savestate
#: search around not understanding it.
PASS_THROUGH = 0x48
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


def model(grid, pos, mv, limit=80, blocked=()):
    """Where the model says a step lands, or None if the step is a wall."""
    dx, dy = DIRS[mv]
    x, y = pos[0] + dx, pos[1] + dy
    if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
        return None
    cell = grid[y][x]
    if cell is None or cell.collision or (x, y) in blocked:
        return None
    step = (dx, dy)
    for _ in range(limit):
        cell = grid[y][x]
        beh = cell.behavior if cell else None
        if beh != PASS_THROUGH:
            step = ARROWS.get(beh)
        if step is None:
            return (x, y)
        nx, ny = x + step[0], y + step[1]
        nxt = grid[ny][nx] if 0 <= ny < len(grid) and 0 <= nx < len(grid[0]) else None
        if nxt is None or nxt.collision or (nx, ny) in blocked:
            return (x, y)
        x, y = nx, ny
    return (x, y)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--map", default="MossdeepCity_Gym")
    ap.add_argument("--nodes", type=int, default=60)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    # THE LIVE MAP, not the shipped one. `grid_drift` compares collision and
    # elevation only, so a switch that merely re-points an arrow is invisible
    # to it -- and re-pointing arrows is the entire puzzle here.
    live = d.live_grid()
    static = d.nav.grid(a.map)
    grid = [[live.get((x, y), static[y][x]) for x in range(len(static[0]))]
            for y in range(len(static))]
    root = ".slide-root.state"
    d.save(root)

    seen = {d.pos()}
    queue = deque([(d.pos(), root)])
    agree = disagree = walls = 0
    examples = []
    while queue and agree + disagree < a.nodes:
        pos, statefile = queue.popleft()
        for mv in "URDL":
            d.load(statefile)
            if d.pos() != pos:
                continue
            d.step_dir(mv)
            # WAIT FOR THE SLIDE TO END. The engine owns input while the
            # player is being carried (`gPlayerAvatar.preventStep`), so a
            # short settle samples a position mid-flight and the next press is
            # swallowed -- which reads exactly like a wall. Every slide-floor
            # measurement this run made was taken that way.
            last = None
            for _ in range(40):
                d.settle(8)
                now = d.pos()
                if now == last:
                    break
                last = now
            if d.map_name() != a.map:  # walked out of the room
                continue
            landed = d.pos()
            blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                       if not o.get("player")}
            claim = model(grid, pos, mv, blocked=blocked)
            if landed == pos:
                walls += 1
                if claim is not None and claim != pos:
                    disagree += 1
                    examples.append((pos, mv, "wall", claim))
                continue
            if claim == landed:
                agree += 1
            else:
                disagree += 1
                examples.append((pos, mv, landed, claim))
            if landed not in seen:
                seen.add(landed)
                child = f".slide-{len(seen)}.state"
                d.save(child)
                queue.append((landed, child))

    log.info("agree %d | disagree %d | refused %d | cells seen %d",
             agree, disagree, walls, len(seen))
    for pos, mv, landed, claim in examples[:12]:
        log.info("  MISMATCH %s %s -> game %s, model %s", pos, mv, landed, claim)
    return 0 if disagree == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
