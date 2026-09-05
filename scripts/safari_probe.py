#!/usr/bin/env python
"""Enter the Safari Zone and sweep it for dex entries.

Thirteen species are reachable ONLY here and the zone is behind no badge at all
-- just the Pokeblock Case and 500 (entry script
pret/data/maps/Route121_SafariZoneEntrance/scripts.inc:47-87). Entry is a
`coord_event` at (8,4), not a person to talk to: stand on it and answer YES.

The sweep itself is `scripts/collect.py`'s Collector, because pacing grass,
fighting with a catch-aware policy and saving after each catch are already
solved there. What this script owns is getting IN, and reporting the two
counters the zone runs on -- `gNumSafariBalls` (30) and
`gSafariZoneStepCounter` (500), both EWRAM, both read via GameState now.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import Collector  # noqa: E402
from pokeagent import dex as dexmod  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("safari")

GATE = "Route121_SafariZoneEntrance"
#: The coord_event that opens the conversation (map.json:80-90).
TRIGGER = (8, 4)
AREAS = ["SafariZone_Southeast", "SafariZone_Southwest",
         "SafariZone_Northeast", "SafariZone_Northwest"]


def reach_gate(d, collector, tries=3) -> bool:
    """Get to the Safari gate, FLYING most of the way.

    Walking it failed from Route 106: the gate sits beside Lilycove and Route
    106 is on the far west coast, so a foot journey crosses most of Hoenn and
    ran out of budget on Route 110. `Collector.goto_map` already ranks fly
    landings by real map-graph distance and walks only the remainder, which is
    exactly this problem -- and Lilycove is a landing now that the run has been
    there.
    """
    for _ in range(tries):
        if d.map_name() == GATE:
            return True
        if collector.goto_map(GATE, budget=420.0):
            return True
        log.info("gate attempt ended at %s (%s)", d.map_name(),
                 d.last_goto_reason)
    return d.map_name() == GATE


def enter(d) -> bool:
    """Stand on the trigger and answer YES."""
    if d.map_name().startswith("SafariZone"):
        return True
    if not d.goto(*TRIGGER, on_battle="fight"):
        log.info("could not stand on %s (%s)", TRIGGER, d.last_goto_reason)
        # The trigger fires on ENTERING the cell, so try stepping onto it from
        # each side rather than giving up on one refused plan.
        for mv in "UDLR":
            d.step_dir(mv)
            if d.map_name().startswith("SafariZone"):
                return True
    for _ in range(8):
        if d.map_name().startswith("SafariZone"):
            return True
        # YES is the default on the entry box; A answers it, and the money and
        # case checks follow inside the script.
        d.emu.run_sequence("A:4 .:40")
        d.advance_scene(40000)
    return d.map_name().startswith("SafariZone")


def counters(d) -> str:
    return (f"balls {d.state.safari_balls()} steps {d.state.safari_steps()} "
            f"in_safari {d.state.in_safari()}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--per-area", type=float, default=900.0,
                    help="seconds per Safari area; the visit is bounded by its "
                         "own 500-step counter, so a small value ends a sweep "
                         "with hundreds of steps unspent")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40000)
    target = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    before, _seen = target.dex_flags(d.state)
    log.info("start %s %s money %s dex %d", d.map_name(), d.pos(),
             d.state.money(), len(before))

    c = Collector(d, per_map=a.per_area, feed_name=None)
    if not reach_gate(d, c):
        log.info("FAIL: never reached %s (at %s)", GATE, d.map_name())
        return 1
    log.info("at the gate: %s %s", d.map_name(), d.pos())
    if not enter(d):
        log.info("FAIL: never got inside (at %s, %s)", d.map_name(),
                 counters(d))
        return 1
    log.info("INSIDE: %s %s | %s", d.map_name(), d.pos(), counters(d))

    # Only the four Safari areas are worth the budget in here; the zone ejects
    # the run at 500 steps and every step outside its own maps is wasted.
    plan = [row for row in c.plan() if row[0] in AREAS]
    log.info("areas owing species: %s",
             [(m, len(sp)) for m, _k, sp in plan])
    # `only=AREAS` is load-bearing: without it the sweep leaves the zone after
    # its first area and burns the step counter on ordinary routes.
    c.run(budget_s=a.minutes * 60.0, max_maps=8, only=AREAS)
    log.info("after the sweep: %s", counters(d))

    after, _seen2 = target.dex_flags(d.state)
    gained = sorted(set(after) - set(before))
    log.info("RESULT dex %d -> %d | new natdex %s | %s",
             len(before), len(after), gained, counters(d))
    if a.out:
        d.save(a.out)
        log.info("saved %s", a.out)
    return 0 if len(after) > len(before) else 1


if __name__ == "__main__":
    raise SystemExit(main())
