#!/usr/bin/env python3
"""Live empirical navigator: goal-biased DFS over actually-walkable cells by
moving the player (recursion stack = path, so no replay needed). Usage:

    livenav.py STATE X Y [MAP_CONST]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from trek import Driver

INV = {"D": "U", "U": "D", "L": "R", "R": "L"}
DELTA = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}


def main():
    state = sys.argv[1]
    gx, gy = int(sys.argv[2]), int(sys.argv[3])
    goal_map = sys.argv[4] if len(sys.argv) > 4 else None
    d = Driver(state)

    def p():
        return tuple(d.pos()[2:])

    def m():
        return d.map_name()

    if goal_map is None:
        goal_map = m()
    print("start", (p(), m()), "goal", (gx, gy), goal_map, flush=True)
    seen = set()
    steps = [0]

    class Desync(Exception):
        pass

    def at_goal(pos, mp):
        return (mp == goal_map and pos == (gx, gy)) or \
               (mp == goal_map and pos[0] == gx and abs(pos[1] - gy) <= 2)

    def ensure(cur):
        if p() != cur:
            raise Desync()

    def dfs():
        cur = p()
        seen.add((cur, m()))
        blocked = set(d.npc_cells())
        order = sorted(("D", "L", "R", "U"),
                       key=lambda mv: abs(cur[0] + DELTA[mv][0] - gx)
                       + abs(cur[1] + DELTA[mv][1] - gy))
        for mv in order:
            if d.battle():
                d.fight()
                d.settle()
                ensure(cur)
            nxt = (cur[0] + DELTA[mv][0], cur[1] + DELTA[mv][1])
            if nxt in blocked:
                continue
            before, bm = p(), m()
            d._step(mv)
            d.settle()
            after, am = p(), m()
            if d.battle():
                d.fight()
                d.settle()
                after, am = p(), m()
            if am != bm:
                if at_goal(after, am):
                    print("GOAL via warp", after, am, flush=True)
                    return True
                for _ in range(8):
                    d._step(INV[mv])
                    d.settle()
                    if m() == bm:
                        break
                ensure(cur)
                continue
            if after == before:
                continue
            steps[0] += 1
            if steps[0] % 25 == 0:
                print(steps[0], "cells, at", after, am, flush=True)
            if at_goal(after, am):
                print("GOAL", after, am, flush=True)
                return True
            if (after, am) in seen:
                d._step(INV[mv])
                d.settle()
                ensure(cur)
                continue
            if dfs():
                return True
            d._step(INV[mv])
            d.settle()
            if p() != cur:
                for _ in range(5):
                    d._step(INV[mv])
                    d.settle()
                    if p() == cur:
                        break
            ensure(cur)
        return False

    ok = False
    for restart in range(8):
        try:
            ok = dfs()
            break
        except Desync as e:
            print("restart after desync at", p(), m(), flush=True)
            seen = set()
    print("done", ok, p(), m(), "explored", len(seen), flush=True)
    if ok:
        d.save()
        print("saved")


if __name__ == "__main__":
    main()
