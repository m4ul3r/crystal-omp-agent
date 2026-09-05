#!/usr/bin/env python
"""Reset Seafloor Cavern Room8 and cross it, then step into Room9.

Room8 puts a boulder on (5,3), the only approach to its door at (5,2), and a
boulder cannot be pushed onto a warp -- so the room is solvable only from a
clean arrival, in 21 moves. Any half-executed plan boxes the player in behind
its own pushes, which is exactly where the run ended up: (5,4) with boulders on
(4,4), (5,3) and (6,4).

    Room8 -> (5,12) warp -> Room3 -> cross to (8,2) -> (8,1) warp -> Room8
    cross to (5,3) -> (5,2) warp -> Room9
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from boulder_solver import walk as boulder_walk, _BELIEF  # noqa: E402

log = logging.getLogger("room8reset")


def hop(d, target, door, tries=10) -> bool:
    _BELIEF.clear()
    here = d.map_name()
    if d.pos() != target and not boulder_walk(d, target, tries=tries):
        log.info("could not stand on %s in %s (at %s)", target, here, d.pos())
        return False
    if not d.take_warp(*door) and d.map_name() == here:
        log.info("%s door %s refused: %s", here, door, d.last_warp_reason)
        return False
    log.info("%s -> %s %s", here, d.map_name(), d.pos())
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s", d.map_name(), d.pos())

    if d.map_name() == "SeafloorCavern_Room8":
        if not hop(d, (5, 12), (5, 12)):
            return 1
    if d.map_name() == "SeafloorCavern_Room3":
        if not hop(d, (8, 2), (8, 1)):
            return 1
    if d.map_name() != "SeafloorCavern_Room8":
        log.info("FAIL: expected Room8, in %s", d.map_name())
        return 1
    if not hop(d, (5, 3), (5, 2)):
        return 1

    log.info("RESULT in %s %s", d.map_name(), d.pos())
    if a.out:
        d.save(a.out)
    return 0 if d.map_name() == "SeafloorCavern_Room9" else 1


if __name__ == "__main__":
    raise SystemExit(main())
