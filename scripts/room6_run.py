#!/usr/bin/env python
"""Cross Seafloor Cavern Room6, which is a WATER CURRENT maze.

Room6 has no boulders and a six-cell landing ledge; everything else is
`MB_*_CURRENT` (0x50-0x53). nav reads those as ordinary water, so it planned a
straight line and the engine refused the same step twelve times at (14,16) --
you cannot swim against a current any more than you can walk against Mossdeep's
arrows.

The transition model is the one already validated against the emulator for the
gym floor (`scripts/slide_probe.py`, 91/92 landings): step onto a forced tile
and you are carried, the tile you are standing on choosing the direction, until
you stop somewhere that does not push.
"""
import argparse
import logging
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from slide_probe import model, DIRS  # noqa: E402

log = logging.getLogger("room6")


def plan(grid, blocked, start, target, limit=20000):
    seen = {start}
    queue = deque([(start, "")])
    while queue and len(seen) < limit:
        pos, path = queue.popleft()
        if pos == target:
            return path
        for mv in "URDL":
            nxt = model(grid, pos, mv, blocked=blocked)
            if nxt is None or nxt == pos or nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + mv))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--to", default="4,2")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    tx, ty = (int(v) for v in a.to.split(","))
    here = d.map_name()
    log.info("START %s %s surfing=%s", here, d.pos(), d.is_surfing())

    # MOUNT FIRST. A bare step from the ledge into water is refused -- the
    # engine wants the A-press prompt -- and `goto` knows how to do that.
    d._surf_sync()
    if not d.is_surfing():
        for spot in ((11, 19), (10, 19), (12, 19)):
            try:
                d.goto(*spot, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if d.is_surfing():
                break
        log.info("mounted: surfing=%s at %s", d.is_surfing(), d.pos())

    live = d.live_grid()
    static = d.nav.grid(here)
    grid = [[live.get((x, y), static[y][x]) for x in range(len(static[0]))]
            for y in range(len(static))]

    for attempt in range(10):
        if d.pos() == (tx, ty):
            break
        blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                   if not o.get("player")}
        path = plan(grid, blocked, d.pos(), (tx, ty))
        if path is None:
            log.info("no current route from %s to (%d,%d)", d.pos(), tx, ty)
            return 1
        log.info("plan %d: %d moves from %s", attempt, len(path), d.pos())
        for mv in path:
            want = model(grid, d.pos(), mv, blocked=blocked)
            d.step_dir(mv)
            d.settle(60)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
            if d.map_name() != here:
                log.info("left the room at %s", d.pos())
                break
            if d.pos() != want:
                log.info("  %s -> %s (wanted %s), replanning", mv, d.pos(),
                         want)
                break

    ok = d.pos() == (tx, ty)
    log.info("RESULT %s at %s %s", ok, d.map_name(), d.pos())
    if a.out:
        d.save(a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
