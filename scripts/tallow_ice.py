"""tallow: Mahogany Gym ice rink -- BFS over slides, execute, talk to Pryce.

    .venv/bin/python scripts/tallow_ice.py saves/tallow.state [goal_x goal_y]
"""
import logging, sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, save_clean, STEP_OF, matchup_policy

d = boot(sys.argv[1])
goal = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (5, 4)
ICE, WALL = 0x23, 0x07
grid = d.nav.grid(d.map_name())
H, W = len(grid), len(grid[0])
npcs = set(d.npc_cells())


def blocked(x, y):
    return not (0 <= x < W and 0 <= y < H) or grid[y][x] == WALL or (x, y) in npcs


def slide(pos, mv):
    """Where a step in `mv` ends: walk one cell, then keep going while on ice."""
    dx, dy = STEP_OF[mv]
    x, y = pos
    nx, ny = x + dx, y + dy
    if blocked(nx, ny):
        return pos
    x, y = nx, ny
    while grid[y][x] == ICE:
        nx, ny = x + dx, y + dy
        if blocked(nx, ny):
            break
        x, y = nx, ny
    return (x, y)


start = d.pos()[2:]
prev = {start: None}
q = deque([start])
while q:
    cur = q.popleft()
    if cur == goal:
        break
    for mv in "UDLR":
        nxt = slide(cur, mv)
        if nxt != cur and nxt not in prev:
            prev[nxt] = (cur, mv)
            q.append(nxt)
if goal not in prev:
    raise SystemExit(f"no slide path {start} -> {goal}")
path = []
c = goal
while prev[c]:
    c, mv = prev[c]
    path.append(mv)
path.reverse()
log.info("slide path %s -> %s: %s", start, goal, "".join(path))
for mv in path:
    before = d.pos()[2:]
    d.step_dir(mv)
    d.settle()
    settle_dialog(d)
    if d.battle():
        d.fight()
    log.info("  %s: %s -> %s", mv, before, d.pos()[2:])
assert d.pos()[2:] == goal, (d.pos()[2:], goal)
if goal != (5, 4):
    save_clean(d)
    raise SystemExit(0)
d.default_policy = matchup_policy(d, {"SEEL": "CRUMB", "DEWGONG": "CRUST", "PILOSWINE": "EMBER"})
save_clean(d, "tallow-pre-pryce.state")
d.talk_to(5, 3, facing="U"); settle_dialog(d)
for _ in range(4):
    if d.battle():
        d.fight()
    d.settle(); settle_dialog(d)
obs = d.observe()
log.info("after Pryce: badges=%s party=%s", obs["badges"],
         [(m["nick"], m["level"], m["hp"]) for m in obs["party"]])
log.info("last battle:\n%s", d.last_battle.summary() if d.last_battle else None)
save_clean(d)
