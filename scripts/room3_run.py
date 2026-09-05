#!/usr/bin/env python
"""Cross Seafloor Cavern Room3, where turning Strength ON is itself a move.

Room3 has nine boulders and no rocks. From the arrival cell (4,15) it solves in
23 moves -- but `use_strength` has to walk up to a boulder and press A, and the
obvious approach strands the player at (6,14), from which the room has no
solution at all (12,000,000 states, exhausted).

So the activation is chosen, not stumbled into: try each boulder from each
side, reachable WITHOUT pushing anything, and keep the first one that leaves
the target still solvable.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from boulder_solver import (snapshot, belief, solve, walk as boulder_walk,  # noqa: E402
                            FACE, _BELIEF)

log = logging.getLogger("room3")


def arm_strength(d, target) -> bool:
    """Turn Strength on from a cell that keeps `target` reachable."""
    if d.state.flag("FLAG_SYS_USE_STRENGTH"):
        return True
    here = d.map_name()
    walls, _live, others, rocks, _elev = snapshot(d)
    boulders = belief(d)
    start = d.pos()
    for bx, by in sorted(boulders,
                         key=lambda b: abs(b[0] - start[0]) + abs(b[1] - start[1])):
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            spot = (bx + dx, by + dy)
            cell = d.nav.cell(here, *spot)
            if cell is None or cell.collision or spot in boulders:
                continue
            # Reachable without shoving anything, and still solvable after.
            if solve(walls, boulders, others, start, [spot],
                     may_push=False) is None:
                continue
            if solve(walls, boulders, others, spot, [target],
                     rocks=rocks) is None:
                continue
            if not boulder_walk(d, spot, tries=4):
                continue
            d.emu.run_sequence(f"{FACE[(dx, dy)]}:4 .:20")
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40000)
            if d.choice_open():
                d.resolve_choice("YES")
            d.advance_scene(60000)
            if d.state.flag("FLAG_SYS_USE_STRENGTH"):
                log.info("strength armed at %s facing %s", spot, (bx, by))
                return True
            d.close_menus()
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--to", default="8,2")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    tx, ty = (int(v) for v in a.to.split(","))
    _BELIEF.clear()
    log.info("START %s %s", d.map_name(), d.pos())
    if not arm_strength(d, (tx, ty)):
        log.info("FAIL: could not arm Strength without stranding")
        return 1
    ok = boulder_walk(d, (tx, ty), tries=10)
    log.info("RESULT %s at %s %s", ok, d.map_name(), d.pos())
    if a.out:
        d.save(a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
