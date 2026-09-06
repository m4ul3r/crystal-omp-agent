#!/usr/bin/env python
"""SNORUNT out of Shoal Cave's Ice Room -- a chain leg, not a savestate.

Worth two dex entries: SNORUNT is the only species in the Ice Room's table
and the only species in the game locked behind the tide, and GLALIE is its
L42 evolution, which the by-level grind engine closes on its own once the
base is in the book.

WHAT THIS SCRIPT KNOWS THAT `pace_map` DOES NOT
-----------------------------------------------
1. THE TIDE IS THE HOST WALL CLOCK. `UpdateShoalTideFlag`
   (pret/src/time_events.c:54-92) indexes a 24-entry table by
   `gLocalTime.hours` and sets FLAG_SYS_SHOAL_TIDE from it; read out of this
   ROM the table is `[1,1,1,0,0,0,0,0,0,1,1,1,1,1,1,0,0,0,0,0,0,1,1,1]`, so
   LOW tide is game hours 3-8 and 15-20. libmgba resolves the GBA RTC through
   libc `localtime_r`, so TZ picks the in-game hour and `time.tzset()` makes
   it bite mid-run. `shoal_hunt.tune_tide` is reused rather than re-derived:
   it takes the whole-hour zone with the MOST low tide left in it, because
   the window is six hours wide and a run that starts on its last minute
   loses the Ice Room half way through.

   The tide does not move the player and does not change the map id -- the
   entrance room's ON_TRANSITION calls `setmaplayoutindex` 169/165 on the
   SAME map and the inner room 170/166 -- it changes which metatiles the
   descents to the Lower Room are. A warp only fires when the behaviour under
   the player is a warp behaviour (`IsWarpMetatileBehavior`,
   pret/src/field_control_avatar.c:696 and 731-743), and at high tide those
   descents are water, so no amount of surfing enters them.

2. THE ROUTE IS THREE WARPS AND THE ICE ROOM HANGS OFF THE **LOWER** ROOM,
   not off the Stairs Room. Read out of the map headers:

       Entrance (19, 5) -> Inner  (34,29)
       Inner    (19,14) -> Lower  ( 7, 2)     [MB_LADDER, tide-gated]
       Lower    (28,11) -> Ice    (17,10)     [MB_LADDER]

   The Stairs Room is a dead end: both of its warps go back to the Inner
   Room. A run that reaches `ShoalCave_LowTideStairsRoom (3,12)` and looks
   for a descent there is one room off the path. The chain is derived by
   `pyre_shoal.warp_route`, which filters to warps that can actually FIRE, so
   at high tide it simply returns no route -- which is the honest answer.

3. THE ICE ROOM CANNOT BE PACED BY `goto`. Decoded, the room is 224
   encounter cells (MB_UNUSED_CAVE), 150 MB_ICE forced-movement cells and one
   ladder. `nav` does not model forced movement at all, so its planner treats
   ice as ordinary floor: asked to cross from (11,12) to (14,12) it picks the
   three-step line through the ice at (12,12) and (13,12) instead of the
   seven-step walk round the top, and every press on ice slides the player
   somewhere the planner did not choose. `Collector.pace_map` counts six of
   those as a stall and abandons the map.

   The arrival pocket does not need it. Off the ladder at (17,10) there are
   18 encounter cells reachable WITHOUT ever touching ice, and encounters are
   per-step -- so the hunt is `walk()` along a BFS path computed inside that
   ice-free pocket, which is deterministic and cannot desync. MB_ICE
   carries the encounter bit too (metatile_behavior.c:41), so nothing is
   given up by staying off it.

   There is no fall hazard: MB_THIN_ICE (0x26) and MB_CRACKED_ICE (0x27) do
   not occur anywhere in this room's shipped layout, and its only warp is the
   ladder home. The loop still checks the map name every iteration, because a
   ladder is one mis-step away from being taken.

4. `d.battle_policy` IS THE ONLY CATCH HOOK ON EVERY PATH.
   `shoal_hunt.install_policy` sets it (and the `advance_scene` bridge), and
   without it the harness's tactics pick a move: an L100 lead one-shots a
   L28 SNORUNT and the leg is over. `encounter_policy` has no consumer.

CHAIN CONTRACT
--------------
`--state` is mutated IN PLACE, after every catch and again at the end. The
leg reads the live dex first and exits 0 immediately when SNORUNT is already
flagged CAUGHT, so re-running it is free. It starts from anywhere: the
milestone save this was written against sits on `Underwater1`, which is not
flyable and has no warp exits, so `surface()` comes before anything else.
"""

import argparse
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boulder_solver  # noqa: E402
from pokeagent import nav as nav_mod  # noqa: E402
from pokeagent.nav import DIRS  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from collect import Collector  # noqa: E402
from share_grind import unwedge  # noqa: E402
from pyre_shoal import (  # noqa: E402
    SHOAL_ROOMS, arm, cross_dungeon, dungeon_of, enter_dungeon, game_hour,
    local_time_offset, missing_of, own_input, tide_table,
)
from shoal_hunt import (  # noqa: E402
    install_policy, shoal_policy, tide_report, tune_tide,
)
from unlocks import _enable_surf  # noqa: E402

log = logging.getLogger("snorunt")

ENTRANCE = SHOAL_ROOMS[0]
LOWER = SHOAL_ROOMS[3]
ICE = SHOAL_ROOMS[-1]
#: The Lower Room's ladder down to the Ice Room, its only approach cell, and
#: the STRENGTH boulder that sits between them.
#: `ShoalCave_LowTideLowerRoom/map.json` warp 3 is (28,11) -> Ice Room warp 0
#: (17,10); the ladder's other three neighbours are (28,12)
#: MB_IMPASSABLE_NORTH, (29,11) collision 1 and (27,11), which is only
#: reachable through (27,10) -- so every route in passes the corridor the
#: boulder blocks.
ICE_LADDER = (28, 11)
LADDER_APPROACH = (28, 10)
BOULDER = (25, 3)


# ---- getting to open air ---------------------------------------------------

def surface(d, tries=8) -> bool:
    """Come up for air, so that flying and routing are legal again.

    `Flight.step_outside` cannot do this: it walks WARP events, and an
    Underwater map has none -- surfacing is a B press on a tile whose ceiling
    allows it (`TrySetupDiveEmergeScript`, field_control_avatar.c:233, via
    `Driver.dive`). The dex-148 milestone this leg was written against sits
    on `Underwater1 (10,33)`, so without this the first thing the leg does is
    ask for a flight from the sea floor and get `indoors` back.
    """
    for _ in range(tries):
        if not d.underwater():
            return True
        own_input(d)
        if d.dive():
            continue
        here = d.map_name()
        grid = d.nav.grid(here)
        px, py = d.pos()
        spots = [(x, y)
                 for y, row in enumerate(grid)
                 for x, c in enumerate(row)
                 if c is not None and c.behavior not in nav_mod.NO_SURFACING
                 and d.nav.beh.kind(c.behavior, c.collision,
                                    c.elevation) != "blocked"]
        spots.sort(key=lambda p: abs(p[0] - px) + abs(p[1] - py))
        moved = False
        for spot in spots[1:12]:
            arm(d, 120.0)
            try:
                if d.goto(*spot, on_battle="fight") and d.dive():
                    moved = True
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("surface goto %s: %s", spot, str(exc)[:70])
                if d.in_battle():
                    d.fight()
                    d.advance_scene(40_000)
        if not moved and d.underwater():
            log.info("   cannot surface from %s %s: %s", here, d.pos(),
                     d.last_field_reason)
            return False
    return not d.underwater()


# ---- the ice-free pocket ---------------------------------------------------

def ice_pocket(d, start) -> tuple:
    """`(walkable, grass)` -- cells reachable from `start` off the ice.

    Never crosses MB_ICE and, crucially, NEVER RETURNS A WARP CELL, so no
    path through this pocket can slide the player or take the ladder home.
    `start` may itself be the ladder -- (17,10) is where the Lower Room drops
    us -- and is a BFS source without being a destination: a first pacing lap
    from (17,11) to (16,10) that was allowed to route through (17,10) took the
    ladder and spent the rest of its budget in the Lower Room, where SNORUNT
    does not live.
    """
    beh = d.nav.beh
    ice = beh.ids.get("MB_ICE")
    here = ICE
    start = tuple(start)
    cur = d.nav.cell(here, *start)
    if cur is None:
        return set(), []
    reach = d.nav.reachable(here, start, cur.elevation)
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in DIRS.values():
            nxt = (x + dx, y + dy)
            if nxt in seen or nxt not in reach:
                continue
            cell = d.nav.cell(here, *nxt)
            if cell is None:
                continue
            if cell.behavior == ice or cell.behavior in beh.door_behaviors:
                continue
            seen.add(nxt)
            queue.append(nxt)
    walkable = {c for c in seen
                if (d.nav.cell(here, *c).behavior
                    not in beh.door_behaviors)}
    grass = set(d.nav.find_tiles(here, "grass")) & walkable
    px, py = start
    return walkable, sorted(grass,
                            key=lambda c: abs(c[0] - px) + abs(c[1] - py))


def pocket_path(d, walkable, start, goal) -> str:
    """Direction letters from `start` to `goal`, staying inside `walkable`."""
    start, goal = tuple(start), tuple(goal)
    if start == goal:
        return ""
    prev = {start: None}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        for letter, (dx, dy) in DIRS.items():
            nxt = (cell[0] + dx, cell[1] + dy)
            if nxt in prev or nxt not in walkable:
                continue
            prev[nxt] = (cell, letter)
            queue.append(nxt)
    if goal not in prev:
        return ""
    out = []
    node = goal
    while prev[node] is not None:
        node, letter = prev[node]
        out.append(letter)
    out.reverse()
    return "".join(out)


# ---- the last hop: a STRENGTH boulder, not a tide -------------------------

def teach_boulder_solver_to_catch(col) -> None:
    """Stop the boulder walker from KO-ing what this leg came for.

    `boulder_solver.walk` resolves its own encounters with
    `d.fight(policy=Driver.damage_first)` -- four call sites, 450, 519, 621
    and 663 -- and that is the right default for a Sokoban room and exactly
    wrong here: the Lower Room's table is ZUBAT/GOLBAT/SPHEAL at a 10%
    encounter rate, so every push pays for a full L100 KO animation, and any
    future table change would hand the leg's own target to the damage policy.
    Rebinding the NAME `boulder_solver` looked up (never the class in
    `pokeagent`, which is shared) points all four sites at the same
    catch-or-flee decision the rest of this leg uses.
    """
    class _CatchFirst:
        damage_first = staticmethod(shoal_policy(col))

    boulder_solver.Driver = _CatchFirst


def descend_to_ice(d, col, rec, tries=3) -> bool:
    """Get into the Ice Room, pushing the Lower Room's boulder out of the way.

    THIS IS THE HOP THAT LOOKS LIKE A TIDE FAILURE AND IS NOT. At low tide
    the derived warp chain reaches `ShoalCave_LowTideLowerRoom (7,2)` and then
    reports, four times:

        warp (28,11) -> ShoalCave_LowTideIceRoom refused: no approach to warp
        (28,11) on ShoalCave_LowTideLowerRoom fired a map change

    which reads as a sealed warp. It is not. `goto`'s own reason is
    `no-path from (18,3) to (28,10)`, and the block is an object event:
    `pret/data/maps/ShoalCave_LowTideLowerRoom/map.json` puts an
    `OBJ_EVENT_GFX_PUSHABLE_BOULDER` at (25,3), and row 3 from x=20 to x=26
    is a one-tile corridor -- row 2 and row 4 are solid there -- so the
    boulder is the only way east, and east is the only way to the ladder.
    `_mark_npcs` marks it like any other body, `find_path` returns None, and
    `goto` spends its 144 rounds waiting for a wanderer that is a rock.

    Boulder positions are per-visit: re-entering the Lower Room puts it back
    at (25,3), so this pushes again on every descent rather than remembering
    that it once did. `boulder_solver.walk` plans the shoves offline against
    the engine's own push rule (field_player_avatar.c:639-655) -- measured, 11
    moves from (24,3).

    TWO THINGS HAVE TO BE RIGHT BEFORE THE PUSH, and both cost this leg a
    run.

    * WHICH DOOR. The Lower Room has four warps and its arrival components do
      not agree about the ladder: from (7,2) or (2,6) the ladder is one of 96
      reachable cells, but from (19,11) -- the landing of the Inner Room's
      (30,25) descent -- there are only 15, and the ladder is not among them.
      `cross_dungeon(..., LOWER)` is satisfied by arriving on the map at all,
      so it took (19,11) and stranded the leg in a pocket with no way on.
      `goal_cells` makes `warp_route` prove the arrival can reach (28,10)
      before it picks a door.
    * WHERE STRENGTH IS TURNED ON. `use_strength` enumerates boulders from
      `field_obstacles()`, which reads LIVE object events -- and Gen 3 only
      spawns object events near the camera. Asked from (28,11) or (19,11),
      eight rows away from (25,3), it answered "no boulder on this map" and
      the leg gave up on a boulder that was simply off-screen. It has to be
      asked from the corridor.

    The push-free plan is tried FIRST, unconditionally: after a fall back down
    the ladder the player lands at (28,11), one step from the approach, and
    the boulder is irrelevant.
    """
    for attempt in range(tries):
        if d.map_name() == ICE:
            return True
        own_input(d)
        d.nav.surfing = False
        arm(d, 600.0)
        if not cross_dungeon(d, LOWER, rec.maps, [LADDER_APPROACH]):
            log.info("   no warp chain from %s %s to a %s component that "
                     "reaches %s", d.map_name(), d.pos(), LOWER,
                     LADDER_APPROACH)
            continue
        try:
            d.sync_grid()
        except Exception as exc:  # noqa: BLE001
            log.debug("   sync_grid: %s", str(exc)[:70])
        arm(d, 600.0)
        # ONE attempt first. When the route is push-free -- which it is after
        # a fall back down the ladder, from (28,11) -- this finishes in a
        # single step. When it is not, one attempt is also all it costs to
        # find that out, and it walks us to the boulder, which is exactly
        # where STRENGTH has to be asked for.
        if not boulder_solver.walk(d, LADDER_APPROACH, tries=1):
            if not arm_strength(d):
                log.info("   attempt %d: STRENGTH stayed off (%s)", attempt,
                         d.last_field_reason)
                continue
            arm(d, 600.0)
            if not boulder_solver.walk(d, LADDER_APPROACH, tries=4):
                log.info("   attempt %d: could not reach %s past the boulder "
                         "at %s (now %s %s)", attempt, LADDER_APPROACH,
                         BOULDER, d.map_name(), d.pos())
                continue
        log.info("   at %s %s, one step off the Ice Room ladder",
                 d.map_name(), d.pos())
        arm(d, 240.0)
        try:
            d.take_warp(*ICE_LADDER)
        except TravelInterrupted:
            resolve_battle(d, col)
        except Exception as exc:  # noqa: BLE001
            log.info("   ladder %s: %s", ICE_LADDER, str(exc)[:90])
        if d.map_name() == ICE:
            return True
        log.info("   the ladder at %s refused: %s", ICE_LADDER,
                 d.last_warp_reason)
    return d.map_name() == ICE


#: Cells to stand on before asking for STRENGTH -- one either side of the
#: boulder's corridor, because which of them is reachable depends on which
#: side of (25,3) the descent left us. `use_strength` walks to the boulder's
#: own flank itself once it can SEE the boulder; this only has to put it on
#: screen.
BOULDER_VIEWPOINTS = ((24, 3), (27, 3), (22, 3), (28, 3))


def arm_strength(d) -> bool:
    """Turn STRENGTH on, from somewhere the boulder is actually spawned."""
    if d.state.flag("FLAG_SYS_USE_STRENGTH"):
        return True
    if d.use_strength():
        return True
    log.info("   strength refused (%s) -- walking into the boulder's "
             "corridor first", d.last_field_reason)
    for cell in BOULDER_VIEWPOINTS:
        arm(d, 240.0)
        try:
            if not d.goto(*cell, on_battle="fight"):
                continue
        except Exception as exc:  # noqa: BLE001
            log.debug("   viewpoint %s: %s", cell, str(exc)[:70])
            if d.in_battle():
                d.fight()
                d.advance_scene(40_000)
            continue
        if d.use_strength():
            return True
    return d.state.flag("FLAG_SYS_USE_STRENGTH")


# ---- the hunt --------------------------------------------------------------

def owed(col, name="SNORUNT") -> bool:
    return bool(missing_of(col, [name]))


def resolve_battle(d, col) -> int:
    """Play whatever is in front of us as a CATCH first. Dex delta."""
    before = col._caught_count()
    for _ in range(60):
        if d.state.battle_ready():
            break
        d.emu.tick(20)
    if d.in_battle():
        col.fight()
    d.advance_scene(40_000)
    own_input(d)
    return col._caught_count() - before


def pace_pocket(d, col, rec, budget, report_every=20) -> bool:
    """Walk laps of the ice-free pocket until SNORUNT lands or time runs out.

    Returns True when SNORUNT is registered. The map name is re-checked every
    iteration: the ladder home is one tile from the pocket, and a run that
    took it would otherwise spend the rest of the budget hunting SNORUNT in a
    room whose table has none.
    """
    deadline = time.time() + budget
    d.nav.surfing = False
    walkable, grass = ice_pocket(d, d.pos())
    log.info("   pocket off %s: %d ice-free cells, %d of them encounter "
             "tiles: %s", d.pos(), len(walkable), len(grass), grass)
    if not grass:
        log.info("   no ice-free encounter cell next to %s -- refusing to "
                 "slide", d.pos())
        return False
    paced, steps, battles, falls = set(), 0, 0, 0
    i = 0
    while time.time() < deadline:
        if not owed(col):
            log.info("   SNORUNT registered after %d encounters, %d steps "
                     "over %d cells %s", battles, steps, len(paced),
                     sorted(paced))
            return True
        if d.in_battle():
            battles += 1
            if resolve_battle(d, col) > 0:
                # `Collector.save` writes `d.state_path`, which IS the chain's
                # `--state`, and only once no script owns input -- so the
                # species is banked the moment it lands.
                col.save()
            continue
        if d.scene_active():
            own_input(d)
            continue
        if d.map_name() != ICE:
            falls += 1
            log.info("   left the Ice Room into %s %s -- climbing back",
                     d.map_name(), d.pos())
            if not descend_to_ice(d, col, rec):
                log.info("   cannot get back onto %s from %s %s", ICE,
                         d.map_name(), d.pos())
                return False
            walkable, grass = ice_pocket(d, d.pos())
            if not grass:
                return False
            continue
        pos = d.pos()
        # The pocket excludes warp cells, so standing on the ladder is normal
        # after a descent and is NOT a reason to re-derive: BFS can leave it
        # even though nothing may route through it.
        if pos not in walkable and not any(
                (pos[0] + dx, pos[1] + dy) in walkable
                for dx, dy in DIRS.values()):
            # Slid, or landed somewhere the last pocket did not cover.
            walkable, grass = ice_pocket(d, pos)
            log.info("   re-derived the pocket from %s: %d cells, %d "
                     "encounter tiles", pos, len(walkable), len(grass))
            if not grass:
                return False
            continue
        i += 1
        target = grass[(i * 7) % len(grass)]
        path = pocket_path(d, walkable, pos, target)
        if not path:
            continue
        ok = d.walk(path)
        if ok and d.map_name() == ICE:
            # The path is a verified BFS line inside the pocket, so on a
            # clean walk every cell on it was stepped on -- and an encounter
            # is rolled per step, which is the only thing being bought here.
            x, y = pos
            for letter in path:
                dx, dy = DIRS[letter]
                x, y = x + dx, y + dy
                paced.add((x, y))
            steps += len(path)
        else:
            moved = d.pos()
            steps += abs(moved[0] - pos[0]) + abs(moved[1] - pos[1])
            paced.add(moved)
            if not (d.in_battle() or d.scene_active()):
                log.debug("   walk %s from %s refused: %s", path, pos,
                          d.last_step_reason)
        if i % report_every == 0:
            log.info("   %d steps, %d encounters, %d cells touched, %d balls,"
                     " %.0fs left", steps, battles, len(paced), col.balls(),
                     deadline - time.time())
    log.info("   budget spent: %d steps, %d encounters, %d re-entries; "
             "cells paced %s", steps, battles, falls, sorted(paced))
    return not owed(col)


# ---- the leg ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True,
                    help="the chain's save -- MUTATED IN PLACE")
    ap.add_argument("--out", default=None,
                    help="extra savestate to bank as proof")
    ap.add_argument("--budget", type=float, default=2100.0,
                    help="seconds of pacing in the Ice Room")
    ap.add_argument("--tz", default=None,
                    help="force a POSIX TZ instead of picking one")
    ap.add_argument("--feed", default=None)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    state = Path(args.state)
    d = Driver(str(state))
    # `feed_name=None` on purpose: `Driver(state)` has already auto-attached a
    # feed named after the save's stem, and building a second LiveFeed for the
    # same name is what `LiveFeed._claim` hard-errors on. `--feed` is only for
    # a caller that wants a different name.
    col = Collector(d, feed_name=args.feed)

    if args.verify_only:
        caught, seen = col.target.dex_flags(d.state)
        index = {e.name.strip().upper(): e.natdex
                 for e in col.target.entries if e.natdex}
        for name in ("SNORUNT", "GLALIE", "SPHEAL"):
            nat = index.get(name)
            log.info("  %-8s %s", name,
                     "CAUGHT" if nat in caught else
                     "seen only" if nat in seen else "unknown")
        log.info("  dex %d caught, %d seen", len(caught), len(seen))
        return 0

    if state.name.startswith("line3") or state.name.startswith("milestone-"):
        ap.error("refusing to drive %s -- fork it first" % state.name)

    before = col._caught_count()
    log.info("start: %s %s, dex %d, %d balls", d.map_name(), d.pos(), before,
             col.balls())
    if not owed(col):
        log.info("SNORUNT is already registered -- nothing to do")
        return 0

    # CATCH OR RUN. Never let tactics have a wild turn: the lead is L100.
    install_policy(col)
    teach_boulder_solver_to_catch(col)
    unwedge(d)

    # ---- the tide, before anything walks
    if args.tz:
        os.environ["TZ"] = args.tz
        time.tzset()
        table = tide_table(d.emu)
        hour = game_hour(datetime.now().astimezone(), local_time_offset(d))
        tz = args.tz
        log.info("tide: TZ=%s forced -> game %02d:xx, table says %s", tz,
                 hour, "HIGH" if table[hour] else "LOW")
        if table[hour]:
            log.info("refusing to walk in: the Inner Room's descents are "
                     "water metatiles at HIGH tide "
                     "(field_control_avatar.c:696)")
            return 1
    else:
        tz, hour, room = tune_tide(d)
        log.info("tide: TZ=%s, game hour %02d, %d minutes of low tide left",
                 tz, hour, room)

    if d.underwater() and not surface(d):
        log.info("could not surface -- the leg cannot route from underwater")
        return 1

    rec = dungeon_of(ENTRANCE)
    if d.map_name() in rec.maps:
        # ALREADY INSIDE, so do NOT walk back out to the entrance room for a
        # fresher flag. That cost a run: restarted in the Lower Room's east
        # component at (28,11) -- one step from the Ice Room ladder -- the leg
        # asked for the entrance room instead, `warp_route` answered with the
        # (19,11) descent because `nav.reachable` crosses the boulder it does
        # not model, and `take_warp` then refused four times on a warp that
        # was never approachable. The flag is still the engine's answer for
        # the layout currently loaded; it is simply the last transition's.
        flag, table_high, hour = tide_report(d)
        log.info("already inside at %s %s: FLAG_SYS_SHOAL_TIDE=%s, table says "
                 "%s at game %02d:xx", d.map_name(), d.pos(), flag,
                 "HIGH" if table_high else "LOW", hour)
        d.nav.surfing = False
    else:
        _enable_surf(d)
        arm(d, 900.0)
        if not enter_dungeon(d, col, rec, budget=900.0):
            log.info("could not get into Shoal Cave from %s %s (%s)",
                     d.map_name(), d.pos(), d.last_goto_reason)
            return 1
        arm(d, 600.0)
        if not cross_dungeon(d, ENTRANCE, rec.maps):
            log.info("inside %s but not in the entrance room", d.map_name())
            return 1
        d.nav.surfing = False
        try:
            drift = d.sync_grid()
        except Exception as exc:  # noqa: BLE001
            log.info("sync_grid: %s", str(exc)[:80])
            drift = -1
        # THE FLAG IS THE ENGINE'S OWN ANSWER, and it is only recomputed by
        # the entrance room's ON_TRANSITION -- which has just run, because we
        # walked in. Reading it here rather than earlier is what makes it
        # evidence.
        flag, table_high, hour = tide_report(d)
        log.info("in %s %s: FLAG_SYS_SHOAL_TIDE=%s, table says %s at game "
                 "%02d:xx, %d cells of live grid drift", d.map_name(),
                 d.pos(), flag, "HIGH" if table_high else "LOW", hour, drift)
    if flag:
        log.info("HIGH tide -- the Inner Room's descents are water metatiles, "
                 "so no warp on them can fire and the Ice Room is unreachable "
                 "(field_control_avatar.c:696, 731-743)")
        d.save(str(state))
        return 1
    if d.map_name() != ICE:
        arm(d, 600.0)
        if not descend_to_ice(d, col, rec):
            log.info("the Ice Room chain broke on %s %s", d.map_name(),
                     d.pos())
            d.save(str(state))
            return 1
    try:
        log.info("   %d cells of grid drift in %s %s", d.sync_grid(),
                 d.map_name(), d.pos())
    except Exception as exc:  # noqa: BLE001
        log.debug("sync_grid: %s", str(exc)[:70])
    log.info("== %s %s for SNORUNT", d.map_name(), d.pos())

    got = pace_pocket(d, col, rec, args.budget)

    own_input(d)
    d.save(str(state))
    log.info("saved %s at %s %s", state, d.map_name(), d.pos())
    if args.out:
        try:
            d.save(args.out)
            log.info("banked %s", args.out)
        except Exception as exc:  # noqa: BLE001
            log.info("could not bank %s: %s", args.out, exc)
    flag, table_high, hour = tide_report(d)
    log.info("---- snorunt result ----")
    log.info("TZ=%s, game %02d:xx, table %s, FLAG_SYS_SHOAL_TIDE=%s",
             os.environ.get("TZ", "(host)"), hour,
             "HIGH" if table_high else "LOW", flag)
    log.info("dex %d -> %d (live)", before, col._caught_count())
    log.info("  SNORUNT %s", "missing" if owed(col) else "CAUGHT")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
