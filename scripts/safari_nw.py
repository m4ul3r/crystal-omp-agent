#!/usr/bin/env python
"""Reach the Safari Zone's NORTH-WEST quadrant, which needs the Mach Bike.

The quadrant holds 7 species (DODUO, DODRIO, GOLDUCK, PINSIR, PSYDUCK,
RHYHORN, SEAKING) and no sweep had ever set foot in it. It is not a routing
bug: measured on the decoded grid, NW has ZERO aligned crossings with either
neighbour on foot.

  SafariZone_Southeast -U-> Northeast   crossings [31,32,33,34]
  SafariZone_Southeast -L-> Southwest   crossings [6,7,20,21,36,37]
  SafariZone_Northeast -L-> Northwest   NONE
  SafariZone_Southwest -U-> Northwest   NONE

Southwest's northward corridor is x=7..10, and it is severed at y=3,2 by two
`MB_MUDDY_SLOPE` tiles (0xD0). The second corridor, x=19..23, is walled off
at y=2 -- that pocket is entered FROM the north, so it is an exit, not a way
in. The slope is the only door.

`ForcedMovement_MuddySlope` (field_player_avatar.c:494-504) slides you back
and zeroes the acceleration unless you are moving NORTH with
`GetPlayerSpeed() > 3`. `sMachBikeSpeeds` is {1,2,4} indexed by
`bikeFrameCounter`, which needs two tiles of CONTINUOUS held movement to
reach 2 -- so the key goes down before the run-up and stays down over the
slope. Biking is legal here: `Overworld_IsBikingAllowed` refuses only
INDOOR, SECRET_BASE and UNDERWATER, and the Safari maps are MAP_TYPE_ROUTE.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from safari_probe import enter, reach_gate  # noqa: E402

log = logging.getLogger("safari_nw")

#: The foot of the corridor, four tiles below the first slope tile at (8,3).
#: Far enough for the counter to reach 2 with room to spare.
RUN_UP_FOOT = (8, 8)


def climb_to_nw(d) -> bool:
    """From anywhere in the Safari Zone, get into the north-west quadrant."""
    if d.map_name() == "SafariZone_Northwest":
        return True
    if d.map_name() != "SafariZone_Southwest":
        if not d.travel("SafariZone_Southwest", on_battle="fight"):
            log.info("could not reach Southwest: %s", d.last_goto_reason)
            return False
    if not d.goto(*RUN_UP_FOOT, on_battle="fight"):
        log.info("could not reach the corridor foot %s: %s",
                 RUN_UP_FOOT, d.last_goto_reason)
        return False
    if not d.mount_bike():
        log.info("could not mount: %s", d.last_bike_reason)
        return False
    log.info("at %s on the bike; holding UP through the slope", d.pos())
    if not d.climb_slope("U", run_up=4):
        log.info("slope refused: %s", d.last_bike_reason)
        return False
    log.info("climbed to %s %s", d.map_name(), d.pos())
    # The slope tops out inside Southwest; the border is two tiles further.
    if d.map_name() == "SafariZone_Southwest":
        d.emu.run_sequence("UP:48")
        d.settle(60)
    arrived = d.map_name() == "SafariZone_Northwest"
    if arrived:
        # OFF THE BIKE ON ARRIVAL. The bike is for the slope, not for the
        # hunt: a rider cannot fish, and each pedal covers up to four tiles,
        # which spends Safari steps far faster than it meets wild Pokemon.
        d.dismount_bike()
    return arrived


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())

    # OUT OF THE CAVE FIRST. The sweeper leaves its state wherever the last
    # fish was, and Meteor Falls B1F cannot route to a surface town at all --
    # three gate attempts died on "no approach to warp (5,6) on MeteorFalls_1F"
    # before anything was even aimed at the Safari Zone. Fly is the way out,
    # and the gate is Lilycove's neighbour.
    if not d.map_name().startswith("SafariZone"):
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if not d.map_name().startswith(("Route121", "Lilycove")):
            if d.fly_to("LilycoveCity"):
                log.info("flew out to %s %s", d.map_name(), d.pos())
            else:
                log.info("could not fly out: %s",
                         getattr(d, "last_fly_reason", "?"))

    if not d.map_name().startswith("SafariZone"):
        class _Mini:
            """`reach_gate` only ever calls `goto_map`."""

            def __init__(self, drv):
                self.d = drv

            def goto_map(self, name, budget=300.0):
                try:
                    return bool(self.d.travel(name, on_battle="fight",
                                              budget_s=budget))
                except Exception as exc:  # noqa: BLE001
                    log.info("travel to %s: %s", name, str(exc)[:90])
                    return False

        if not reach_gate(d, _Mini(d)):
            log.info("never reached the gate (at %s)", d.map_name())
            return 1
        if not enter(d):
            log.info("could not enter the Safari Zone")
            return 1
    log.info("inside: %s %s", d.map_name(), d.pos())

    ok = climb_to_nw(d)
    log.info("NORTHWEST reached: %s (at %s %s)", ok, d.map_name(), d.pos())
    if ok and a.out:
        d.save(a.out)
        log.info("banked %s", a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
