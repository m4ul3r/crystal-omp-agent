#!/usr/bin/env python
"""Walk the Seafloor Cavern to the boss trigger, one named hop at a time.

The graph-routing version thrashed: it would re-enter a room it had just left
and re-solve it, and a room whose boulders are wedged has no signal to say so.
This is the route the decomp's warp tables describe, stated explicitly, with
the boulder solver doing each leg.

    Room1  (6,2)  -> Room2      approach (6,3)
    Room2  (5,2)  -> Room6      approach (5,3)
    Room6  (4,1)  -> Room3      approach (4,2)
    Room3  (8,1)  -> Room8      approach (8,2)
    Room8  (5,2)  -> Room9      approach (5,3)
    Room9  trigger (17,42)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from boulder_solver import walk as boulder_walk  # noqa: E402
from room6_run import plan as current_plan  # noqa: E402
from slide_probe import model as slide_model  # noqa: E402

log = logging.getLogger("cavern")

HOPS = [
    ("SeafloorCavern_Entrance", (10, 2), (10, 1)),
    ("SeafloorCavern_Room1", (6, 3), (6, 2)),
    ("SeafloorCavern_Room2", (5, 3), (5, 2)),
    ("SeafloorCavern_Room6", (4, 2), (4, 1)),
    ("SeafloorCavern_Room3", (8, 2), (8, 1)),
    ("SeafloorCavern_Room8", (5, 3), (5, 2)),
]
ROOM9 = "SeafloorCavern_Room9"


CURRENTS = (0x50, 0x51, 0x52, 0x53)


def _has_currents(d) -> bool:
    grid = d.nav.grid(d.map_name())
    return any(c is not None and c.behavior in CURRENTS
               for row in grid for c in row)


def _cross_currents(d, target) -> bool:
    """Room6 is a current maze: nav plans across it and the engine refuses."""
    here = d.map_name()
    if d.pos() == target:
        return True
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
    live, static = d.live_grid(), d.nav.grid(here)
    grid = [[live.get((x, y), static[y][x]) for x in range(len(static[0]))]
            for y in range(len(static))]
    for _ in range(8):
        if d.pos() == target:
            return True
        blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                   if not o.get("player")}
        path = current_plan(grid, blocked, d.pos(), target)
        if path is None:
            return False
        for mv in path:
            want = slide_model(grid, d.pos(), mv, blocked=blocked)
            d.step_dir(mv)
            d.settle(60)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
            if d.map_name() != here or d.pos() != want:
                break
    return d.pos() == target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s", d.map_name(), d.pos())
    order = [name for name, _, _ in HOPS]

    for _ in range(len(HOPS) * 4):
        here = d.map_name()
        if here == ROOM9:
            break
        if here not in order:
            log.info("off the route in %s", here)
            return 1
        idx = order.index(here)
        _n, approach, door = HOPS[idx]
        if d.pos() != approach:
            # ONLY boulder rooms need the boulder planner. Room6 is flooded --
            # its arrival ledge is six cells and everything else is ocean --
            # and the offline planner has no model of mounting Surf, so it
            # declared a room with no boulders in it "wedged". reach_cell goes
            # through nav, which is told about Surf by _surf_sync.
            ok = False
            if d.boulder_signature():
                ok = boulder_walk(d, approach, tries=8)
            elif _has_currents(d):
                ok = _cross_currents(d, approach)
            else:
                try:
                    ok = d.reach_cell(*approach, map_name=here,
                                      on_battle="fight")
                except Exception as exc:  # noqa: BLE001
                    log.info("  %s: %s", here, str(exc)[:70])
                    if d.in_battle():
                        d.fight(policy=Driver.damage_first)
                    ok = d.pos() == approach
            if not ok:
                # WEDGED. Boulders reset when the map reloads, so step back
                # through the door we came in by and try the room again --
                # verified live: a boulder pushed to (7,11) is at (5,11) again
                # after one round trip.
                back = HOPS[idx - 1] if idx else None
                if back is None:
                    log.info("HOP %s: stuck with nowhere to reset", here)
                    return 1
                entry = {"SeafloorCavern_Room1": (5, 18),
                         "SeafloorCavern_Room2": (12, 19),
                         "SeafloorCavern_Room6": (11, 21),
                         "SeafloorCavern_Room3": (4, 15),
                         "SeafloorCavern_Room8": (5, 12)}.get(here)
                log.info("HOP %s: wedged, resetting via %s", here, entry)
                if not entry or not boulder_walk(d, entry, tries=6):
                    return 1
                d.take_warp(*entry)
                continue
        before = here
        if not d.take_warp(*door) and d.map_name() == before:
            log.info("HOP %s: door %s refused: %s", here, door,
                     d.last_warp_reason)
            return 1
        log.info("HOP %s -> %s %s", before, d.map_name(), d.pos())
        if a.out:
            d.save(a.out)

    if d.map_name() != ROOM9:
        log.info("FAIL: in %s, not %s", d.map_name(), ROOM9)
        return 1

    log.info("in Room9; walking to the trigger")
    try:
        d.reach_cell(17, 42, map_name=ROOM9, on_battle="fight")
    except Exception:  # noqa: BLE001
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
    for _ in range(10):
        d.advance_scene(120000)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
        if d.state.var("VAR_SEAFLOOR_CAVERN_STATE"):
            break
    log.info("RESULT cavern=%s sootopolis=%s route128=%s at %s",
             d.state.var("VAR_SEAFLOOR_CAVERN_STATE"),
             d.state.var("VAR_SOOTOPOLIS_STATE"),
             d.state.var("VAR_ROUTE128_STATE"), d.map_name())
    if a.out:
        d.save(a.out)
    return 0 if d.state.var("VAR_SEAFLOOR_CAVERN_STATE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
