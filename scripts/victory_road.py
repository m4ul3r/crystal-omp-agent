#!/usr/bin/env python
"""Cross Victory Road to the Pokemon League door.

Written as a WARP GRAPH WALK rather than one pathfinding call, because asking
for the far side in a single request does not work here:

* `travel("EverGrandeCity")` is satisfied in under a second by the door you
  just walked in through.
* `reach_cell(39, 5)` -- the north exit -- ran 41 minutes without returning.
* `reach_cell(9, 14)` hung too, even though the decoded grid puts those
  stairs in the same 476-cell component as the entrance.

Each floor on its own is fine: `goto` walks it in tens of seconds. What breaks
is crossing three of them in one search. So this holds the map's own warp
table, walks to ONE warp at a time, and repeats -- the same shape that solved
the Seafloor Cavern.

The goal is `VictoryRoad_1F (39, 5)`, which is the only exit that lands on the
league side of Ever Grande; the entrance at (15, 40) goes back to the beach.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boulder_solver import walk as boulder_walk  # noqa: E402

log = logging.getLogger("vr")

FLOORS = ("VictoryRoad_1F", "VictoryRoad_B1F", "VictoryRoad_B2F")

#: The exit that reaches the league. (15,40) is the way we came in.
GOAL = ("VictoryRoad_1F", (39, 5))

#: Never take these -- they leave the dungeon the wrong way.
LEAVES = {("VictoryRoad_1F", (15, 40))}

REPO = Path(__file__).resolve().parents[1]


def warps(map_name: str) -> list[tuple[int, int, str]]:
    j = json.loads((REPO / "pret" / "data" / "maps" / map_name /
                    "map.json").read_text())
    out = []
    for w in (j.get("warp_events") or []):
        dest = str(w.get("dest_map", "")).replace("MAP_", "")
        out.append((int(w["x"]), int(w["y"]), dest))
    return out


def reachable_warps(d) -> list[tuple[int, int, str]]:
    """Every warp on this floor, nearest first.

    This used to filter by `nav.reachable`, which walks the STATIC grid and
    therefore cannot see past a breakable rock or a boulder we intend to
    shove. On Victory Road B1F that left exactly two doors on offer and the
    crossing ping-ponged between (30,25) and B2F forever, while (17,16),
    (5,26) and (42,2) -- all openable -- were never even considered. The
    boulder solver already answers "can I get there" properly and fails fast
    when it cannot, so let it judge instead of pre-filtering with a model we
    know is wrong.
    """
    m = d.map_name()
    here = set(d.nav.reachable(m, d.pos(), d.elevation()))
    x0, y0 = d.pos()

    def rank(w):
        # Statically reachable doors first, then by distance.
        return (0 if (w[0], w[1]) in here else 1,
                abs(w[0] - x0) + abs(w[1] - y0))

    return sorted(warps(m), key=rank)


#: Boulders reset to their map defaults when the floor reloads, so a floor we
#: have shoved into an unsolvable shape is one round trip from fresh.
#: The warp each floor uses to step out and back in. Picking "whatever warp is
#: reachable first" chose the one we were trying to REACH, so the reset walked
#: into the puzzle it was supposed to undo.
BACK_DOOR = {
    "VictoryRoad_B1F": (8, 3),     # -> 1F (9,14)
    "VictoryRoad_1F": (9, 14),     # -> B1F (8,3)
    "VictoryRoad_B2F": (30, 25),   # -> B1F (30,25)
}


def reset_floor(d) -> bool:
    """Leave and re-enter this floor, restoring every boulder.

    A half-pushed floor is usually unsolvable -- Sokoban pushes are one-way,
    and the solver reported "no solution to (30,25) from (17,12)" on a floor
    whose boulder had already moved from (9,10) to (10,10). From a FRESH floor
    the same search finds a 68-move plan, so undoing beats out-thinking.
    """
    m = d.map_name()
    door = BACK_DOOR.get(m)
    if door is None:
        return False
    x, y = door
    # BOULDER-AWARE, like everything else on these floors. Plain `goto` cannot
    # cross a room it has just shoved boulders around in, so the reset failed
    # silently and `reach` gave up after a single attempt.
    if d.pos() != (x, y):
        d.use_strength()
        if not boulder_walk(d, (x, y), tries=4, smashing=True) \
                and d.pos() != (x, y):
            return False
    if not d.take_warp(x, y):
        return False
    other = d.map_name()
    for w in warps(other):
        if w[2].replace("VICTORY_ROAD_", "VictoryRoad_") == m:
            if d.pos() != (w[0], w[1]):
                d.goto(w[0], w[1], on_battle="fight")
            if d.pos() == (w[0], w[1]):
                d.take_warp(w[0], w[1])
            break
    return d.map_name() == m


def reach(d, cell, tries=1) -> bool:
    """Walk to `cell`, pushing boulders and smashing rocks as needed.

    Plain `goto` cannot do this floor: it planned routes the engine refuses
    and reported "walked 144 chunks without arriving". The boulder solver
    plans PUSHES, which is the difference between 144 wasted chunks and a
    68-move plan. Its belief about where the boulders are goes stale once it
    has shoved a few, so a failure resets the floor and re-plans from the
    map's own defaults.
    """
    start_map = d.map_name()
    for attempt in range(tries):
        if d.pos() == cell or d.map_name() != start_map:
            # A WARP TILE FIRES WHEN YOU STEP ON IT (gotcha 15 in reverse):
            # walking onto the stairs already took them, so a changed map is
            # arrival, not a stall. Treating it as a stall made the crossing
            # "reset the floor" immediately after every successful descent.
            return True
        d.use_strength()
        try:
            if boulder_walk(d, cell, tries=6, smashing=True):
                return True
        except Exception as exc:  # noqa: BLE001
            # A wild encounter mid-route raises TravelInterrupted out of
            # smash_rock's own goto and killed the whole crossing. In a
            # dungeon full of trainers that is a normal event, not a fault:
            # settle the battle and let the next attempt re-plan.
            log.info("  %s during the walk: %s", type(exc).__name__,
                     str(exc)[:80])
            try:
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
                d.close_menus()
            except Exception:  # noqa: BLE001
                pass
        if d.pos() == cell or d.map_name() != start_map:
            return True
        # NO RESET HERE. Resetting after every candidate door cost minutes
        # per attempt and re-randomised the floor before the next door was
        # even tried; `cross` resets once when a WHOLE pass has failed.
        log.info("  stalled at %s heading for %s", d.pos(), cell)
    return d.pos() == cell or d.map_name() != start_map


def cross(d, out=None, budget_s=3600.0) -> bool:
    stop = time.time() + budget_s
    #: Warps already taken, so a two-room loop cannot run forever.
    used: set[tuple[str, int, int]] = set()
    #: The cell we just arrived on. Ordering candidates by distance makes the
    #: door we came through the nearest one by definition -- distance zero --
    #: so the crossing took it straight back and ping-ponged 1F(9,14) <->
    #: B1F(8,3) forever, one door per floor, never trying the other six.
    arrived: tuple[str, int, int] | None = None

    while time.time() < stop:
        m, pos = d.map_name(), d.pos()
        if m not in FLOORS:
            log.info("left the dungeon at %s %s", m, pos)
            return m == "EverGrandeCity"

        if m == GOAL[0]:
            here = set(d.nav.reachable(m, pos, d.elevation()))
            if GOAL[1] in here:
                log.info("goal warp is reachable from %s", pos)
                if reach(d, GOAL[1]):
                    if d.take_warp(*GOAL[1]):
                        log.info("OUT at %s %s", d.map_name(), d.pos())
                        if out:
                            d.save(out)
                        return True

        options = [w for w in reachable_warps(d)
                   if (m, w[0], w[1]) not in used
                   and (m, w[0], w[1]) != arrived
                   and (m, (w[0], w[1])) not in LEAVES]
        if not options:
            # RETRY THE WHOLE SET. A warp that failed once is not failed
            # forever: every refused step teaches the solver a wall
            # (`boulder_solver.note_wall`), so the next pass plans around
            # something the last one walked into. Clearing the used set turns
            # a single sweep into a convergent one, bounded by the budget.
            if used:
                log.info("no unused warp on %s from %s -- resetting and "
                         "retrying all (%d tried)", m, pos, len(used))
                used.clear()
                # One reset per PASS, not per door: the floor is only worth
                # restoring once every candidate on it has been refused.
                reset_floor(d)
                continue
            log.info("no warp reachable at all on %s from %s", m, pos)
            return False

        # Prefer a floor we have not just come from; otherwise the first.
        target = options[0]
        for w in options:
            if w[2].replace("VICTORY_ROAD_", "VictoryRoad_") != m:
                target = w
                break
        cell = (target[0], target[1])
        used.add((m, cell[0], cell[1]))
        log.info("%s %s -> warp %s (%s)", m, pos, cell, target[2])
        try:
            arrived = reach(d, cell)
        except Exception as exc:  # noqa: BLE001
            log.info("  %s reaching %s: %s", type(exc).__name__, cell,
                     str(exc)[:80])
            arrived = False
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
        if not arrived:
            log.info("  could not reach %s on %s", cell, m)
            continue
        if d.map_name() == m and d.pos() == cell:
            d.take_warp(*cell)
        if d.map_name() != m:
            arrived = (d.map_name(), d.pos()[0], d.pos()[1])
        log.info("  now %s %s", d.map_name(), d.pos())
        if out:
            d.save(out)

    log.info("out of budget at %s %s", d.map_name(), d.pos())
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=60.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    out = a.out or a.state
    log.info("START %s %s", d.map_name(), d.pos())
    ok = cross(d, out, a.minutes * 60.0)
    log.info("RESULT %s at %s %s", ok, d.map_name(), d.pos())
    if out:
        d.save(out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
