"""tallow: Ice Path B1F -- push the four boulders into the four pits with a
per-boulder push BFS (state = boulder cell + player cell), then drop through
a pit to B2F Mahogany side.

    .venv/bin/python scripts/tallow_boulders.py saves/tallow.state
"""
import logging, sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, save_clean, STEP_OF, PIT_COLL

WALL = 0x07
d = boot(sys.argv[1])
e = d.emu
import json
ASSIGN = {tuple(map(int, k.split(","))): tuple(v) for k, v in json.loads(sys.argv[2]).items()} if len(sys.argv) > 2 else None
log.info("map %s assignment %s", d.map_name(), ASSIGN)
grid = d.live_grid()
H, W = len(grid), len(grid[0])
pits = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == PIT_COLL}
log.info("pits %s", sorted(pits))


npcs = set()
BLOCKS = {}
npcs |= set(d.find_tiles('warp'))     # stairs are not floor: walking over one changes floors


def free(c, boulders):
    x, y = c
    return 0 <= x < W and 0 <= y < H and grid[y][x] != WALL and c not in boulders and c not in npcs


def walk_cells(start, boulders):
    """Player-reachable cells (pits are holes: never step on them)."""
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in STEP_OF.values():
            n = (x + dx, y + dy)
            if n not in seen and free(n, boulders) and n not in pits:
                seen.add(n); q.append(n)
    return seen


def plan(boulder, player, others, goal):
    """Sequence of (stand_cell, dir) pushes taking `boulder` onto `goal`."""
    start = (boulder, player)
    prev = {start: None}
    q = deque([start])
    while q:
        b, p = q.popleft()
        if b == goal:
            path = []
            s = (b, p)
            while prev[s]:
                s, push = prev[s]
                path.append(push)
            return path[::-1]
        reach = walk_cells(p, others | {b})
        for mv, (dx, dy) in STEP_OF.items():
            stand = (b[0] - dx, b[1] - dy)
            dest = (b[0] + dx, b[1] + dy)
            if stand in reach and free(dest, others) and (dest not in pits or dest == goal):
                nb = dest if dest != goal else goal
                st = (nb, b)                      # player steps into the boulder's old cell
                if st not in prev:
                    prev[st] = ((b, p), (stand, mv))
                    q.append(st)
    return None


def walk_to(cell, boulders, tries=16):
    for _ in range(tries):
        if d.pos()[2:] == cell:
            return
        _walk_to(cell, boulders)
    assert d.pos()[2:] == cell, (d.pos()[2:], cell)


def _walk_to(cell, boulders):
    """BFS walk that never touches a boulder or a pit; ice cells slide."""
    def ok(c):
        return free(c, boulders) and c not in pits

    def move(pos, mv):
        dx, dy = STEP_OF[mv]
        n = (pos[0] + dx, pos[1] + dy)
        if not ok(n):
            return pos
        while grid[n[1]][n[0]] == 0x23 and ok((n[0] + dx, n[1] + dy)):
            n = (n[0] + dx, n[1] + dy)
        return n

    start = d.pos()[2:]
    prev = {start: None}
    q = deque([start])
    while q and cell not in prev:
        cur = q.popleft()
        for mv in STEP_OF:
            n = move(cur, mv)
            if n != cur and n not in prev:
                prev[n] = (cur, mv); q.append(n)
    assert cell in prev, f"walk_to: {start} -> {cell} unreachable"
    path, c = [], cell
    while prev[c]:
        c, mv = prev[c]; path.append(mv)
    log.info("  walk %s -> %s: %s", start, cell, "".join(path[::-1]))
    for mv in path[::-1]:
        before = d.pos()[2:]
        r = d.step_dir(mv); d.settle()
        if d.battle() or r == "battle":
            d.fight(); d.settle()
            return
        if r != "moved":
            log.info("    step %s from %s -> %s (%s) now %s textbox=%s menu=%s", mv, before, r,
                     d.last_step_reason, d.pos()[2:], d.textbox(), d.menu_open())
            log.info("%s", "\n".join(d.emu.screen_text()[-6:]))
            fought = False
            for _ in range(6):                     # a trainer walking up to us lands a few frames later
                settle_dialog(d); d.emu.tick(30); d.settle()
                if d.battle():
                    d.fight(); fought = True
                    d.settle(max_frames=600); settle_dialog(d); d.close_menus(); d.emu.tick(60)
                    break
            settle_dialog(d); d.close_menus()
            if not fought and not d.textbox() and not d.menu_open() and d.pos()[2:] == before:
                dx, dy = STEP_OF[mv]
                tgt = (before[0] + dx, before[1] + dy)
                if BLOCKS.get(tgt, 0) >= 1:
                    npcs.add(tgt)                                  # blocked twice: learn the wall
                BLOCKS[tgt] = BLOCKS.get(tgt, 0) + 1
            return


def push(mv, expect, cur_before=None):
    key = {"U": "up", "D": "down", "L": "left", "R": "right"}[mv]
    for hold in (28, 36, 44, 60, 90):
        if hold == 60:                       # maybe STRENGTH never got armed: face it and ask again
            d.press(f"{mv}:8 .:10 A:6 .:40"); settle_dialog(d); d.close_menus()
        e.py.button_press(key); e.tick(hold); e.py.button_release(key); e.tick(24)
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight()
        b = {(sp["map_x"], sp["map_y"]) for sp in d.sprites() if sp.get("movement") == 25}
        if expect in b or (cur_before is not None and cur_before not in b):
            return
    raise SystemExit(f"push {mv}: boulder not at {expect}; boulders {sorted(b)}")


# STRENGTH once per map entry: face the nearest boulder and press A
objs = [(o["x"], o["y"]) for o in d.map_objects() if o["sprite"] == "SPRITE_BOULDER" and not o.get("masked")]
live = {(sp["map_x"], sp["map_y"]) for sp in d.sprites() if sp.get("movement") == 25}
objs = sorted(live) if len(live) >= len(objs) else sorted(set(objs) | live)
IGNORE = {tuple(c) for c in json.loads(sys.argv[3])} if len(sys.argv) > 3 else set()
objs = [c for c in objs if c not in IGNORE]
npcs |= {(o["x"], o["y"]) for o in d.map_objects() if o["sprite"] != "SPRITE_BOULDER" and not o.get("masked")}
log.info("boulders (map objects) %s", objs)
boulders = set(objs)
remaining_pits = set(pits)
activated = False
log.info("repel: %s %s", d.use_item("REPEL"), d.last_item_reason)
while boulders:
    picked = None
    for b in sorted(boulders, key=lambda c: abs(c[0] - d.pos()[2]) + abs(c[1] - d.pos()[3])):
        if ASSIGN is not None and b not in ASSIGN:
            continue
        goals = [ASSIGN[b]] if ASSIGN is not None else sorted(remaining_pits)
        for p in goals:
            pl = plan(b, d.pos()[2:], boulders - {b}, p)
            if pl is not None and (picked is None or len(pl) < len(picked[2])):
                picked = (b, p, pl)
    if not picked and ASSIGN is not None and not any(b in ASSIGN for b in boulders):
        break
    assert picked, f"no boulder can reach a pit from here: boulders {sorted(boulders)} pits {sorted(remaining_pits)}"
    b, goal, pl = picked
    log.info("boulder %s -> pit %s in %d pushes", b, goal, len(pl))
    cur = b
    for stand, mv in pl:
        walk_to(stand, boulders)
        if not activated:
            d.press(f"{mv}:8 .:10 A:6 .:40"); settle_dialog(d); d.close_menus()
            activated = True
        dx, dy = STEP_OF[mv]
        nxt = (cur[0] + dx, cur[1] + dy)
        push(mv, nxt, cur)
        boulders.discard(cur)
        if nxt not in pits:
            boulders.add(nxt)
        cur = nxt
        log.info("  %s pushed %s -> %s (me %s)", b, stand, cur, d.pos()[2:])
    remaining_pits.discard(goal)
    log.info("boulder %s dropped into %s", b, goal)
    save_clean(d)
log.info("all boulders down")
save_clean(d, f"tallow-pits-{d.map_name().lower()}.state")
