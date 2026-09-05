#!/usr/bin/env python
"""Reset Seafloor Cavern Room3 and cross it in one uninterrupted run.

Room3 only solves from its arrival cell. Any save taken part-way through leaves
the run standing on (6,14), from which twelve million states find nothing -- so
the reset and the crossing have to happen without a checkpoint in between.

    Room3 -> (4,15) warp -> Room6 -> cross the currents -> (4,1) warp -> Room3
    arm Strength from a cell that keeps (8,2) solvable
    cross to (8,2) -> warp (8,1) -> Room8
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from boulder_solver import walk as boulder_walk, _BELIEF  # noqa: E402
from cavern_run import _cross_currents  # noqa: E402
from room3_run import arm_strength  # noqa: E402

log = logging.getLogger("room3reset")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s", d.map_name(), d.pos())

    if d.map_name() == "SeafloorCavern_Room3":
        _BELIEF.clear()
        if d.pos() != (4, 15):
            if not boulder_walk(d, (4, 15), tries=8):
                log.info("could not reach the Room6 door at (4,15)")
                return 1
        # Standing on it does not fire it; step off and back on.
        if not d.take_warp(4, 15) and d.map_name() == "SeafloorCavern_Room3":
            log.info("(4,15) refused: %s", d.last_warp_reason)
            return 1
        log.info("out to %s %s", d.map_name(), d.pos())

    if d.map_name() == "SeafloorCavern_Room6":
        _BELIEF.clear()
        if not _cross_currents(d, (4, 2)):
            log.info("could not cross Room6's currents")
            return 1
        if not d.take_warp(4, 1) and d.map_name() == "SeafloorCavern_Room6":
            log.info("(4,1) refused: %s", d.last_warp_reason)
            return 1
        log.info("back in %s %s", d.map_name(), d.pos())

    if d.map_name() != "SeafloorCavern_Room3":
        log.info("FAIL: expected Room3, in %s", d.map_name())
        return 1

    _BELIEF.clear()
    if not arm_strength(d, (8, 2)):
        log.info("FAIL: could not arm Strength without stranding")
        return 1
    if not boulder_walk(d, (8, 2), tries=10):
        log.info("FAIL: could not cross Room3 (at %s)", d.pos())
        return 1
    if not d.take_warp(8, 1) and d.map_name() == "SeafloorCavern_Room3":
        log.info("FAIL: (8,1) refused: %s", d.last_warp_reason)
        return 1

    log.info("RESULT in %s %s", d.map_name(), d.pos())
    if a.out:
        d.save(a.out)
    return 0 if d.map_name() == "SeafloorCavern_Room8" else 1


if __name__ == "__main__":
    raise SystemExit(main())
