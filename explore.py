#!/usr/bin/env python3
"""Empirical explorer: BFS over actually-walkable cells using live movement."""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from trek import Driver

DIRS = {"U": "up", "D": "down", "L": "left", "R": "right"}
DELTA = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
BUTTONS = ("up", "down", "left", "right", "a", "b", "start", "select")


def release_all(e):
    for b in BUTTONS:
        e.py.button_release(b)
    e.tick(3)


def step(d, mv, frames=22):
    e = d.emu
    release_all(e)
    before = d.pos()
    if d.battle():
        return "battle"
    e.py.button_press(DIRS[mv])
    e.tick(frames)
    e.py.button_release(DIRS[mv])
    e.tick(18)
    now = d.pos()
    if now[:2] != before[:2]:
        return "warp"
    if now != before:
        return "moved"
    return "blocked"


def main():
    goal_y = int(sys.argv[1]) if len(sys.argv) > 1 else 44
    d = Driver()
    start = d.pos()[2:]
    seen = {start}
    q = deque([start])
    parent = {}
    while q:
        cur = q.popleft()
        if cur[1] <= goal_y:
            print("REACHED", cur, flush=True)
            # walk back the found path? just stop here
            return
        for mv, (dx, dy) in DELTA.items():
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in seen or not (0 <= nxt[0] < 40):
                continue
            # teleport-free check: BFS requires being AT cur; emulate by walking
            r = step(d, mv)
            if r == "warp":
                print(f"  warp {cur} -{mv}-> {d.pos()} {d.map_name()}", flush=True)
                return
            if r == "battle":
                d.fight()
                continue
            if r == "moved":
                now = d.pos()[2:]
                seen.add(now)
                parent[now] = (cur, mv)
                if now == nxt:
                    q.append(nxt)
                    print(f"  open {cur} -{mv}-> {now}", flush=True)
    print("explored:", len(seen), sorted(seen))


if __name__ == "__main__":
    main()
