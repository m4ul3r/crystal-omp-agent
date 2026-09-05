"""Exit Ilex Forest: BFS over the static grid treating the freshly-cut
tree cell as walkable, then execute the path with live verification.
"""
import sys
from collections import deque
sys.path.insert(0, ".")
from trek import Driver
from crystalagent.nav import WALKABLE, WARPS, HOPS

GOAL = (1, 5)
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
CUT_OK = {(8, 25)}          # tree we cut this session
def bfs(grid):
    start = None
    h, w = len(grid), len(grid[0])

    def passable(x, y):
        if not (0 <= x < w and 0 <= y < h):
            return False
        c = grid[y][x]
        if isinstance(c, str):
            try:
                c = int(c, 16)
            except ValueError:
                return False
        return (c in WALKABLE or c in WARPS or c in HOPS
                or (x, y) in CUT_OK)

    # find nearest floor cell to current pos later; BFS from goal backwards
    dist = {GOAL: 0}
    q = deque([GOAL])
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS.values():
            nx, ny = x + dx, y + dy
            if (nx, ny) not in dist and passable(nx, ny):
                dist[(nx, ny)] = dist[(x, y)] + 1
                q.append((nx, ny))
    return dist


def main():
    d = Driver("saves/omp_speed_run.state")
    md = d.nav
    grid = md.grid("ILEX_FOREST")
    dist = bfs(grid)

    if "ILEX_FOREST" == d.map_name():
        d.goto(8, 26)
        d.use_cut(8, 25)
        d.settle()
    p = d.pos()[2:]
    print("start", p, "dist-to-goal:", dist.get(p), flush=True)
    steps = 0
    while d.map_name() == "ILEX_FOREST" and p != GOAL and steps < 250:
        best = None
        for mv, (dx, dy) in DIRS.items():
            n = (p[0] + dx, p[1] + dy)
            if n in dist and (best is None or dist[n] < dist[best]):
                best = n
        if best is None or dist.get(p) is None:
            print("off-model at", p)
            break
        mv = [k for k, v in DIRS.items()
              if (p[0] + v[0], p[1] + v[1]) == best][0]
        r = d.step_dir(mv)
        if r == "battle":
            d.fight()
            continue
        np = d.pos()[2:]
        if np == p:
            # live block disagrees with model: sidestep once
            for mv2 in DIRS:
                if mv2 == mv:
                    continue
                r2 = d.step_dir(mv2)
                if r2 in ("moved", "warp"):
                    break
        p = d.pos()[2:]
        steps += 1

    print("at", d.pos(), d.map_name())
    if d.map_name() == "ILEX_FOREST" and d.pos()[2:] == GOAL:
        d._step("U")
        d.settle()
        print("exit ->", d.map_name(), d.pos())
    if d.map_name() != "ILEX_FOREST":
        d.save()
        print("[saved]")


if __name__ == "__main__":
    main()
