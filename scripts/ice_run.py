#!/usr/bin/env python
"""Cross Sootopolis Gym's cracked-ice floor to Wallace.

`MB_THIN_ICE` (0x26) cracks the moment you step off it and `MB_CRACKED_ICE`
(0x27) drops you through a `warphole` to Gym_B1F -- which is exactly what a
plain route does: it walks toward Wallace, re-enters a tile it already used,
and falls.

So an ice tile is a SINGLE-USE node. The search is a path over
(position, tiles already cracked); ordinary floor stays reusable, which keeps
the state space small enough to walk.
"""
import argparse
import logging
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("ice")

THIN, CRACKED = 0x26, 0x27
#: MB_SLIDE_* -- the gym draws its closed stairs as forced-movement tiles.
CLOSED_STAIR = frozenset((0x44, 0x45, 0x46, 0x47))
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


def read_floor(d):
    """`(walls, thin, cracked)` from the LIVE map -- the ice changes as we go."""
    name = d.map_name()
    live, static = d.live_grid(), d.nav.grid(name)
    walls, thin, cracked, stairs = set(), set(), set(), set()
    for y in range(len(static)):
        for x in range(len(static[0])):
            c = live.get((x, y)) or static[y][x]
            if c is None or c.collision:
                walls.add((x, y))
                continue
            if c.behavior == THIN:
                thin.add((x, y))
            elif c.behavior == CRACKED:
                cracked.add((x, y))
            elif c.behavior in CLOSED_STAIR:
                stairs.add((x, y))
                # A CLOSED STAIR, not floor. Until the section's threshold is
                # met the way out is MB_SLIDE_SOUTH: it shoves you back and
                # drops you through. Measured live -- three ice steps then
                # north onto (8,16) landed in Gym_B1F with the counter zeroed.
                # Treating it as walkable is what made the router plan a
                # 22-move stroll straight to Wallace, four times.
                walls.add((x, y))
    # THE EDGE IS A WALL. `walls` only ever contained cells INSIDE the grid,
    # so a plain BFS wandered off the map into unbounded empty space and never
    # returned -- which is what "hung after choosing an entry" actually was.
    w, h = len(static[0]), len(static)
    for x in range(-1, w + 1):
        walls.add((x, -1))
        walls.add((x, h))
    for y in range(-1, h + 1):
        walls.add((-1, y))
        walls.add((w, y))
    return walls, frozenset(thin), frozenset(cracked), frozenset(stairs)


def component(thin, start):
    """The ice tiles reachable from `start` through ice."""
    seen, stack = set(), [start]
    while stack:
        c = stack.pop()
        if c in seen or c not in thin:
            continue
        seen.add(c)
        for dx, dy in DIRS.values():
            stack.append((c[0] + dx, c[1] + dy))
    return seen


def cover(ice, start, exits=frozenset(), limit=50_000_000, seconds=8.0):
    """A path from `start` that steps on EVERY tile of its ice section once.

    That is the actual puzzle: `VAR_ICE_STEP_COUNT` gates the three stair sets
    at 8 / 28 / 69 (SootopolisCity_Gym_1F/scripts.inc:37-40), and until the
    threshold is met the way out is an `MB_SLIDE_SOUTH` tile that shoves you
    back and drops you through. Measured live: three ice steps then north onto
    (8,16) put the player in Gym_B1F with the counter zeroed.

    Plain DFS with a most-constrained-first order -- grid Hamiltonian paths
    fall out of it almost immediately.
    """
    todo = component(ice, start)
    if start not in todo:
        return None
    order = []
    seen = {start}
    budget = [limit]
    # A WALL-CLOCK CUTOFF does the bounding; the node budget must be large.
    # At 400,000 the 40-tile section reported "no covering path" while a
    # 39-move answer sits 3 seconds away.
    # Most entries answer
    # instantly, but a 40-tile section with no Hamiltonian path from the tile
    # you asked about will happily explore for half an hour -- which is exactly
    # what wedged two runs while probing all 66 tiles for a workable entry.
    deadline = time.time() + seconds

    def nbrs(c):
        out = []
        for mv, (dx, dy) in DIRS.items():
            n = (c[0] + dx, c[1] + dy)
            if n in todo and n not in seen:
                out.append((mv, n))
        # Fewest onward options first: the classic Warnsdorff ordering.
        out.sort(key=lambda t: sum(
            1 for d2 in DIRS.values()
            if (t[1][0] + d2[0], t[1][1] + d2[1]) in todo
            and (t[1][0] + d2[0], t[1][1] + d2[1]) not in seen))
        return out

    def dfs(c):
        if len(seen) == len(todo):
            # AND FINISH BESIDE THE WAY OUT. Covering the section opens its
            # stairs, but a path that ends deep in the field leaves you ringed
            # by your own holes -- section 1 covered perfectly and stranded the
            # run on (7,18) with the counter already past the threshold.
            return not exits or any(
                (c[0] + dx, c[1] + dy) in exits for dx, dy in DIRS.values())
        if budget[0] <= 0 or time.time() > deadline:
            return False
        budget[0] -= 1
        for mv, n in nbrs(c):
            seen.add(n)
            order.append(mv)
            if dfs(n):
                return True
            order.pop()
            seen.discard(n)
        return False

    return "".join(order) if dfs(start) else None


def solve(walls, thin, cracked, blocked, start, goal, limit=600_000):
    """Shortest move string that never steps on ice twice."""
    seen = {(start, frozenset())}
    queue = deque([(start, frozenset(), "")])
    while queue and len(seen) < limit:
        pos, used, path = queue.popleft()
        if pos == goal:
            return path
        for mv, (dx, dy) in DIRS.items():
            nxt = (pos[0] + dx, pos[1] + dy)
            if nxt in walls or nxt in blocked or nxt in cracked:
                continue
            used2 = used
            if nxt in thin:
                if nxt in used:
                    continue
                used2 = used | {nxt}
            state = (nxt, used2)
            if state in seen:
                continue
            seen.add(state)
            queue.append((nxt, used2, path + mv))
    return None


def floor_path(walls, thin, cracked, blocked, start, goals):
    """Shortest route that stays OFF the ice entirely."""
    block = walls | thin | cracked | blocked
    seen, queue = {start}, deque([(start, "")])
    goals = set(goals)
    while queue:
        pos, path = queue.popleft()
        if pos in goals:
            return path
        for mv, (dx, dy) in DIRS.items():
            n = (pos[0] + dx, pos[1] + dy)
            if n in block or n in seen or not (0 <= n[0] < 512
                                               and 0 <= n[1] < 512):
                continue
            seen.add(n)
            queue.append((n, path + mv))
    return None


def run_path(d, path, here):
    """Walk every move; stop at the first surprise and say what it was."""
    for mv in path:
        before = d.pos()
        d.step_dir(mv)
        d.settle(40)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        if d.map_name() != here:
            return False, f"fell to {d.map_name()}"
        if d.pos() == before:
            return False, f"refused {mv} at {before}: {d.last_step_reason}"
    return True, ""


def reset_floor(d) -> bool:
    """Out of the gym and back in: the ice and the counter both reset."""
    if d.map_name() == "SootopolisCity_Gym_B1F":
        d.reach_cell(11, 22, map_name="SootopolisCity_Gym_B1F",
                     on_battle="fight")
        d.take_warp(11, 22)
        d.close_menus()
    if d.map_name() != "SootopolisCity_Gym_1F":
        return False
    for door in ((8, 25), (9, 25)):
        try:
            d.reach_cell(*door, map_name="SootopolisCity_Gym_1F",
                         on_battle="fight")
        except Exception:  # noqa: BLE001
            pass
        if d.pos() == door and d.take_warp(*door):
            break
    if d.map_name() != "SootopolisCity":
        return False
    d.close_menus()
    try:
        d.goto(31, 33, on_battle="fight")
    except Exception:  # noqa: BLE001
        pass
    d.emu.run_sequence("UP:24 .:40")
    d.advance_scene(60000)
    d.close_menus()
    return d.map_name() == "SootopolisCity_Gym_1F"


def cross_to(d, gx: int = 8, gy: int = 3) -> bool:
    """Cross Sootopolis Gym's cracked-ice floor and stand on (gx, gy).

    Extracted so the autonomous loop can use it. Plain routing cannot walk
    this floor: every thin-ice tile cracks the moment you step off it and
    becomes a hole on the second visit, so an ordinary path falls through to
    B1F halfway to Wallace. The play loop reported exactly that on a loop --
    "walk to Wallace failed (left SootopolisCity_Gym_1F for
    SootopolisCity_Gym_B1F mid-route)" -- and never once started the battle.
    Each section therefore needs a Hamiltonian path entered from a tile that
    has one.
    """
    GYM = "SootopolisCity_Gym_1F"
    for attempt in range(6):
        if not reset_floor(d):
            log.info("could not get back onto a fresh gym floor (%s)",
                     d.map_name())
            return 1
        log.info("attempt %d: fresh floor at %s ice=%s", attempt, d.pos(),
                 d.state.var("VAR_ICE_STEP_COUNT"))
        ok = True
        for section in range(4):
            walls, thin, cracked, stairs = read_floor(d)
            blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                       if not o.get("player")}
            done = floor_path(walls, thin, cracked, blocked, d.pos(),
                              [(gx, gy)])
            if done is not None:
                ok, why = run_path(d, done, GYM)
                log.info("  walked out to Wallace: %s %s", ok, why)
                break
            # Step onto the nearest section from floor, then cover ALL of it in
            # one pass -- a half-covered section can never be finished, because
            # the tiles you already used are holes now.
            # CHOOSE AN ENTRY THAT HAS A COVERING PATH. A Hamiltonian path
            # over a section exists only from some of its tiles -- section 1
            # solves from (8,19) but not (9,19); section 2 from (6,12) or
            # (8,12) and no other; section 3 from (3,6) or (4,6). Stepping on
            # the nearest ice and hoping is how the run kept half-covering a
            # section and then falling.
            plan = None
            for tile in sorted(thin):
                body = cover(thin, tile, exits=stairs)
                if body is None:
                    continue
                stands = [(tile[0] + dx, tile[1] + dy)
                          for dx, dy in DIRS.values()
                          if (tile[0] + dx, tile[1] + dy) not in thin
                          and (tile[0] + dx, tile[1] + dy) not in walls]
                approach = floor_path(walls, thin, cracked, blocked, d.pos(),
                                      stands)
                if approach is None:
                    continue
                step_in = next(mv for mv, (dx, dy) in DIRS.items()
                               if (tile[0] - dx, tile[1] - dy) in stands
                               and False) if False else None
                plan = (tile, approach, body)
                break
            if plan is None:
                log.info("  no section with both a covering path and a way in")
                ok = False
                break
            entry, approach, body = plan
            ok, why = run_path(d, approach, GYM)
            if not ok:
                log.info("  approach failed: %s", why)
                break
            first = next((mv for mv, (dx, dy) in DIRS.items()
                          if (d.pos()[0] + dx, d.pos()[1] + dy) == entry), None)
            if first is None:
                log.info("  landed at %s, not beside %s", d.pos(), entry)
                ok = False
                break
            log.info("  section %d: covering %d tiles from %s", section,
                     len(body) + 1, entry)
            ok, why = run_path(d, first + body, GYM)
            log.info("  -> %s ice=%s %s", d.pos(),
                     d.state.var("VAR_ICE_STEP_COUNT"), why)
            if not ok:
                break
        if d.map_name() == GYM and d.pos() == (gx, gy):
            break

    return d.map_name() == GYM and d.pos() == (gx, gy)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--to", default="8,3")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    gx, gy = (int(v) for v in a.to.split(","))
    log.info("START %s %s ice=%s", d.map_name(), d.pos(),
             d.state.var("VAR_ICE_STEP_COUNT"))
    ok = cross_to(d, gx, gy)
    log.info("RESULT %s at %s %s ice=%s", ok, d.map_name(), d.pos(),
             d.state.var("VAR_ICE_STEP_COUNT"))
    if a.out:
        d.save(a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
