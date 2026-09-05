#!/usr/bin/env python
"""Cross Victory Road to the League, via B2F -- the route six sessions missed.

The dungeon's connectivity, measured rather than assumed:

    1F(15,40) entrance  -> 476-cell region, does NOT contain the goal
    1F(42,38)           -> 28-cell dead pocket
    1F(21,32)           -> 188 cells, CONTAINS the goal (39,5)

So the whole dungeon reduces to reaching **B1F(20,21)**, which is 1F warp 2 =
(21,32). From the entrance component B1F(20,21) is genuinely NO SOLUTION, and
so are (17,16), (42,2), (5,26). Only (30,25) and (42,25) solve, and (42,25)
lands in the 28-cell pocket.

The way through is DOWN and back up:

    B1F(30,25) -> B2F(30,25) -> B2F(19,12) -> B1F(17,16) -> B1F(20,21)
               -> 1F(21,32)  -> 1F(39,5)   -> EverGrandeCity(18,27)

Why it was hidden: **B1F(30,25) and B2F(30,25) are the SAME coordinates.**
Arriving puts the player straight onto the destination floor's own warp, and
step_hold's still-held direction completes a second step that fires it again --
so the crossing bounced B1F -> B2F -> B1F while `take_warp` correctly reported
True, because the map really did change. Twice. Every B2F measurement in every
earlier session was therefore taken while standing on B1F, which is why all
three of its far doors read "no solution". Stepping off the arrival cell before
doing anything else turns two of them into 39- and 37-move plans.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
import boulder_solver as bs  # noqa: E402
import league_run  # noqa: E402

log = logging.getLogger("chain")

#: (map we should be on, cell to walk to, map we expect after the warp)
#: The route, and why each leg is what it is.
#:
#: The dungeon's connectivity, measured rather than assumed:
#:
#:     1F(15,40) entrance -> 476 cells, does NOT contain the exit
#:     1F(42,38)          -> 28-cell dead pocket, uniformly elevation 4
#:     1F(21,32)          -> 188 cells, CONTAINS the exit (39,5)
#:
#: so the whole dungeon reduces to reaching B1F(20,21), which is 1F warp 2 =
#: (21,32). From the entrance component that cell is genuinely unreachable, and
#: so is every other B1F door except (30,25) and (42,25) -- and (42,25) lands
#: in the 28-cell pocket.
#:
#: **B2F is crossed by SURF.** That is the fact six sessions of geometry
#: analysis missed. B2F holds a 256-cell lake of MB_OCEAN_WATER (0x15) at
#: elevation 1, and every reachability fill ran with `surfing=False`, so the
#: lake decoded as void and the arrival pocket read as a sealed 65-cell dead
#: end whose only neighbours were elevation seams. With surf enabled the fill
#: from B2F(30,25) goes 65 -> 634 cells and includes both (19,12) and (43,2);
#: find_path returns a 71-move route to the door. Nothing about the map was
#: wrong -- the question was.
LEGS = [
    ("VictoryRoad_1F", (9, 14), "VictoryRoad_B1F"),
    ("VictoryRoad_B1F", (30, 25), "VictoryRoad_B2F"),
    ("VictoryRoad_B2F", (19, 12), "VictoryRoad_B1F"),
    ("VictoryRoad_B1F", (20, 21), "VictoryRoad_1F"),
    ("VictoryRoad_1F", (39, 5), "EverGrandeCity"),
]

#: Floors whose route crosses water, with the SHORE cell to mount from and
#: the direction the water lies in. A mount needs the FACED tile to be water,
#: so it cannot be done from wherever the warp happens to drop us -- B2F
#: arrives at (30,26), a dozen cells from the lake, and "could not mount surf"
#: is all you get. (33,17) is the pocket's shore; (33,16) is open water.
SURF_FLOORS = {"VictoryRoad_B2F": ((33, 17), "U")}



def _mount(d) -> bool:
    """Get on the water. `_mount_surf` needs the FACED tile to be water, so
    try each direction from where we stand."""
    for mv in "URDL":
        if d._mount_surf(mv):
            return True
    return bool(d.is_surfing())


def sokoban_to_door(d) -> bool:
    """Open the last stretch of B1F to the goal warp at (20,21).

    Plain nav plans straight up column 20 and the engine refuses: (20,22)
    through (20,24) are collision-1 wall, (20,26) carries a boulder and
    (21,26) a breakable rock. The way through is east then north, and it has
    to be done in this order because the only tile you can smash (21,26) from
    is (20,26) -- which the boulder is sitting on.
    """
    log.info("   opening the last stretch to (20,21)")
    if not d.state.flag("FLAG_SYS_USE_STRENGTH"):
        _guard(d, d.use_strength)
    if d.pos() != (20, 27):
        _guard(d, d.goto, 20, 27, on_battle="fight")
    _settle(d)
    if d.pos() == (20, 27):
        _guard(d, d.step_dir, "U")          # shove the boulder off (20,26)
        _settle(d)
    if d.pos() == (20, 26):
        log.info("   smash (21,26) -> %s", _guard(d, d.smash_rock, 21, 26))
        _settle(d)
    for mv in "RRUUUUULL":
        if d.map_name() != "VictoryRoad_B1F":
            return True                      # the (20,21) warp fired
        before = d.pos()
        _guard(d, d.step_dir, mv)
        _settle(d)
        if d.pos() == before:
            p = d.nav.find_path("VictoryRoad_B1F", d.pos(), (20, 21),
                                d.elevation())
            if not p:
                break
            for m2 in p:
                if d.map_name() != "VictoryRoad_B1F":
                    return True
                _guard(d, d.step_dir, m2)
                _settle(d)
            break
    return d.map_name() != "VictoryRoad_B1F"


def _settle(d) -> None:
    for _ in range(8):
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
            d.advance_scene(60000)
        elif d.scene_active():
            d.advance_scene(60000)
            d.close_menus()
        else:
            return


def _guard(d, fn, *a, **k):
    """Run `fn`, absorbing the wild encounters this dungeon throws constantly.

    `smash_rock` and `use_strength` both run nested `goto`s whose default is
    on_battle="raise", so any encounter inside them raises TravelInterrupted
    straight out through the caller.
    """
    for _ in range(5):
        try:
            return fn(*a, **k)
        except TravelInterrupted:
            _settle(d)
    return None

def step_off(d, cell) -> bool:
    """Leave the warp cell we just landed on, before it can fire again.

    Only needed because some pairs share coordinates, but harmless everywhere:
    a warp triggers on the step that ENTERS it, so standing beside it is
    always the safe place to plan from.
    """
    if d.pos() != cell:
        return True
    for mv in "DURL":
        if d.step_dir(mv) and d.pos() != cell:
            return True
    return False


def enter(d, cell, want, tries=4) -> bool:
    """Take the warp at `cell` and end up actually standing on `want`."""
    for attempt in range(tries):
        d.take_warp(*cell)
        if d.map_name() == want:
            step_off(d, cell)
            if d.map_name() == want:
                log.info("   entered %s at %s", want, d.pos())
                return True
        log.info("   attempt %d landed on %s %s -- retrying",
                 attempt, d.map_name(), d.pos())
        d.settle(60)
    return d.map_name() == want


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    # PUBLISH WHERE THE WIDGET IS LOOKING. A Driver left to itself publishes
    # to a feed named after its STATE FILE, so a run on saves/gauntlet2.state
    # wrote live/gauntlet2.png while the desktop widget watched live/default.*
    # and showed a frame from 102 minutes earlier -- reported, correctly, as
    # "last frame was over 6000 seconds ago" while the game was running fine.
    ap.add_argument("--feed", default="default")
    ap.add_argument("--out")
    ap.add_argument("--tries", type=int, default=10)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = a.out or a.state

    d = Driver(a.state)
    if a.feed:
        # The Driver auto-attaches a feed named after its STATE FILE, and the
        # emulator allows exactly one tick observer -- so publishing where the
        # widget is looking means REPLACING that feed, not adding to it.
        if getattr(d.emu, "observer", None) is not None:
            d.emu.observer = None
        LiveFeed(a.feed).attach(d)
    log.info("START %s %s", d.map_name(), d.pos())

    if not d.map_name().startswith(("VictoryRoad", "EverGrandeCity")):
        if not league_run.to_city(d) or not league_run.climb(d):
            log.info("could not reach the plateau")
            return 1
        league_run.heal_on_plateau(d)
    if d.map_name() == "EverGrandeCity":
        if not enter(d, league_run.VICTORY_ROAD_DOOR, "VictoryRoad_1F"):
            log.info("could not enter Victory Road")
            return 1

    for want_map, cell, lands in LEGS:
        if d.map_name() == "EverGrandeCity":
            break
        if d.map_name() != want_map:
            log.info("SKIP leg %s %s -- standing on %s", want_map, cell,
                     d.map_name())
            continue
        # B1F APPEARS TWICE IN THE ROUTE -- once outbound to (30,25) and once
        # on the way back at (20,21) -- so a leg list keyed on map name alone
        # sends the return trip back down the stairs it just came up. Locate
        # ourselves by COMPONENT: if the goal feeder is reachable from here,
        # this is the return visit.
        if want_map == "VictoryRoad_B1F":
            here = {(x, y) for x, y, _ in
                    d.nav._reachable_triples(want_map, d.pos(), d.elevation())}
            if (20, 21) in here and cell != (20, 21):
                log.info("SKIP leg %s -- already in the goal component",
                         cell)
                continue
            if cell == (20, 21) and (20, 21) not in here:
                log.info("STOPPED: (20,21) not reachable from %s", d.pos())
                d.save(out)
                return 1
        log.info("LEG %s -> %s (then %s)", d.map_name(), cell, lands)
        if want_map in SURF_FLOORS and not d.is_surfing():
            shore, face = SURF_FLOORS[want_map]
            # MOUNT BEFORE PLANNING. nav.surfing gates whether water counts as
            # road, so a plan made on foot routes around a lake that is the
            # only way through -- which is exactly how this floor read as a
            # sealed pocket for six sessions.
            log.info("   walking to the shore %s for %s", shore, want_map)
            # `goto` here, not the boulder solver: B2F has NO boulders (its
            # snapshot reports an empty set), so the solver buys nothing, and
            # it stalls on the arrival warp cell where plain nav routes fine.
            if d.pos() != shore and not d.goto(*shore, on_battle="fight"):
                log.info("   could not reach the shore (at %s)", d.pos())
            if d._mount_surf(face) or _mount(d):
                log.info("   surfing from %s", d.pos())
            else:
                log.info("   could not mount surf at %s", d.pos())
        # A WILD ENCOUNTER IS NOT A ROUTING FAILURE, and in this dungeon it is
        # the normal case. `smash_rock` runs a nested `goto` whose default is
        # on_battle="raise", so a battle inside `use_strength` throws
        # TravelInterrupted straight out through bs.walk and killed the run at
        # (19,25) two cells into a leg. Fight it and re-plan.
        walked = False
        for _try in range(6):
            try:
                # Plain nav wherever it already has a route: the goal
                # component is a 24-move walk from (17,16), and the solver
                # spent ten attempts trying to smash a rock at (21,26) it
                # could neither reach nor face while standing five cells
                # from the door.
                # Plain nav ONLY where boulders are not in the way: the
                # surf floor has none, and the goal component's last stretch
                # is a plain walk the solver mishandles. Everywhere else the
                # route needs pushes, and find_path cannot see boulders at
                # all -- preferring it for (30,25) produced a path the engine
                # refused on move one, from a leg that had worked all session.
                use_nav = want_map in SURF_FLOORS or cell == (20, 21)
                walked = (d.goto(*cell, on_battle="fight") if use_nav
                          else bs.walk(d, cell, tries=a.tries, smashing=True))
                break
            except TravelInterrupted:
                log.info("   battle mid-leg at %s -- fighting", d.pos())
                d.fight(policy=Driver.damage_first)
                d.advance_scene(60000)
                if d.pos() == cell:
                    walked = True
                    break
        # A LEG THAT ARRIVES IS A LEG THAT WORKED. Walking onto the warp cell
        # fires it, so the map changes mid-walk and the position check then
        # compares against the floor we just left -- which reported "could not
        # reach (9,14)" while standing on B1F, having descended correctly.
        if d.map_name() == lands:
            log.info("   warped mid-walk: now %s %s", d.map_name(), d.pos())
            step_off(d, cell)
            d.save(out)
            continue
        if not walked and d.pos() == cell:
            walked = True
        if not walked and want_map == "VictoryRoad_B1F" and cell == (20, 21):
            if sokoban_to_door(d):
                log.info("   through the (20,21) warp: now %s %s",
                         d.map_name(), d.pos())
                d.save(out)
                continue
        if not walked:
            log.info("STOPPED: could not reach %s on %s (at %s)", cell,
                     want_map, d.pos())
            d.save(out)
            return 1
        if not enter(d, cell, lands):
            log.info("STOPPED: warp at %s never landed on %s", cell, lands)
            d.save(out)
            return 1
        d.save(out)

    # THE LAST LEG. Coming out at 1F(39,5) lands in Ever Grande's UPPER
    # region -- 232 cells holding the League door at (18,5) and nothing else
    # of interest. The lower plateau (where the waterfall climb lands) cannot
    # reach it: row 37 is solid wall across the full map width.
    if d.map_name() == "EverGrandeCity":
        log.info("ON THE UPPER PLATEAU at %s", d.pos())
        if d.goto(18, 6, on_battle="fight") or d.pos()[1] <= 8:
            if enter(d, (18, 5), "EverGrandeCity_PokemonLeague"):
                log.info("INSIDE THE POKEMON LEAGUE at %s", d.pos())
                d.save(out)
                return 0
        log.info("could not enter the League from %s (%s)", d.pos(),
                 d.last_goto_reason)

    log.info("RESULT %s %s", d.map_name(), d.pos())
    d.save(out)
    return 0 if d.map_name().startswith("EverGrandeCity") else 1


if __name__ == "__main__":
    raise SystemExit(main())
