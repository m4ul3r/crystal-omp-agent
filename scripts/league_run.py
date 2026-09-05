#!/usr/bin/env python
"""Sootopolis -> Ever Grande -> Victory Road -> the Pokemon League.

Badge 8 is won, so Waterfall works and this is the last stretch of the main
game. It is written as explicit legs because the two hard parts are not
battles:

* **Ever Grande is a plateau above a waterfall.** From the sea only 320 cells
  are reachable and none of the three doors are among them; the climb at
  (18,68) is what opens the city. Nav models a waterfall tile as ordinary
  water, so routing happily claims the top is reachable and then never gets
  there -- `reach_cell` to a cell above the falls ran for over four minutes
  without returning.
* **`climb_waterfall` is strict** and says so before pressing anything:
  badge 8, already surfing, and the FACED tile must be MB_WATERFALL --
  `GetInteractedWaterScript` checks `IsPlayerSurfingNorth()`. Standing at the
  foot of the falls (18,68) satisfies all three; `last_field_reason` names
  which one failed otherwise.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("league")

CITY = "EverGrandeCity"

#: Sea route from Mossdeep. Each hop is a single map connection.
SEA = ["Route127", "Route128", CITY]

#: Foot of the falls, and the column of MB_WATERFALL above it.
FALLS_BASE = (18, 68)

#: Ever Grande's own doors, from its warp table.
VICTORY_ROAD_DOOR = (18, 41)
LEAGUE_DOOR = (18, 5)
CENTER_DOOR = (27, 48)


def bank(d, out) -> None:
    if out:
        try:
            d.save(out)
        except Exception:  # noqa: BLE001
            pass


def to_city(d, out=None) -> bool:
    """Stand in Ever Grande City, at sea level.

    Flying is tried first and usually wins: entering the city once sets
    `FLAG_VISITED_EVER_GRANDE_CITY`, after which Fly reaches it directly and
    the four sea legs are pure overhead.

    It does NOT skip the dungeon, though, and it is worth writing down why:
    the region-map cursor for Ever Grande sits at (28,10), which is above the
    cliff with the League, so this looked like a way to bypass Victory Road
    entirely. The cursor is only where the map marker is DRAWN -- the landing
    is the map's own fly warp, and that is (18,42), on the lower plateau.
    """
    if d.map_name() == CITY:
        return True
    if d.state.flag("FLAG_VISITED_EVER_GRANDE_CITY") and d.fly_to(CITY):
        log.info("  flew straight to %s %s", d.map_name(), d.pos())
        bank(d, out)
        return d.map_name() == CITY
    if d.map_name() not in SEA and not d.fly_to("MossdeepCity"):
        log.info("  could not fly to Mossdeep from %s", d.map_name())
        return False
    for leg in SEA:
        if d.map_name() == CITY:
            break
        try:
            d.travel(leg, on_battle="fight")
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
        bank(d, out)
    log.info("  at %s %s", d.map_name(), d.pos())
    return d.map_name() == CITY


#: Anything at or above this row is on top of the falls. Nav cannot answer
#: this question -- it models MB_WATERFALL as ordinary water, so it reports the
#: plateau's doors as reachable from sea level and a climb as unnecessary. The
#: first version of this function trusted that and declared "ON THE PLATEAU"
#: while sitting at (1,67), still in the ocean.
PLATEAU_Y = 59


def on_plateau(d) -> bool:
    return d.map_name() == CITY and d.pos()[1] <= PLATEAU_Y


def climb(d, out=None) -> bool:
    """Up the falls onto the plateau. Judged by POSITION, never by nav."""
    for attempt in range(6):
        if on_plateau(d):
            return True
        try:
            d.reach_cell(*FALLS_BASE, map_name=CITY, on_battle="fight")
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
        if d.pos() != FALLS_BASE:
            log.info("  attempt %d: at %s, not the falls %s", attempt,
                     d.pos(), FALLS_BASE)
            continue
        ok = d.climb_waterfall()
        log.info("  attempt %d: waterfall %s (%s) -> %s", attempt, ok,
                 d.last_field_reason, d.pos())
        bank(d, out)
    return on_plateau(d)


def heal_on_plateau(d, out=None) -> bool:
    """Use Ever Grande's own Center before the dungeon.

    Victory Road is long, its trainers are L43-45, and the run arrives from a
    sea crossing already hurt -- a crossing attempt whited out to Sootopolis
    with the lead at 0 HP, which throws away the whole traverse. The Center at
    (27,48) is on the plateau, one door from the Victory Road entrance.
    """
    if "PokemonCenter" not in d.map_name():
        try:
            d.reach_cell(CENTER_DOOR[0], CENTER_DOOR[1] + 1, map_name=CITY,
                         on_battle="fight")
        except Exception:  # noqa: BLE001
            pass
        if not d.take_warp(*CENTER_DOOR):
            log.info("  could not enter the Center at %s", CENTER_DOOR)
            return False
    ok = d.heal()
    log.info("  healed=%s (%s)", ok,
             [(m.nickname, f"{m.hp}/{m.max_hp}") for m in d.state.party()])
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    bank(d, out)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=120.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    out = a.out or a.state
    deadline = time.time() + a.minutes * 60
    log.info("START %s %s badges=%d", d.map_name(), d.pos(),
             len(d.state.badges()))

    if not to_city(d, out):
        log.info("RESULT could not reach %s", CITY)
        return 1
    bank(d, out)

    if not climb(d, out):
        log.info("RESULT stuck below the falls at %s", d.pos())
        return 1
    log.info("ON THE PLATEAU at %s", d.pos())
    bank(d, out)

    heal_on_plateau(d, out)

    # THE DOOR IS THE EASY PART. Entering Victory Road works
    # (take_warp(18,41) -> VictoryRoad_1F (15,40), verified); getting THROUGH
    # it does not, and that is the next stage's work rather than a missing
    # line here:
    #
    #   * `travel("EverGrandeCity")` from inside is satisfied instantly by the
    #     door you just came in -- it walks back out to (18,42).
    #   * `reach_cell(39,5)` at the far exit ran 41 minutes without returning,
    #     and so did `reach_cell(9,14)`, the stairs down that the decoded grid
    #     says share a component with the entrance.
    #
    # The floors are boulder puzzles with Strength and Surf sections; the
    # decoded grid says a cell is reachable and the engine disagrees, which is
    # exactly the shape `scripts/boulder_solver.py` was written for in the
    # Seafloor Cavern. Wire that in, floor by floor, rather than asking one
    # search to cross three of them.
    if d.take_warp(*VICTORY_ROAD_DOOR):
        log.info("INSIDE %s %s -- traversal is not solved yet", d.map_name(),
                 d.pos())
        bank(d, out)

    log.info("RESULT plateau reached with %.0f min left",
             (deadline - time.time()) / 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
