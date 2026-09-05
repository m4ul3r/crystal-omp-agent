#!/usr/bin/env python
"""Walk east until Lilycove is VISITED again, restoring FLY's landing.

Promoting a Safari fork rolled the run back to before it had ever entered
Lilycove, so `FLAG_VISITED_LILYCOVE_CITY` went clear. That single flag is the
fly map's gate (`Overworld_MapTypeAllowsTeleportAndFly` will fly, but the
region map "draws LILYCOVE CITY greyed out and ignores A on it"), and with it
clear the loop could reach neither Mt. Pyre nor the Safari Zone:

    could not fly to LilycoveCity: not-visited
    could not reach SafariZone_Southwest: no walkable route from SlateportCity

A fork promotion is a timeline rollback, and the flags it drops are invisible
in a dex count. This walks the short way back: FLY to Fortree, which IS a
landing, then east through Route 120 and 121 -- each a single leg, rather than
asking the router for one journey across half of Hoenn.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("lilycove")

FLAG = "FLAG_VISITED_LILYCOVE_CITY"
#: Fly to the first, then walk the rest in order. Short legs on purpose.
LANDING = "FortreeCity"
LEGS = ["Route120", "Route121", "LilycoveCity"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40000)
    log.info("start %s %s | %s=%s", d.map_name(), d.pos(), FLAG,
             d.state.flag(FLAG))

    if d.state.flag(FLAG):
        log.info("already visited; nothing to do")
        return 0

    if d.map_name() != LANDING and not d.fly_to(LANDING):
        log.info("could not fly to %s (%s) -- walking from here",
                 LANDING, d.last_fly_reason)

    for leg in LEGS:
        for attempt in range(4):
            if d.map_name() == leg:
                break
            try:
                d.travel(leg, on_battle="fight", budget_s=150)
            except Exception as exc:  # noqa: BLE001 - a battle on the way
                log.info("  %s try%d: %s", leg, attempt, str(exc)[:80])
        log.info("%s -> at %s %s | %s=%s", leg, d.map_name(), d.pos(), FLAG,
                 d.state.flag(FLAG))
        if d.state.flag(FLAG):
            break

    ok = bool(d.state.flag(FLAG))
    log.info("RESULT %s=%s at %s", FLAG, ok, d.map_name())
    if ok:
        # Prove it: the landing must be selectable, not merely flagged.
        targets = [t.map_name for t in d.fly_destinations()
                   if t.unlock_flag_name is None
                   or d.state.flag(t.unlock_flag_name)]
        log.info("fly landings now: %s", targets)
        if a.out:
            d.save(a.out)
            log.info("saved %s", a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
