#!/usr/bin/env python
"""Register RAYQUAZA from the Sky Pillar apex. A chain.py LEG.

Contract (see the chain notes): takes `--state PATH`, mutates that file IN
PLACE, skips the species when the live dex already has it CAUGHT, works from
any starting map, and exits 0 when it did its job or had nothing to do.

------------------------------------------------------------------ the route

The voyage to `SkyPillar_1F` is `skypillar_grind.py`'s, reused rather than
reinvented: Mossdeep -> Route127 -> 128 -> 129 -> 130 -> 131, then
**`sync_grid()`**, then the door. `Route131_MapScripts` runs
`MAP_SCRIPT_ON_TRANSITION -> call_if_set FLAG_SYS_GAME_CLEAR ->
setmaplayoutindex 320` (`pret/data/maps/Route131/scripts.inc:1-11`), and nav
decodes the SHIPPED layout, on which the door cell (36,6) sits in a sealed
lagoon. Post-Champion the live layout differs in 233 cells and the channel is
open, so the sync is what makes the door reachable at all.

------------------------------------------------------- the crumbling floors

`MB_CRACKED_FLOOR` is 0xD2 and `MB_CRACKED_FLOOR_HOLE` is 0x66
(`pret/include/constants/metatile_behaviors.h:214`, `:106`). Only 2F and 4F
have them: measured off the live nav grid, 2F has 38 and 4F has 34, while 1F,
3F, 5F and the Top have ZERO. Both floors carry

    map_script MAP_SCRIPT_ON_FRAME_TABLE, CaveHole_CheckFallDownHole
    map_script MAP_SCRIPT_ON_TRANSITION, CaveHole_FixCrackedGround
    setstepcallback 7
    setholewarp MAP_SKY_PILLAR_{1,3}F, 255, 0, 0

(`pret/data/maps/SkyPillar_2F/scripts.inc`, `.../SkyPillar_4F/scripts.inc`).
Note there is NO `setmaplayoutindex` on any pillar floor, so unlike Route131
the shipped layout IS the live one and `sync_grid` changes nothing here --
verified, 0 cells.

`PerStepCallback_806A07C` (`pret/src/field_tasks.c:669`) zeroes
`VAR_ICE_STEP_COUNT` when the destination tile is a cracked floor and
`GetPlayerSpeed() != 4` (`:692-696`), and unconditionally while standing on a
hole (`:684-686`). `CaveHole_CheckFallDownHole` is
`map_script_2 VAR_ICE_STEP_COUNT, 0, EventScript_FallDownHole`
(`pret/data/scripts/cave_hole.inc:2`) and that script is `warphole
MAP_UNDEFINED` (`:9-18`), which against the floor's own `setholewarp` lands
the player at the SAME (x,y) one floor down. `CaveHole_FixCrackedGround`
(`:5-7`) restores the var on every map entry, so collapsed tiles are never
permanently spent and a failed run may simply be retried.

`GetPlayerSpeed()` is `sMachBikeSpeeds[bikeFrameCounter]` = {1,2,4}, and the
counter only reaches 2 after two tiles of CONTINUOUS held movement -- which
is why `Driver.climb_slope` insists on one long press and why this script
does too. Speed is READ from `gPlayerAvatar+0x0A` via `Driver.bike_speed()`
rather than counted, because a refused step resets it invisibly.

------------------------------------------------------- why a fall is needed

Pure 4-connectivity on the live grids (cracks treated as passable):

  1F  (6,13) <-> (10,1)    one region, no cracks           -> walk
  2F  (10,1) <-> (3,1)     ONLY via cracks (121 vs 17/27)  -> MACH BIKE
  3F  (3,1) <-> (11,1)     103 cells; **(7,1) is a sealed 11-cell pocket**
  4F  (11,1) region 98 cells: reaches NEITHER (7,1) nor (3,1)
      (7,1) <-> (3,1)      a separate 12-cell region
  5F  (3,1) <-> (10,1)     no cracks                        -> walk
  Top (16,14) <-> (14,7)   no cracks                        -> walk

So 4F's eastern half cannot walk to (3,1), and 3F's (7,1) pocket cannot be
walked into from anywhere. The only link between them is a DELIBERATE FALL
through 4F (6,4) or (7,4): those land at the same coords in 3F's 11-cell
pocket, the one region containing (7,1), which warps to 4F (7,1), whose
12-cell region contains (3,1) -> 5F. Falling anywhere else on 4F -- (4,4),
(9,4) or any of the southern cracks -- lands in 3F's BIG region and merely
returns you to the start, which is why the fall tile is chosen and not taken
as it comes.

Row y=4 on 4F, read off the live grid, is the corridor that matters:

    x:  0  1  2  3  4  5  6  7  8  9 10 11 12 13
        #  #  .  .  x  .  x  x  .  x  #  .  .  .

with y=3 and y=5 walled across x=4..9, so x=2..9 is one tile tall. (9,4) is a
dead end. (5,4) and (8,4) are the only solid rests inside it, and both are
reachable only THROUGH a cracked tile, so the corridor is entered at speed
and left by releasing the key on a solid tile.

A fall is entered SLOWLY on purpose: from a standstill the first tile is
callback 0 (speed 1), so a single step off (8,4) into (7,4) drops through
without any of the crossing problem.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, _HOLD  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

log = logging.getLogger("rayquaza")

TARGET = "RAYQUAZA"

#: Mossdeep is the nearest flyable town with a Center; Pacifidlog was never
#: visited on this line so the Fly map greys it out.
HEAL_TOWN = "MossdeepCity"
SEA_CHAIN = ["Route127", "Route128", "Route129", "Route130", "Route131"]
#: The door sits in a one-tile gap: (36,5) is rock and (36,7) is open water,
#: so it is always approached from BELOW.
PILLAR_DOOR = (36, 6)
DOOR_APPROACH = (36, 7)

CRACK = 0xD2
HOLE = 0x66

#: Warp cells, floor by floor, read from each map's own warp table.
W_1F_UP = (10, 1)        # -> 2F (10,1)
W_2F_UP = (3, 1)         # -> 3F (3,1)
W_3F_UP_EAST = (11, 1)   # -> 4F (11,1)
W_3F_UP_POCKET = (7, 1)  # -> 4F (7,1)   (only reachable after the fall)
W_4F_UP = (3, 1)         # -> 5F (3,1)
W_5F_UP = (10, 1)        # -> Top (16,14)
RAYQUAZA_AT = (14, 7)    # the object; talk to it from below


def dex_has(d, species=TARGET) -> bool:
    """Is `species` flagged CAUGHT in the LIVE dex right now?"""
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    caught, _seen = t.dex_flags(d.state)
    return species in {str(c) for c in caught}


def bank(d, path) -> None:
    if not path:
        return
    try:
        d.save(path)
        log.info("banked %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("save failed: %s", exc)


# --------------------------------------------------------------- the bike

BIKE_SHOP_WARP = ("MauvilleCity", 35, 5)
RYDEL_CELLS = ((2, 5), (2, 4), (3, 5))


def held_bike(d) -> str:
    try:
        pocket = {str(k).upper() for k in (d.state.bag().get("key_items") or {})}
    except Exception:  # noqa: BLE001
        return ""
    for name in ("MACH BIKE", "ACRO BIKE"):
        if name in pocket:
            return name
    return ""


def swap_bike(d, want: str) -> bool:
    """Hold `want`, exchanging at Rydel's if we must. Rydel EXCHANGES, so the
    other bike leaving the bag is proof of success rather than a loss."""
    have = held_bike(d)
    if have == want:
        log.info("bike: already holding %s", want)
        return True
    if not have:
        log.info("bike: NEITHER bike in the bag")
        return False
    name, wx, wy = BIKE_SHOP_WARP
    log.info("bike: holding %s, want %s -- to Rydel's", have, want)
    if d.map_name() != name:
        # SURFACE FIRST. Fly is refused on MAP_TYPE_UNDERWATER
        # (`Overworld_MapTypeAllowsTeleportAndFly`), and this save is banked on
        # `Underwater1`. `Driver.fly_to` dives for us (trek.py:2898-2899), but
        # `flyable_here()`/`step_outside()` do NOT surface, so calling them
        # first is what made the leg die on
        # "indoors -- Underwater1 is MAP_TYPE_UNDERWATER".
        if not to_open_air(d):
            log.info("bike: could not reach open air from %s", d.map_name())
        if not d.fly_to(name) and not d.travel(name, on_battle="fight"):
            log.info("bike: could not reach %s (at %s)", name, d.map_name())
            return False
    if not d.take_warp(wx, wy):
        log.info("bike: could not enter the shop (%s)", d.last_warp_reason)
        return False
    for cx, cy in RYDEL_CELLS:
        try:
            d.talk_to(cx, cy)
        except Exception as exc:  # noqa: BLE001
            log.debug("shop talk (%d,%d): %s", cx, cy, str(exc)[:70])
        d.advance_scene(40_000)
        for _ in range(8):
            if held_bike(d) == want:
                break
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
        if held_bike(d) == want:
            log.info("bike: EXCHANGED, now holding %s", want)
            d.flight.step_outside()
            return True
    log.info("bike: still holding %s", held_bike(d) or "neither")
    return False


# --------------------------------------------------------------- the rides

#: Frames per tile for movement callback index 0/1/2. `sMachBikeSpeedCallbacks`
#: is {Speed1 = 16 frames/tile, Speed2 = 8, Speed4 = 4} and
#: `MachBikeTransition_TrySpeedUp` moves with `[counter]` then increments it
#: (bike.c:218-257). So a run from a standstill is 16, 8, then 4 forever, and
#: the FIRST cracked tile must be the third tile of a continuous hold.
FRAMES = (16, 8, 4)


def leg_frames(tiles: int, from_rest: bool) -> int:
    """Frames to hold a direction for `tiles` tiles.

    From a standstill the counter climbs 0,1,2,2,...; after a TURN AT FULL
    SPEED it is already 2, because `MACH_TRANS_START_MOVING` is
    `MachBikeTransition_TrySlowDown` and `counter = --bikeSpeed` leaves a
    full-speed rider at counter 2 (bike.c:76-82). One turn per straight tile
    is therefore free -- which is the only reason these crossings exist.
    """
    if not from_rest:
        return 4 * tiles
    total = 0
    for i in range(tiles):
        total += FRAMES[min(i, 2)]
    return total


def ride(d, direction: str, frames: int) -> tuple:
    """ONE held press. Momentum only builds while the key stays down, so this
    never splits the hold (`Driver.climb_slope`, trek.py:301-304)."""
    d.emu.run_sequence(f"{_HOLD[direction]}:{frames}")
    d.settle(60)
    return d.pos()


def ride_path(d, legs, label="") -> tuple:
    """ONE continuous hold with mid-ride TURNS: `legs` is [(dir, tiles), ...].

    The whole run goes into a single `run_sequence`, so the key state changes
    from one direction straight to the next with NO idle frame between. That
    matters: an idle frame is no input at all, `Bike_SetBikeStill` kills the
    counter (bike.c:244), and the next cracked tile is then entered at
    callback 0 and drops the player a floor. Splitting these rides into one
    press per leg is exactly the bug that fell through 2F on the first pass.
    """
    parts = []
    for i, (direction, tiles) in enumerate(legs):
        parts.append(f"{_HOLD[direction]}:{leg_frames(tiles, i == 0)}")
    seq = " ".join(parts)
    before = d.pos()
    log.info("%s ride: %s from %s", label, seq, before)
    d.emu.run_sequence(seq)
    d.settle(90)
    after = d.pos()
    log.info("%s ride: %s -> %s speed %d map %s", label, before, after,
             d.bike_speed(), d.map_name())
    return after


def step(d, direction: str) -> tuple:
    """One slow step: callback 0, speed 1 -- what a deliberate fall wants."""
    d.emu.run_sequence(f"{_HOLD[direction]}:12")
    d.settle(60)
    return d.pos()


def behavior_at(d, m, x, y):
    g = d.nav.grid(m)
    if not (0 <= y < len(g) and 0 <= x < len(g[0])):
        return None
    return g[y][x].behavior


def walk_to(d, x, y, label="") -> bool:
    """Plain nav walk on a crack-free floor."""
    if d.pos() == (x, y):
        return True
    ok = d.goto(x, y, label=label, on_battle="fight")
    if not ok:
        log.info("goto %s (%d,%d) failed: %s", label, x, y, d.last_goto_reason)
    return d.pos() == (x, y)


# --------------------------------------------------------------- the voyage

def surfacable(d, m, x, y) -> bool:
    """Can `dive()` come UP from this cell? `Driver.dive` refuses when the
    STANDING cell's behaviour is in `nav.NO_SURFACING` = {0x19, 0x2A}
    (`pokeagent/nav.py:70`, checked at `trek.py:1734-1736`)."""
    from pokeagent import nav as nav_mod
    c = d.nav.cell(m, x, y)
    return c is not None and c.collision == 0 and c.behavior not in nav_mod.NO_SURFACING


def surface(d, radius=40) -> bool:
    """Come up from MAP_TYPE_UNDERWATER, walking to a surfacable ceiling first.

    This save is banked at `Underwater1 (10,33)`, whose behaviour is 0x2A
    (`SEAWEED_NO_SURFACING`), so `dive()` refuses there and every script that
    opens by flying dies on "indoors -- Underwater1 is MAP_TYPE_UNDERWATER".
    `fly_to` does call `dive()` for us (trek.py:2898), but from a cell it can
    never surface from. The nearest surfacable cell is (13,33), behaviour 0x0.
    """
    from collections import deque

    if not d.underwater():
        return True
    m = d.map_name()
    if surfacable(d, m, *d.pos()) and d.dive():
        return not d.underwater()
    g = d.nav.grid(m)
    H, W = len(g), len(g[0])
    start = d.pos()
    seen = {start}
    q = deque([start])
    targets = []
    while q and len(targets) < 8:
        x, y = q.popleft()
        if (x, y) != start and surfacable(d, m, x, y):
            targets.append((x, y))
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            t = (x + dx, y + dy)
            if (0 <= t[0] < W and 0 <= t[1] < H and g[t[1]][t[0]].collision == 0
                    and t not in seen and abs(t[0] - start[0]) + abs(t[1] - start[1]) <= radius):
                seen.add(t)
                q.append(t)
    for tx, ty in targets:
        if not walk_to(d, tx, ty, label="surfacable"):
            continue
        if d.dive():
            log.info("surfaced at (%d,%d) -> %s %s", tx, ty, d.map_name(), d.pos())
            return not d.underwater()
        log.info("dive refused at (%d,%d): %s", tx, ty,
                 getattr(d, "last_field_reason", "?"))
    return not d.underwater()


def to_open_air(d) -> bool:
    """Somewhere Fly accepts: not underwater, not indoors."""
    if d.underwater() and not surface(d):
        log.info("could not surface from %s %s", d.map_name(), d.pos())
        return False
    if d.flight.flyable_here():
        return True
    d.flight.step_outside()
    return d.flight.flyable_here()


def open_the_pillar(d) -> int:
    """Push Route131's post-Champion layout into nav."""
    n = 0
    try:
        n = d.sync_grid()
    except Exception as exc:  # noqa: BLE001
        log.warning("sync_grid failed: %s", exc)
    log.info("sync_grid on %s: %d cells", d.map_name(), n)
    return n


def sail_to_pillar(d, budget_s=900.0) -> bool:
    """Mossdeep -> Route131 -> SkyPillar_1F."""
    if d.map_name().startswith("SkyPillar"):
        return True
    if not to_open_air(d):
        log.info("cannot reach open air from %s", d.map_name())
    if d.map_name() != HEAL_TOWN:
        if not d.fly_to(HEAL_TOWN):
            log.info("fly to %s failed: %s", HEAL_TOWN, d.last_fly_reason)
    try:
        d.heal()
    except Exception as exc:  # noqa: BLE001
        log.debug("heal: %s", exc)
    t0 = time.time()
    for dest in SEA_CHAIN:
        if time.time() - t0 > budget_s:
            log.info("sail budget spent at %s", d.map_name())
            return False
        if d.map_name() == dest:
            continue
        ok = False
        for _try in range(4):
            try:
                ok = d.travel(dest, on_battle="fight")
            except Exception as exc:  # noqa: BLE001
                log.debug("travel %s: %s", dest, str(exc)[:80])
                ok = False
            if d.map_name() == dest:
                ok = True
                break
        log.info("leg -> %s: %s (at %s %s)", dest, ok, d.map_name(), d.pos())
        if d.map_name() != dest:
            return False
    # The door only exists on the live layout.
    open_the_pillar(d)
    if not walk_to(d, *DOOR_APPROACH, label="door-approach"):
        log.info("could not stand below the pillar door (at %s)", d.pos())
        return False
    if not d.take_warp(*PILLAR_DOOR):
        log.info("door warp refused: %s", d.last_warp_reason)
        return False
    log.info("through the door: %s %s", d.map_name(), d.pos())
    # Entrance -> Outside -> 1F, using each map's OWN warp table:
    #   SkyPillar_Entrance warp 0 (6,16)  -> Route131          (the way BACK)
    #   SkyPillar_Entrance warp 1 (14,4)  -> SkyPillar_Outside
    #   SkyPillar_Outside  warp 0 (17,13) -> SkyPillar_Entrance
    #   SkyPillar_Outside  warp 1 (14,5)  -> SkyPillar_1F
    # The first pass used (6,16) and (17,14) and walked straight back out to
    # Route131: (6,16) IS the door we arrive on, and standing on a warp does
    # not fire it, so re-taking it only steps off and back through.
    if d.map_name() == "SkyPillar_Entrance":
        walk_to(d, 14, 5, label="entrance-approach")
        if not d.take_warp(14, 4):
            log.info("entrance warp refused: %s", d.last_warp_reason)
    if d.map_name() == "SkyPillar_Outside":
        walk_to(d, 14, 6, label="outside-approach")
        if not d.take_warp(14, 5):
            log.info("outside warp refused: %s", d.last_warp_reason)
    log.info("at %s %s", d.map_name(), d.pos())
    return d.map_name() == "SkyPillar_1F"


# --------------------------------------------------------------- the climb

def cross_2f(d) -> bool:
    """2F -> the (3,1) staircase, in ONE continuous ride.

    Live 2F rows (`.`=solid, `x`=cracked, `#`=wall), x=0..13:

        2  ........#.....      7  ..xx######...#     11 x..#xx...#xxxx
        3  .....#.##.....      8  xxxx######....     12 ..#.xx....xxxx
        4  ......#...#...      9  xxxx######....     13 ...xxx.......#
        5  ....######xxxx     10  xx..######xx..

    Row 2 is split at x=8 and row 4 at x=6, so the west half -- the half with
    (3,1) -- is reached only by going all the way south and back up. The run
    starts from (11,2):

      D x11  (11,3)..(11,13)  crossing (11,5) (11,6) (11,10) (11,11) (11,12)
      L x10  (10,13)..(1,13)  crossing (5,13) (4,13) (3,13)
      U x6   (1,12)..(1,7)    crossing (1,10) (1,9) (1,8)
      R x1   (2,7)            crossing (2,7)      <- one tile, then turn
      U x5   (2,6)..(2,2)

    Only the first leg starts from rest, so only it pays 16+8 frames; every
    turn afterwards happens AT FULL SPEED and keeps callback 2.
    """
    if d.map_name() != "SkyPillar_2F":
        return False
    if not d.on_bike() and not d.mount_bike():
        log.info("2F: no mach bike (%s)", d.last_bike_reason)
        return False
    if not walk_to(d, 11, 2, label="2F-runup"):
        log.info("2F: could not reach the run-up cell (11,2), at %s", d.pos())
        return False
    ride_path(d, [("D", 11), ("L", 10), ("U", 6), ("R", 1), ("U", 5)],
              label="2F")
    if d.map_name() != "SkyPillar_2F":
        log.info("2F: left the floor mid-ride -> %s %s", d.map_name(), d.pos())
        return False
    if d.pos() != (2, 2):
        log.info("2F: ride ended at %s, wanted (2,2)", d.pos())
    if not walk_to(d, 3, 2, label="2F-stairs-approach"):
        return False
    d.take_warp(*W_2F_UP)
    return d.map_name() == "SkyPillar_3F"


def fall_from_4f(d) -> bool:
    """4F east -> the 3F (7,1) pocket, by falling through (7,4).

    Live 4F rows, x=0..13:

        1  ###.###.###.##      6  ....######.xxx     11 ...x.#.x.#xx..
        2  ........#.....      7  .x#x######.x#.     12 ..#.x.x.x.xx..
        3  xx########...#      8  .x#x######...#     13 ...x.x.#.xxx..
        4  ##..x.xx.x#...      9  #x#x######....
        5  .#..######.xxx     10  #x..######....

    Row 4's x=2..9 strip is one tile tall (y=3 and y=5 are wall across x=4..9)
    and is the ONLY approach to (6,4)/(7,4). Two rides, because the middle
    needs a standstill on solid ground at (12,8):

      ride A from (11,1):  D x7   (11,2)..(11,8)  crossing (11,5) (11,6) (11,7)
      step R:              (12,8)                 solid, resets the counter
      ride B from (12,8):  D x4   (12,9)..(12,12)
                           L x9   (11,12)..(3,12) crossing (11,12) (10,12)
                                                           (8,12) (6,12) (4,12)
                           U x8   (3,11)..(3,4)   crossing (3,11) (3,9) (3,8)
                                                           (3,7)
                           R x5   (4,4)..(8,4)    crossing (4,4) (6,4) (7,4)

    The R leg stops ON (8,4), which is solid: releasing there is safe, and it
    is the only rest cell adjacent to a pocket crack. Then ONE slow step west
    enters (7,4) at callback 0, which is the deliberate fall.
    """
    if d.map_name() != "SkyPillar_4F":
        return False
    if not d.on_bike() and not d.mount_bike():
        log.info("4F: no mach bike (%s)", d.last_bike_reason)
        return False
    if not walk_to(d, 11, 1, label="4F-runup"):
        log.info("4F: not at the (11,1) entrance, at %s", d.pos())
    ride_path(d, [("D", 7)], label="4F-A")
    if d.map_name() != "SkyPillar_4F":
        log.info("4F: fell during ride A -> %s %s", d.map_name(), d.pos())
        return False
    if d.pos() != (11, 8):
        log.info("4F: ride A ended at %s, wanted (11,8)", d.pos())
    step(d, "R")
    if d.pos() != (12, 8):
        log.info("4F: could not reach (12,8), at %s", d.pos())
    ride_path(d, [("D", 4), ("L", 9), ("U", 8), ("R", 5)], label="4F-B")
    if d.map_name() != "SkyPillar_4F":
        log.info("4F: fell during ride B -> %s %s", d.map_name(), d.pos())
        return d.map_name() == "SkyPillar_3F" and d.pos() in ((6, 4), (7, 4))
    pos = d.pos()
    for rest, direction, crack in (((8, 4), "L", (7, 4)), ((5, 4), "R", (6, 4))):
        if pos == rest:
            log.info("4F: at %s, stepping %s into %s to fall on purpose",
                     rest, direction, crack)
            step(d, direction)
            d.advance_scene(40_000)
            log.info("4F: after the fall -> %s %s", d.map_name(), d.pos())
            return d.map_name() == "SkyPillar_3F"
    log.info("4F: ride B ended at %s, not a rest beside a pocket crack", pos)
    return False


def climb(d) -> bool:
    """1F -> Top, with the fall detour on 4F. Returns True at the apex."""
    for _round in range(6):
        m = d.map_name()
        log.info("climb: at %s %s", m, d.pos())
        if m == "SkyPillar_Top":
            return True
        if m == "SkyPillar_1F":
            if not walk_to(d, *W_1F_UP, label="1F-stairs"):
                return False
            d.take_warp(*W_1F_UP)
        elif m == "SkyPillar_2F":
            if not cross_2f(d):
                log.info("2F crossing failed at %s", d.pos())
            if d.map_name() == "SkyPillar_2F":
                # Fell or stalled: try the stairs if we happen to be on them.
                if d.pos() != W_2F_UP:
                    return False
                d.take_warp(*W_2F_UP)
        elif m == "SkyPillar_3F":
            # Which 3F region are we in? The pocket contains (7,1).
            if d.pos()[0] in (6, 7) and d.pos()[1] >= 2:
                if walk_to(d, *W_3F_UP_POCKET, label="3F-pocket-stairs"):
                    d.take_warp(*W_3F_UP_POCKET)
                    continue
            if walk_to(d, *W_3F_UP_POCKET, label="3F-pocket-stairs"):
                d.take_warp(*W_3F_UP_POCKET)
                continue
            if walk_to(d, *W_3F_UP_EAST, label="3F-east-stairs"):
                d.take_warp(*W_3F_UP_EAST)
                continue
            return False
        elif m == "SkyPillar_4F":
            # West region? Then (3,1) is a plain walk to 5F.
            if walk_to(d, *W_4F_UP, label="4F-west-stairs"):
                d.take_warp(*W_4F_UP)
                continue
            if not fall_from_4f(d):
                log.info("4F: could not reach the pocket (at %s %s)",
                         d.map_name(), d.pos())
                return False
        elif m == "SkyPillar_5F":
            if not walk_to(d, *W_5F_UP, label="5F-stairs"):
                return False
            d.take_warp(*W_5F_UP)
        else:
            log.info("climb: unexpected map %s", m)
            return False
    return d.map_name() == "SkyPillar_Top"


# --------------------------------------------------------------- the battle

def catch_policy(d, balls=("MASTER BALL", "ULTRA BALL")):
    """Throw the best ball in the bag, every turn, and NEVER attack.

    RAYQUAZA cannot flee (a scripted `setwildbattle`) and its `catchRate` is 3
    (`pret/src/data/pokemon/base_stats.h`, SPECIES_RAYQUAZA), so at full HP an
    ULTRA BALL is only `a = (1/3)*3*2 = 2`. Attacking with an L100 lead would
    KO it outright, so the policy is pure throwing: the odds per ball are low
    but the battle is unlimited, and 75 ULTRA BALLS is ~44% cumulative.
    A MASTER BALL, if one is present, always succeeds.
    """
    def have(name):
        try:
            pocket = d.state.bag().get("poke_balls") or {}
        except Exception:  # noqa: BLE001
            return 0
        for k, v in pocket.items():
            if str(k).upper() == name:
                return int(v or 0)
        return 0

    def decide(_frame):
        for name in balls:
            if have(name) > 0:
                return ("ball", name)
        return None

    return decide


def fight_rayquaza(d) -> bool:
    """Talk to the apex object and throw balls until it is ours."""
    if d.map_name() != "SkyPillar_Top":
        return False
    # Stand below it and face up: the object is at (14,7).
    walk_to(d, 14, 8, label="rayquaza-approach")
    d.battle_policy = catch_policy(d)
    log.info("apex: at %s, talking to %s", d.pos(), RAYQUAZA_AT)
    try:
        d.talk_to(*RAYQUAZA_AT)
    except Exception as exc:  # noqa: BLE001
        log.info("talk_to failed: %s", str(exc)[:120])
    d.advance_scene(60_000)
    # The scripted battle starts itself; play it with the throwing policy.
    try:
        d.fight(policy=d.battle_policy, max_frames=2_000_000)
    except Exception as exc:  # noqa: BLE001
        log.info("fight raised: %s", str(exc)[:160])
    d.advance_scene(60_000)
    return dex_has(d)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True,
                    help="save to drive AND mutate in place")
    ap.add_argument("--feed", default=None,
                    help="live feed name (unique per process)")
    ap.add_argument("--budget", type=float, default=3600.0)
    a = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    d = Driver(a.state, live=a.feed or False)
    if d.at_title():
        log.info("title screen; resuming -> %s", d.resume_from_title())

    if dex_has(d):
        log.info("%s already CAUGHT -- nothing to do", TARGET)
        return 0

    log.info("start: %s %s | dex-has-%s False", d.map_name(), d.pos(), TARGET)
    balls = d.state.bag().get("poke_balls") or {}
    log.info("balls: %s", dict(balls))

    if not swap_bike(d, "MACH BIKE"):
        log.info("FAIL: the climb needs the MACH BIKE")
        return 1
    bank(d, a.state)

    if not sail_to_pillar(d):
        log.info("FAIL: could not reach SkyPillar_1F (at %s %s)",
                 d.map_name(), d.pos())
        return 1
    bank(d, a.state)

    if not climb(d):
        log.info("FAIL: could not reach the apex (at %s %s)",
                 d.map_name(), d.pos())
        bank(d, a.state)
        return 1
    bank(d, a.state)

    got = fight_rayquaza(d)
    bank(d, a.state)
    log.info("RESULT %s caught=%s (at %s %s)", TARGET, got,
             d.map_name(), d.pos())
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
