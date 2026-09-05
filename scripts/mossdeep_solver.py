#!/usr/bin/env python
"""Solve Mossdeep's gym floor offline, then walk the answer.

The floor is 173 forced-movement tiles. Four switches each re-point exactly
ONE of them (`pret/data/maps/MossdeepCity_Gym/scripts.inc`), so the room has 16
shapes and the puzzle is choosing which one to stand in.

Nothing here is guessed. The movement model was validated against the emulator
transition by transition -- 91 of 92 landings, the one disagreement a trainer's
sight line -- by `scripts/slide_probe.py`. The three things that model has to
get right, each of which cost this run a session:

* `MB_WALK_*` (0x40-0x43) and `MB_SLIDE_*` (0x44-0x47) both force movement.
* `MB_TRICK_HOUSE_PUZZLE_8_FLOOR` (0x48) does NOT stop you -- a slide crossing
  one keeps its direction. That tile alone is why stepping LEFT at (2,22) lands
  you on (8,17), which the run had read as a teleport.
* NPCs block. The "walls" at (2,24) and (9,17) are gym trainers standing there.
"""
import argparse
import logging
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("mossdeep")

GYM = "MossdeepCity_Gym"
LEADERS = (8, 4)  # standing here faces Tate at (8,3); Liza is (9,3)

ARROWS = {0x40: (1, 0), 0x41: (-1, 0), 0x42: (0, -1), 0x43: (0, 1),
          0x44: (1, 0), 0x45: (-1, 0), 0x46: (0, -1), 0x47: (0, 1)}
PASS_THROUGH = 0x48
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}

#: switch cell -> (cell it re-points, behaviour it becomes when SET).
#: `setmetatile` lines from the gym's own scripts.inc; RedArrow_Right/Left/Up
#: resolve to MB_WALK_EAST/WEST/NORTH through the tileset attributes.
SWITCHES = [
    ((2, 7), (5, 5), 0x40),    # FLAG_MOSSDEEP_GYM_SWITCH_1
    ((8, 10), (8, 14), 0x40),  # FLAG_MOSSDEEP_GYM_SWITCH_2
    ((17, 15), (15, 17), 0x41),  # FLAG_MOSSDEEP_GYM_SWITCH_3
    ((5, 24), (1, 23), 0x42),  # FLAG_MOSSDEEP_GYM_SWITCH_4
]


def behaviour(base, cfg, cell):
    """The tile's behaviour in switch configuration `cfg`."""
    for i, (_sw, target, beh) in enumerate(SWITCHES):
        if target == cell and cfg >> i & 1:
            return beh
    got = base.get(cell)
    return got.behavior if got else None


def landing(base, blocked, cfg, pos, mv, limit=80):
    """Where a step ends, or None when the game would refuse it."""
    dx, dy = DIRS[mv]
    x, y = pos[0] + dx, pos[1] + dy
    cell = base.get((x, y))
    if cell is None or cell.collision or (x, y) in blocked:
        return None
    step = (dx, dy)
    for _ in range(limit):
        beh = behaviour(base, cfg, (x, y))
        if beh != PASS_THROUGH:
            step = ARROWS.get(beh)
        if step is None:
            return (x, y)
        nx, ny = x + step[0], y + step[1]
        nxt = base.get((nx, ny))
        if nxt is None or nxt.collision or (nx, ny) in blocked:
            return (x, y)
        x, y = nx, ny
    return (x, y)


def solve(base, blocked, start, cfg0, goal):
    """Shortest sequence of steps and switch presses that reaches `goal`."""
    start_state = (start, cfg0)
    seen = {start_state}
    queue = deque([(start_state, [])])
    while queue:
        (pos, cfg), path = queue.popleft()
        if pos == goal:
            return path
        moves = [(mv, landing(base, blocked, cfg, pos, mv)) for mv in "URDL"]
        # A sign is read from the tile beneath it, facing up.
        for i, (sw, _t, _b) in enumerate(SWITCHES):
            if pos == (sw[0], sw[1] + 1):
                moves.append((f"press{i + 1}", pos))
        for mv, dest in moves:
            if dest is None or dest == pos and not mv.startswith("press"):
                continue
            ncfg = cfg ^ (1 << (int(mv[-1]) - 1)) if mv.startswith("press") else cfg
            state = (dest, ncfg)
            if state in seen:
                continue
            seen.add(state)
            queue.append((state, path + [mv]))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    if d.map_name() != GYM:
        log.info("FAIL: not in the gym (at %s)", d.map_name())
        return 1
    base = d.live_grid()
    blocked = {(o["x"], o["y"]) for o in d.live_npcs() if not o.get("player")}
    cfg0 = sum(1 << i for i, f in enumerate(d.switch_signature()) if f)
    log.info("at %s | switches %s | npcs %s", d.pos(), d.switch_signature(),
             sorted(blocked))

    plan = solve(base, blocked, d.pos(), cfg0, LEADERS)
    if plan is None:
        log.info("FAIL: no route to %s in any switch configuration", LEADERS)
        return 1
    log.info("PLAN (%d moves): %s", len(plan), " ".join(plan))
    if a.plan_only:
        return 0

    # RE-PLAN ON DRIFT. Trainers wander and a battle can shove the player, so
    # the plan is re-solved from wherever the game actually put us rather than
    # replayed blind -- the whole point of a solver this fast is that asking
    # again is free.
    for attempt in range(12):
        if d.pos() == LEADERS:
            break
        blocked = {(o["x"], o["y"]) for o in d.live_npcs() if not o.get("player")}
        cfg0 = sum(1 << i for i, f in enumerate(d.switch_signature()) if f)
        plan = solve(base, blocked, d.pos(), cfg0, LEADERS)
        if plan is None:
            log.info("FAIL: no route from %s (switches %s)", d.pos(),
                     d.switch_signature())
            return 1
        log.info("plan %d: %d moves from %s", attempt, len(plan), d.pos())
        for i, mv in enumerate(plan):
            if mv.startswith("press"):
                sw = SWITCHES[int(mv[-1]) - 1][0]
                before = d.switch_signature()
                d.emu.run_sequence("U:4 .:20")
                for _ in range(3):
                    d.emu.run_sequence("A:4 .:40")
                    d.advance_scene(30000)
                    if d.switch_signature() != before:
                        break
                log.info("  %2d %-7s %s -> %s", i, mv, before,
                         d.switch_signature())
                if d.switch_signature() == before:
                    log.info("FAIL: switch %s did not flip", sw)
                    return 1
                cfg0 = sum(1 << j for j, f in enumerate(d.switch_signature())
                           if f)
                continue
            want = landing(base, blocked, cfg0, d.pos(), mv)
            d.step_dir(mv)
            d.settle(60)
            d.advance_scene(20000)
            if d.in_battle():
                d.fight()
                d.advance_scene(40000)
            if d.map_name() != GYM:
                log.info("FAIL: left the gym at move %d", i)
                return 1
            if d.pos() != want:
                log.info("  %2d %-7s -> %s (wanted %s) REPLAN", i, mv, d.pos(),
                         want)
                break
            log.info("  %2d %-7s -> %s", i, mv, d.pos())

    log.info("ARRIVED %s", d.pos())
    if a.out:
        d.save(a.out)
    return 0 if d.pos() == LEADERS else 1


if __name__ == "__main__":
    raise SystemExit(main())
