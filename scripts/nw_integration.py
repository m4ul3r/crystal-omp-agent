#!/usr/bin/env python
"""Prove `Collector.goto_map("SafariZone_Northwest")` actually arrives.

The quadrant needs the Mach Bike up a muddy slope, and two bugs hid that:

* `goto_map` returned True the moment the gate let us in, while we were
  standing in SOUTHEAST -- so the sweep hunted the wrong quadrant believing
  it was in NW, which is why its seven species stayed missing on runs that
  reported reaching it.
* nothing rode the slope, so even a correct answer had no path.

This drives the real collector entry point, not the standalone script, so a
pass means the SWEEP can get there.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from collect import Collector  # noqa: E402

log = logging.getLogger("nw_integration")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--budget", type=float, default=900.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s party=%s", d.map_name(), d.pos(),
             [(m.nickname, m.hp) for m in d.state.party()])

    # A WIPED PARTY CANNOT ENTER. The gate refuses, the A presses do nothing,
    # and the run blacklists the map -- observed live, with the collector's
    # own "healing first" line printing AFTER the failure it caused.
    if not any((m.hp or 0) > 0 for m in d.state.party() if not m.is_egg):
        log.info("party is down; healing before anything else")
        d.heal_at_nearest_center()

    c = Collector(d)
    ok = c.goto_map("SafariZone_Northwest", budget=a.budget)
    log.info("goto_map(SafariZone_Northwest) -> %s | now %s %s",
             ok, d.map_name(), d.pos())
    arrived = d.map_name() == "SafariZone_Northwest"
    log.info("ARRIVED IN NORTHWEST: %s", arrived)
    if arrived:
        n = d.nav
        grass = n.find_tiles(d.map_name(), "grass")
        reach = {(t[0], t[1]) for t in n.reachable(d.map_name(), d.pos())}
        log.info("huntable: %d of %d grass cells reachable from here",
                 sum(1 for g in grass if tuple(g) in reach), len(grass))
        if a.out:
            d.save(a.out)
            log.info("banked %s", a.out)
    return 0 if arrived else 1


if __name__ == "__main__":
    raise SystemExit(main())
