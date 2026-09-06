#!/usr/bin/env python
"""Mt Pyre and Shoal Cave: five wild species and the game's only SEA INCENSE.

    scripts/pyre_shoal.py --state saves/mine.state --legs pyre,shoal

What is here and why it needs its own script rather than a `collect.py` sweep:

* **MT PYRE** owes VULPIX (MtPyre_Exterior, 20% across three level slots),
  DUSKULL and CHIMECHO (both MtPyre_Summit, 13% and 2%). The interior floors
  1F-6F also list DUSKULL but only at 10%, so the Summit is the right floor
  for both of its species and the interior is worth no pacing time at all --
  every rate read out of `docs/gen3/guide/encounters.json`, not from memory.
* **SHOAL CAVE** owes SPHEAL (50% in every room) and SNORUNT (10%, and ONLY
  in `ShoalCave_LowTideIceRoom`).
* **The SEA INCENSE** sits in an item ball at `MtPyre_4F (3,11)`
  (`pret/data/maps/MtPyre_4F/map.json:27-39`, `finditem ITEM_SEA_INCENSE` at
  `pret/data/item_ball_scripts.inc:401-402`). It is the only one in the game
  and it is load-bearing for somebody else's job: an Azurill egg hatches as
  MARILL unless a Day Care parent holds it, silently, with no message
  (`pret/src/daycare.c:602-622`).

Three things about the geography had to be derived rather than assumed, and
all three had already cost a naive attempt its budget:

1. **Mt Pyre's floors are not simply connected.** The item ball's component on
   4F is entered from ONE place: the hole at `MtPyre_3F (1,12)`, which is in
   turn in a different 3F component from the stairs up from 2F. Arriving on 4F
   the obvious way -- 1F -> 2F -> 3F(10,1) -> 4F(2,5) -- lands in a 46-cell
   pocket from which no neighbour of (3,11) is reachable at all. `warp_route`
   below searches the shipped warp graph with per-component reachability, so
   the chain is derived from `data/maps/*` every run instead of memorised.
2. **The Shoal Cave Ice Room is behind a five-warp detour.** The Inner Room
   has five walkable components. From the entrance you land in the one that
   only reaches the Lower Room's 15-cell dead end; the Ice Room door at
   `ShoalCave_LowTideLowerRoom (28,11)` is in a different Lower Room
   component, reached by going Inner -> Stairs -> Inner again. Same searcher
   finds it.
3. **The tide is the host wall clock, and it decides whether SNORUNT exists.**
   See `tide_now` and `retune_for_low_tide`.

A cave floor classifies as `kind == "grass"` because `behaviors.kind` returns
that for any land-encounter behaviour (`pokeagent/behaviors.py:155`), so
`Collector.pace_map(deadline, "grass")` is the correct pacer inside Mt Pyre
and Shoal Cave as well as out on the Exterior's real grass.
"""

import argparse
import logging
import os
import struct
import sys
import time
from collections import deque, namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402
from collect import Collector  # noqa: E402
from share_grind import unwedge  # noqa: E402
# The item-ball and surf helpers already carry their lessons in their
# docstrings -- an item ball is an `object_event` that blocks its own cell, so
# it is `talk_to` plus A and never `goto` -- and a second copy of either here
# would be a second convention.
from unlocks import _enable_surf, _has, _pick_up  # noqa: E402

log = logging.getLogger("pyre_shoal")

#: The one item this script must not come home without.
SEA_INCENSE = "SEA INCENSE"
#: Its ball, from `pret/data/maps/MtPyre_4F/map.json:27-39`.
INCENSE_BALL = ("MtPyre_4F", 3, 11)

#: Mt Pyre's interior stack.
PYRE_INTERIOR = ("MtPyre_1F", "MtPyre_2F", "MtPyre_3F", "MtPyre_4F",
                 "MtPyre_5F", "MtPyre_6F")
#: Shoal Cave's five rooms. The `HighTide*` map headers are vestigial -- they
#: have no warp events at all -- because the tide swaps the LAYOUT of the
#: LowTide maps rather than moving the player to a different map. See
#: `tide_now`.
SHOAL_ROOMS = ("ShoalCave_LowTideEntranceRoom", "ShoalCave_LowTideInnerRoom",
               "ShoalCave_LowTideStairsRoom", "ShoalCave_LowTideLowerRoom",
               "ShoalCave_LowTideIceRoom")

#: A dungeon whose interior `travel` MUST NOT be asked to plan.
#:
#: `nav.route_legs` believes every warp event in a map header is a door, and
#: half of Mt Pyre's are hole LANDINGS that never fire (see `warp_route`). Ask
#: `travel` to leave MtPyre_4F and it plans through one, walks to the tile and
#: presses a direction at it: measured pinned at `MtPyre_3F (2,10)` for 150
#: seconds with frames advancing and the player not moving, until the whole
#: run's budget was gone. Everything inside one of these is navigated with
#: `warp_route`, and `travel` is only ever asked about the OUTSIDE map.
Dungeon = namedtuple(
    "Dungeon", "maps door_map inner_door outside outer_door")

DUNGEONS = (
    Dungeon(frozenset(PYRE_INTERIOR + ("MtPyre_Exterior", "MtPyre_Summit")),
            "MtPyre_1F", (17, 18), "Route122", (22, 29)),
    Dungeon(frozenset(SHOAL_ROOMS),
            "ShoalCave_LowTideEntranceRoom", (20, 30), "Route125", (22, 19)),
)


def dungeon_of(map_name):
    """The `Dungeon` this map belongs to, or None for the overworld."""
    for rec in DUNGEONS:
        if map_name in rec.maps:
            return rec
    return None


# ---- the tide ------------------------------------------------------------
#
# `UpdateShoalTideFlag` (pret/src/time_events.c:54-92) is a plain table
# lookup on `gLocalTime.hours`: 1 sets FLAG_SYS_SHOAL_TIDE (high water), 0
# clears it. It runs from the ON_TRANSITION script of the entrance room
# (pret/data/maps/ShoalCave_LowTideEntranceRoom/scripts.inc:5-16), which then
# picks the layout -- `setmaplayoutindex 169` at high tide, 165 at low -- and
# the Inner Room does the same with 170/166.
#
# THE MAP NAME NEVER CHANGES, so the encounter table does not either: SPHEAL
# is huntable at any tide. SNORUNT is not, and the reason is a hard engine
# rule rather than a wall: a warp event only fires when the METATILE
# BEHAVIOUR under the player is a warp behaviour
# (`IsWarpMetatileBehavior`, pret/src/field_control_avatar.c:696 and 731-743).
# At high tide the Inner Room's descents to the Lower Room are swapped to
# water metatiles at elevation 1, so the map header's warps are still listed
# and cannot be triggered -- and the Ice Room, the only place SNORUNT lives,
# is unreachable. Surfing onto the tile does not help; the behaviour is what
# is checked.
#
# `gLocalTime = RTC - gSaveBlock2.localTimeOffset` (pret/src/rtc.c:320-324,
# the subtraction with borrows at 293-318), and libmgba reads the RTC through
# `localtime_r` on the host clock. So the in-game hour -- and the tide -- is a
# function of TZ, which this script sets rather than waiting hours for.

#: `struct Time` is `s16 days; s8 hours, minutes, seconds` (global.h:758-764).
_TIME = struct.Struct("<hbbb")
#: `struct SaveBlock2.localTimeOffset` (global.h:860).
_LOCAL_TIME_OFFSET = 0x98


def tide_table(emu) -> list:
    """The 24-entry table `UpdateShoalTideFlag` indexes, read from the ROM.

    The array is function-local rodata with no symbol of its own, so it is
    found through the literal pool inside the function: the only word in
    those 0x50 bytes that points into ROM and whose first 24 bytes are all 0
    or 1. Derived rather than transcribed, because a transcribed copy of a
    ROM table is the thing this harness exists not to do.
    """
    base = emu.resolve("UpdateShoalTideFlag")
    body = emu.read(base, 0x50)
    for off in range(0, 0x50 - 3, 4):
        word = struct.unpack_from("<I", body, off)[0]
        if not 0x08000000 <= word < 0x09000000:
            continue
        table = list(emu.read(word, 24))
        if all(b in (0, 1) for b in table):
            return table
    raise RuntimeError("could not find the tide table in UpdateShoalTideFlag")


def local_time_offset(d) -> tuple:
    """`gSaveBlock2.localTimeOffset` as `(days, hours, minutes, seconds)`."""
    base = d.emu.resolve("gSaveBlock2") + _LOCAL_TIME_OFFSET
    return _TIME.unpack(d.emu.read(base, 5))


def game_hour(when: datetime, offset: tuple) -> int:
    """The hour `gLocalTime` would hold if the RTC read `when`.

    A transcription of `RtcCalcTimeDifference` (pret/src/rtc.c:293-318) --
    a field-wise subtract with borrows, which is NOT the same as subtracting
    seconds, because the days field carries the borrow out.
    """
    _days, off_h, off_m, off_s = offset
    sec = when.second - off_s
    minute = when.minute - off_m
    hour = when.hour - off_h
    if sec < 0:
        minute -= 1
    if minute < 0:
        hour -= 1
    return hour + 24 if hour < 0 else hour


def tide_now(d) -> tuple:
    """`(is_high, game_hour)` for right now, from the ROM's own table."""
    table = tide_table(d.emu)
    hour = game_hour(datetime.now().astimezone(), local_time_offset(d))
    return bool(table[hour]), hour


def retune_for_low_tide(d) -> tuple:
    """Point TZ at a zone where the game's clock is mid-low-tide.

    Returns `(tz, game_hour, minutes_of_headroom)`, or `(None, hour, mins)`
    when the clock is already in a low-tide window.

    This changes NOTHING in the save: the tide is a real-world-clock mechanic
    and TZ is how the process tells libmgba's `localtime_r` what o'clock it
    is. `time.tzset()` forces glibc to re-read TZ, and mGBA re-samples the
    RTC on every poll, so it takes effect mid-run -- measured going 10:39 to
    15:39 in-game with one `tzset`.

    Whole-hour offsets only, and the one with the most headroom left wins:
    the window is six game-hours wide and a run that starts on the last
    minute of it loses the Ice Room half way through.
    """
    table = tide_table(d.emu)
    offset = local_time_offset(d)
    now = datetime.now(timezone.utc)

    def headroom(shift_h) -> int:
        """Minutes until the game clock leaves the low-tide window."""
        when = now.astimezone(timezone(timedelta(hours=shift_h)))
        if table[game_hour(when, offset)]:
            return -1
        for step in range(1, 24 * 60):
            later = when + timedelta(minutes=step)
            if table[game_hour(later, offset)]:
                return step
        return 24 * 60

    # The current zone first: if the clock is already inside a low-tide
    # window there is nothing to retune and the process keeps the host's TZ.
    current = headroom(round(
        datetime.now().astimezone().utcoffset().total_seconds() / 3600))
    if current > 0:
        return None, game_hour(datetime.now().astimezone(), offset), current
    best = max(range(-11, 15), key=headroom)
    mins = headroom(best)
    if mins < 0:
        raise RuntimeError("no whole-hour timezone puts this save at low tide")
    # POSIX TZ: the offset is what you ADD to local time to reach UTC, so the
    # sign is inverted against the UTC offset it names.
    tz = "TIDE%+d" % -best
    os.environ["TZ"] = tz
    time.tzset()
    hour = game_hour(datetime.now().astimezone(), offset)
    log.info("tide: TZ=%s puts the game clock at %02d:xx -- low tide, %d "
             "minutes of it left", tz, hour, mins)
    return tz, hour, mins


# ---- getting to a cell that is not in the arrival component ---------------

def warp_route(nav, start_map, start_cell, goal_map, goal_cells,
               allowed) -> list:
    """`[(map, (wx, wy), dest_map, (dx, dy))]` -- the warps to walk.

    A BFS over the warp graph whose nodes are (map, arrival cell) and whose
    edges are only the warps that are REACHABLE from that arrival and that
    can actually FIRE. Both filters were load-bearing:

    * **Components.** A Mt Pyre floor or a Shoal Cave room has up to five
      walkable components, and which door you came in by decides which of
      them you can use. The Sea Incense's pocket on 4F is not reachable from
      the stairs at all.
    * **HALF OF MT PYRE'S WARP EVENTS CANNOT BE ENTERED.** A warp only fires
      when the metatile under the player is itself a warp behaviour
      (`IsWarpMetatileBehavior`, pret/src/field_control_avatar.c:696 and
      731-743). The interior's holes come in pairs: `MtPyre_5F (12,12)` is
      MB_MT_PYRE_HOLE (0x0F) and drops into the Sea Incense's pocket, while
      `MtPyre_4F (12,12)` is the LANDING -- behaviour 0x08, an ordinary
      encounter tile that happens to carry a warp event. Standing on it does
      nothing, forever. Unfiltered, this searcher routed through
      `MtPyre_2F (6,12)` (also 0x08) and the run stood on the tile pressing
      a direction until it gave up, four times, reporting "no approach to
      warp (6,12)" from the tile itself.

    `goal_cells` are cells that must be REACHABLE on `goal_map`, not stood
    on -- the Sea Incense's own cell is blocked by the item ball object, so
    the goal is its neighbours. Pass None when simply arriving on the map is
    enough.
    """
    doors = nav.beh.door_behaviors
    seen = {(start_map, tuple(start_cell))}
    queue = deque([(start_map, tuple(start_cell), [])])
    while queue:
        here, cell, path = queue.popleft()
        cur = nav.cell(here, *cell)
        if cur is None:
            continue
        reach = nav.reachable(here, cell, cur.elevation)
        if here == goal_map and (goal_cells is None
                                 or any(tuple(g) in reach
                                        for g in goal_cells)):
            return path
        for exit_ in nav.exits(here):
            if exit_.get("kind") != "warp":
                continue
            dest, lands = exit_.get("dest"), exit_.get("lands_at")
            if dest not in allowed or not lands:
                continue
            if (exit_["x"], exit_["y"]) not in reach:
                continue
            tile = nav.cell(here, exit_["x"], exit_["y"])
            if tile is None or tile.behavior not in doors:
                continue
            key = (dest, tuple(lands))
            if key in seen:
                continue
            seen.add(key)
            queue.append((dest, tuple(lands),
                          path + [(here, (exit_["x"], exit_["y"]), dest,
                                   tuple(lands))]))
    return []


def own_input(d, tries=6) -> bool:
    """Wait until the walker, not a script, owns input.

    THE REASON THIS EXISTS. `take_warp` plans its approach with `goto`, and
    `goto` is refused for FREE while a scene holds `sLockFieldControls` or
    `preventStep` -- it returns instantly, so a retry loop spends no frames
    and reports "no approach to warp (11,1) on MtPyre_1F" three times in a
    row without the player moving a tile. That is exactly what killed the
    first Sea Incense attempt: a SHUPPET ambushed the last leg of the walk
    into Mt Pyre, and the post-battle fade still owned input when the warp
    chain started. The tile was reachable the whole time; nothing was wrong
    with the route.
    """
    for _ in range(tries):
        if not d.scene_active():
            return True
        if d.in_battle():
            d.fight()
        d.advance_scene(40_000)
        d.close_menus()
    if d.scene_active():
        return unwedge(d)
    return True


def arm(d, seconds=300.0) -> None:
    """Give the next walk its own clock, and never inherit a spent one.

    `_journey_deadline` is a Driver-wide field that `goto` refuses against
    for free (pokeagent/trek.py:823-829), and `Collector.pace_map` sets it
    per walk without ever clearing it (scripts/collect.py:534). Anything run
    after a pacing slice therefore inherits an EXPIRED deadline and is
    refused instantly. That is what the first full run of this script died
    of: standing on Route 122 it asked for Mt Pyre's door and got "no
    approach to warp (22,29)" three times, while `last_goto_reason` said --
    correctly -- "journey budget spent at (7,1) heading for (21,29)". The
    approach is thirty tiles of surf and walks fine when it is actually
    asked; measured, `goto(22,30)` from (7,1) succeeds. scripts/evolve_grind
    .py:151 documents the same landmine from the other end.
    """
    d._journey_deadline = time.time() + seconds


def walk_warps(d, route, tries=4, warp_budget=300.0) -> bool:
    """Take a `warp_route` chain, one warp at a time, verifying each hop.

    `sync_grid` before each: Shoal Cave's Inner Room rewrites its own
    metatiles for the salt and shell piles (`scripts.inc:22-57`), and at high
    tide the whole layout is swapped under the map name nav knows, so the
    shipped grid can disagree with what the walker has to cross. A stale grid
    makes `take_warp` answer "no approach to warp" on a door that is open.
    """
    for _map, (wx, wy), dest, _lands in route:
        for attempt in range(tries):
            if d.map_name() == dest:
                break
            own_input(d)
            arm(d, warp_budget)
            try:
                d.sync_grid()
            except Exception as exc:  # noqa: BLE001
                log.debug("   sync_grid: %s", str(exc)[:70])
            try:
                if d.take_warp(wx, wy) and d.map_name() == dest:
                    break
                log.info("   warp (%d,%d) -> %s refused: %s", wx, wy, dest,
                         d.last_warp_reason)
            except TravelInterrupted:
                log.info("   wild on the way to (%d,%d) -- fighting", wx, wy)
                d.fight()
                d.advance_scene(40_000)
            except Exception as exc:  # noqa: BLE001
                log.info("   warp (%d,%d): %s", wx, wy, str(exc)[:90])
            if attempt == tries - 1:
                return False
        if d.map_name() != dest:
            return False
        log.info("   -> %s %s", d.map_name(), d.pos())
    return True


def cross_dungeon(d, goal_map, maps, goal_cells=None) -> bool:
    """Walk a derived warp chain to `goal_map` without leaving the dungeon."""
    if d.map_name() == goal_map:
        if goal_cells is None:
            return True
        cur = d.nav.cell(d.map_name(), *d.pos())
        reach = d.nav.reachable(d.map_name(), d.pos(), cur.elevation)
        if any(tuple(g) in reach for g in goal_cells):
            return True
    own_input(d)
    route = warp_route(d.nav, d.map_name(), d.pos(), goal_map, goal_cells,
                       maps)
    if not route:
        log.info("   no firing warp chain from %s %s to %s", d.map_name(),
                 d.pos(), goal_map)
        return False
    log.info("   %d warps: %s", len(route),
             " -> ".join("%s(%d,%d)" % (m, x, y)
                         for m, (x, y), _, _ in route))
    return walk_warps(d, route)


def leave_dungeon(d, rec) -> bool:
    """Out through the front door, on a derived chain, never on `travel`."""
    if not cross_dungeon(d, rec.door_map, rec.maps, [rec.inner_door]):
        return False
    d.nav.surfing = True          # Route 122 and 125 are both sea routes
    for _ in range(3):
        if d.map_name() == rec.outside:
            return True
        own_input(d)
        arm(d)
        try:
            d.take_warp(*rec.inner_door)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except Exception as exc:  # noqa: BLE001
            log.info("   leaving %s: %s", rec.door_map, str(exc)[:90])
    if d.map_name() != rec.outside:
        log.info("   the door at %s %s refused: %s", rec.door_map,
                 rec.inner_door, d.last_warp_reason)
    return d.map_name() == rec.outside


def _fly_and_walk(d, col, name, budget) -> bool:
    _enable_surf(d)
    arm(d, budget)
    try:
        if col.goto_map(name, budget=budget):
            own_input(d)
            return True
    except Exception as exc:  # noqa: BLE001
        log.info("   goto_map %s: %s", name, str(exc)[:90])
    for _ in range(2):
        arm(d, budget)
        try:
            if d.travel(name, on_battle="fight", budget_s=budget / 2):
                own_input(d)
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("   travel %s: %s", name, str(exc)[:110])
            break
    if d.map_name() == name:
        own_input(d)
        return True
    return False


def enter_dungeon(d, col, rec, budget=600.0) -> bool:
    """Get inside: reach the OUTSIDE route, then force the door by hand.

    In that order, deliberately. Asking `travel` for the door map means one
    plan that has to cross a grass belt, mount Surf and thread a one-tile
    beach, all inside `travel`'s small per-leg budget; measured, it pinned on
    Route 121's grass at (40,12) for 150 seconds and then spent the whole
    pyre budget retrying. Reaching Route 122 ANYWHERE is easy, and the door
    approach is then a single `goto` with a clock of its own -- measured,
    `goto(22,30)` from the far corner (7,1) walks it.

    Both of these doors are only approachable off the water: Route 122's
    (22,29) has exactly one open neighbour, (22,30), on a beach reached by
    dismounting north off (22,32).
    """
    if d.map_name() in rec.maps:
        return True
    if _fly_and_walk(d, col, rec.outside, budget):
        _enable_surf(d)
        for _ in range(3):
            if d.map_name() in rec.maps:
                return True
            own_input(d)
            arm(d, budget)
            try:
                d.take_warp(*rec.outer_door)
            except TravelInterrupted:
                d.fight()
                d.advance_scene(40_000)
            except Exception as exc:  # noqa: BLE001
                log.info("   entering %s: %s", rec.door_map, str(exc)[:90])
        log.info("   the door at %s %s refused: %s", rec.outside,
                 rec.outer_door, d.last_warp_reason)
    else:
        log.info("   could not reach %s (%s)", rec.outside,
                 d.last_goto_reason)
    if d.map_name() in rec.maps:
        return True
    # Last resort: let the router take the door as part of its own plan.
    return _fly_and_walk(d, col, rec.door_map, budget)


def reach_map(d, col, name, budget=420.0) -> bool:
    """Get onto `name`, whichever side of a dungeon wall each end is on.

    The three cases are genuinely different and conflating them is what cost
    a run its budget: inside a dungeon only `warp_route` may plan, crossing
    the wall is a specific door, and outside it is fly-then-walk.
    """
    arm(d, budget)
    if d.map_name() == name:
        own_input(d)
        return True
    home = dungeon_of(d.map_name())
    want = dungeon_of(name)
    if home is not None and home is want:
        return cross_dungeon(d, name, home.maps)
    if home is not None and not leave_dungeon(d, home):
        log.info("   stuck inside %s at %s %s", home.door_map, d.map_name(),
                 d.pos())
        return False
    if want is not None:
        if not enter_dungeon(d, col, want, budget):
            return False
        return cross_dungeon(d, name, want.maps)
    return _fly_and_walk(d, col, name, budget)


# ---- the dex ledger -------------------------------------------------------

def natdex_map(col) -> dict:
    return {e.name.strip().upper(): e.natdex
            for e in col.target.entries if e.natdex}


def missing_of(col, names, index=None) -> list:
    """Which of `names` still have no CAUGHT flag. Seen does not count."""
    index = index or natdex_map(col)
    caught, _seen = col.target.dex_flags(col.d.state)
    return [n for n in names if index.get(n.upper()) not in caught]


def collection_policy(col):
    """Throw at what the dex still owes us. Run from everything else.

    PP IS THE BINDING CONSTRAINT ON A HUNT, NOT HP. This party's L100 lead
    is a PELIPPER with SURF 0/x, FLY 1 and HYDRO PUMP 5 -- five damaging
    turns in the whole party's front slot. Knocking out the ZUBAT and
    GOLBAT that make up half of Shoal Cave's table therefore ends the hunt
    after five encounters, and it ends it BADLY: with no damaging move the
    battle layer parks on the move menu, `scene_active()` stays True, and
    `pace_map`'s scene branch spends `advance_scene(40_000)` six times over
    -- measured at 192 seconds for a 30-second slice, with the stall
    watchdog reporting the player pinned and `save` refusing because "a
    script still owns input".

    Fleeing costs no PP at all, and a wild is never worth a turn unless the
    dex owes us it. Field moves are unaffected: Surf and Fly out of battle
    do not spend PP, which is why Fly kept working on 0.

    The other half of this is the reason it is safe to hand the CATCHER this
    as its `inner`: `Catcher.policy` only calls inner on a turn it decided
    not to throw, and its throw trigger is "my best move would KO it"
    (pokeagent/catching.py:369) -- which is False once PP is gone. Without
    this, a dry lead would have FLED FROM A CHIMECHO, and Chimecho is a 2%
    slot on one map. Here inner throws instead, so a wanted species gets a
    ball every turn regardless of what the party can still hit.
    """
    def decide(frame):
        # A TRAINER CANNOT BE FLED (battle.py:2102-2106). Hand the turn back
        # so the harness's own tactics fight it out; Mt Pyre's exterior has
        # trainers on it.
        if not frame.get("wild"):
            return None
        enemy = frame.get("enemy") or {}
        species = enemy.get("species") or enemy.get("name")
        try:
            owed = bool(species) and not col.catcher.dex_caught(species)
        except Exception:  # noqa: BLE001 - never lose a battle to this
            owed = False
        if not owed:
            return "flee"
        # The catcher's own "cheapest ball in the bag" rule, rather than a
        # second copy of the price lookup here.
        ball = col.catcher._pick_ball()
        return ("ball", ball) if ball else None

    return decide


def install_battle_bridge(col) -> None:
    """Route every encounter through the CATCHER, even the ones `goto` eats.

    `goto` walks a six-step chunk with `walk`, and when a wild interrupts it
    mid-chunk `step_dir` fails with "scene-owns-input" -- so goto takes its
    SCENE branch and calls `advance_scene(40000)`
    (pokeagent/trek.py:933-936) instead of raising, and the
    `on_battle="raise"` that `pace_map` asked for never fires.
    `advance_scene` presses A on a stalled signature, which inside a battle
    means FIGHT and then MOVE SLOT 0 -- and with this party's lead holding
    SURF at 0 PP that is "There's no PP left for this move!", answered with
    another A, until the 40,000-frame budget is gone.

    Profiled over one 30-second pace slice: `advance_scene` took 85.4 of the
    97 seconds across four calls, ~21 seconds each. Every encounter in the
    slice went to blind A rather than to a ball, which is why a cave with a
    50% SPHEAL slot produced one wild in two minutes and no catches at all.

    Wrapping the instance's `advance_scene` closes the hole where it opens:
    play a live battle with the collector's policy first -- one flee or one
    ball, no PP -- then let the original finish the fade.
    """
    d = col.d
    original = d.advance_scene
    busy = []

    def advance(*args, **kwargs):
        # Reentrancy guard: `col.fight` can itself reach a scene advance, and
        # a wrapper that recursed here would never come back.
        if not busy and d.in_battle():
            busy.append(True)
            try:
                col.fight()
            except Exception as exc:  # noqa: BLE001 - never lose the walk
                log.info("   bridge: fight raised %s", str(exc)[:80])
            finally:
                busy.pop()
        return original(*args, **kwargs)

    d.advance_scene = advance


def needs_nurse(col) -> str:
    """Why this party wants a Centre, or "" when it does not.

    `Collector.hurt` asks whether ANY party member can still damage
    something, which is the right question for a fight and the wrong one
    here: a blind-A scene advance always picks the LEAD'S SLOT 0, so a lead
    whose first move is spent jams the battle even with three other moves
    full. That is exactly this party -- PELIPPER with SURF 0, SPIT UP 10,
    FLY 1, HYDRO PUMP 5 -- and `pp_dry()` answers False for it because
    HYDRO PUMP still has charges.
    """
    try:
        party = col.d.state.party()
        pp = list(party[0].pp or []) if party else []
        if pp and not pp[0]:
            return "the lead's slot-0 move is out of PP"
    except Exception:  # noqa: BLE001
        pass
    if col.pp_dry():
        return "no damaging PP in the party"
    if col.hurt():
        return "hurt"
    return ""


def hunt(col, wanted, budget, slice_s=30.0) -> list:
    """Pace this map until `wanted` is closed or the budget runs out.

    Sliced rather than one long `pace_map` call, for two reasons.

    * `pace_map` walks until ITS deadline and has no idea which species this
      leg came for -- handed the whole budget it keeps pacing a map whose
      species are already in the book. The slice is also where healing and
      restocking get a turn.
    * **THE SLICE BOUNDS ONE BAD WALK.** `pace_map` jumps to
      `cells[(i * 7) % len(cells)]`, so in a maze it will sooner or later
      pick a target across the room, and it caps a single `goto` at
      `min(its deadline, now + 60)`. Measured in Shoal Cave's entrance room:
      most hops cost 0-5 seconds, but `goto(21,24)` from (15,29) ground for
      78 seconds, moved three tiles and returned False with "journey budget
      spent". With a 90-second slice that is the whole slice for nothing --
      the run showed two consecutive slices of 129 seconds with ZERO wild
      encounters, which in a cave whose encounter rate is 10 is the giveaway
      that no steps were being taken. A short slice makes the slice deadline
      the binding one, so a doomed target costs `slice_s` and the next slice
      picks a different one.
    """
    d = col.d
    index = natdex_map(col)
    deadline = time.time() + budget
    # Pacing is on foot. Leaving `nav.surfing` on from the trip out here made
    # the walker plan across the Summit's 168 water cells and mount Surf
    # mid-patch, which is time spent not meeting anything.
    d.nav.surfing = False
    while time.time() < deadline:
        left = missing_of(col, wanted, index)
        if not left:
            log.info("   %s: all of %s registered", d.map_name(),
                     ", ".join(wanted))
            return []
        # STEP OUTSIDE BEFORE THE ERRAND. `heal_at_nearest_center` and
        # `restock_balls` both plan with `travel`, and `travel` cannot be
        # trusted to leave one of these dungeons -- see `DUNGEONS`. Walking
        # out on a derived chain first turns both into ordinary overworld
        # errands.
        nurse = needs_nurse(col)
        shop = col.balls() <= col.BALL_FLOOR and col.can_afford_a_ball()
        if nurse or shop:
            here = d.map_name()
            rec = dungeon_of(here)
            if rec is not None and not leave_dungeon(d, rec):
                log.info("   could not step out of %s for the errand", here)
                return left
            if nurse:
                log.info("   healing: %s", nurse)
                arm(d, 480.0)
                col.heal()
            if shop:
                arm(d, 480.0)
                col.restock_balls()
            if not reach_map(d, col, here):
                log.info("   could not get back to %s after the errand", here)
                return left
            d.nav.surfing = False
        # CLEAR THE FIELD BEFORE PACING. `pace_map`'s answer to a live scene
        # is `advance_scene(40_000)` six times and then a silent exit -- it
        # never presses B, never closes a menu and never finishes a battle,
        # so one parked box costs the whole slice and logs nothing. Handing
        # it a field that owns its own input is the difference between a
        # slice that walks and a slice that burns.
        own_input(d)
        log.info("   pacing %s for %s (%d balls, %.0fs left)", d.map_name(),
                 ", ".join(left), col.balls(), deadline - time.time())
        got = col.pace_map(min(deadline, time.time() + slice_s), "grass")
        if got:
            log.info("   +%d new to the dex (now %d)", got,
                     col._caught_count())
    return missing_of(col, wanted, index)


# ---- the legs -------------------------------------------------------------

def leg_incense(d, col) -> bool:
    """The SEA INCENSE. Done FIRST: it is somebody else's blocker."""
    if _has(d, SEA_INCENSE):
        log.info("sea incense: already in the bag")
        return True
    rec = dungeon_of(INCENSE_BALL[0])
    if not enter_dungeon(d, col, rec):
        log.info("sea incense: could not get inside Mt Pyre")
        return False
    d.nav.surfing = False
    goal_map, bx, by = INCENSE_BALL
    # The ball's own cell is an `object_event` and blocks itself, so the goal
    # is a cell BESIDE it -- `_pick_up` walks to a neighbour and presses A.
    neighbours = [(bx, by - 1), (bx, by + 1), (bx - 1, by), (bx + 1, by)]
    log.info("sea incense: routing to %s (%d,%d)", goal_map, bx, by)
    if not cross_dungeon(d, goal_map, rec.maps, neighbours):
        log.info("sea incense: the warp chain broke on %s %s", d.map_name(),
                 d.pos())
        return False
    own_input(d)
    arm(d, 180.0)
    got = _pick_up(d, bx, by, SEA_INCENSE, tries=6)
    log.info("sea incense: %s", "IN THE BAG" if got else "NOT obtained")
    if got:
        col.save()
    return got


def leg_pyre(d, col, budget) -> dict:
    """VULPIX on the Exterior, then DUSKULL and CHIMECHO on the Summit.

    Rates from `docs/gen3/guide/encounters.json`: Exterior gives VULPIX 20%
    across L25/27/29; the Summit gives DUSKULL 13% (L26/28/30) and CHIMECHO
    2% (two L28 slots). The interior floors 4F/5F/6F list DUSKULL at only
    10% and no CHIMECHO at all, so they get no pacing time.
    """
    out = {}
    slices = ("MtPyre_Exterior", ["VULPIX"]), ("MtPyre_Summit",
                                               ["DUSKULL", "CHIMECHO"])
    share = budget / len(slices)
    for name, wanted in slices:
        left = missing_of(col, wanted)
        if not left:
            log.info("%s: nothing wanted here any more", name)
            out[name] = []
            continue
        if not reach_map(d, col, name):
            log.info("%s: could not reach it (%s)", name, d.last_goto_reason)
            out[name] = left
            continue
        log.info("== %s for %s", name, ", ".join(left))
        out[name] = hunt(col, wanted, share)
        col.save()
    return out


def leg_shoal(d, col, budget, want_low_tide=True) -> dict:
    """SPHEAL anywhere in the cave, SNORUNT only in the Ice Room.

    `ShoalCave_LowTideEntranceRoom` is SPHEAL at 50% (L26-32 across six
    slots) and `ShoalCave_LowTideIceRoom` is SNORUNT at 10% (L26 5%, L28 4%,
    L30 1%) -- the ice room is the only table in the game with SNORUNT in it.
    """
    out = {}
    high, hour = tide_now(d)
    log.info("tide: the game clock reads %02d:xx, table says %s", hour,
             "HIGH" if high else "LOW")
    if high and want_low_tide:
        retune_for_low_tide(d)
        high, hour = tide_now(d)
    rec = dungeon_of(SHOAL_ROOMS[0])
    if not enter_dungeon(d, col, rec):
        log.info("shoal: could not get into the cave")
        return {"unreached": ["SPHEAL", "SNORUNT"]}
    entrance = SHOAL_ROOMS[0]
    if not cross_dungeon(d, entrance, rec.maps):
        return {"unreached": ["SPHEAL", "SNORUNT"]}
    d.nav.surfing = False
    # THE FLAG IS ONLY TRUE AFTER THE ROOM'S ON_TRANSITION SCRIPT HAS RUN.
    # Read before entering it is whatever the last visit left behind; read
    # here it is the tide the engine just computed, which is the answer that
    # decides whether SNORUNT is on the table.
    drift = 0
    try:
        drift = d.sync_grid()
    except Exception as exc:  # noqa: BLE001
        log.debug("sync_grid: %s", str(exc)[:70])
    engine_high = bool(d.state.flag("FLAG_SYS_SHOAL_TIDE"))
    log.info("shoal: in %s %s -- FLAG_SYS_SHOAL_TIDE=%s, %d cells of live "
             "grid drift", d.map_name(), d.pos(), engine_high, drift)

    share = budget / 2.0
    log.info("== %s for SPHEAL", entrance)
    out[entrance] = hunt(col, ["SPHEAL"], share)
    col.save()

    if engine_high:
        log.info("shoal: HIGH tide -- the Inner Room's descents are water "
                 "metatiles, so the Lower and Ice rooms cannot be entered "
                 "and SNORUNT is off the table this run "
                 "(field_control_avatar.c:696)")
        out["SNORUNT"] = ["SNORUNT (blocked by high tide)"]
        return out
    if not missing_of(col, ["SNORUNT"]):
        return out
    ice = SHOAL_ROOMS[-1]
    log.info("shoal: routing to %s", ice)
    if not cross_dungeon(d, ice, rec.maps):
        log.info("shoal: the ice-room chain broke on %s %s", d.map_name(),
                 d.pos())
        out[ice] = ["SNORUNT"]
        return out
    log.info("== %s for SNORUNT", ice)
    out[ice] = hunt(col, ["SNORUNT"], share)
    col.save()
    return out


def report(col, before, targets) -> None:
    d = col.d
    index = natdex_map(col)
    caught, _seen = col.target.dex_flags(d.state)
    log.info("---- pyre_shoal result ----")
    log.info("dex caught %d -> %d", before, len(caught))
    for name in targets:
        log.info("  %-9s %s", name,
                 "CAUGHT" if index.get(name) in caught else "still missing")
    # POCKET MEMBERSHIP BEFORE ANY MENU. `gBagPockets` is re-pointed while
    # the bag UI is open, so this has to be read with every menu shut.
    # `pocket_items` answers `(slot, item_id, qty)` -- the ID, not a name --
    # so the name comes from the ROM's own item table.
    try:
        from pokeagent.teaching import Teacher

        held = {d.names.item(item_id).strip().upper(): qty
                for _slot, item_id, qty in Teacher(d).pocket_items(0)}
    except Exception as exc:  # noqa: BLE001
        log.info("  pocket read failed (%s); falling back to state.bag()",
                 str(exc)[:70])
        held = {str(k).strip().upper(): v
                for k, v in (d.state.bag().get("items") or {}).items()}
    log.info("  %-9s %s", SEA_INCENSE,
             "IN BAG" if SEA_INCENSE in held else "NOT IN BAG")
    log.info("  ITEMS pocket: %s",
             ", ".join("%s x%d" % (k, v) for k, v in sorted(held.items()))
             or "(empty)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True,
                    help="YOUR fork. Never saves/line3.state.")
    ap.add_argument("--legs", default="pyre,shoal",
                    help="comma list of pyre,shoal (incense rides with pyre)")
    ap.add_argument("--pyre-budget", type=float, default=1500.0)
    ap.add_argument("--shoal-budget", type=float, default=1500.0)
    ap.add_argument("--balls", type=int, default=80,
                    help="restock target; CHIMECHO is a 45-rate 2%% slot and "
                         "eats a dozen balls at full HP on its own")
    ap.add_argument("--no-restock", action="store_true")
    ap.add_argument("--tide", choices=("low", "now"), default="low",
                    help="'low' retunes TZ so the Ice Room is enterable")
    ap.add_argument("--feed", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    state = Path(args.state)
    if state.name.startswith("line3"):
        ap.error("refusing to drive saves/line3.state -- fork it first")
    d = Driver(str(state))
    feed = args.feed or state.stem
    col = Collector(d, feed_name=feed)
    col.BALL_TARGET = args.balls
    # CATCH, OR RUN. NEVER TRAIN, AND NEVER TRADE PP FOR A KO.
    #
    # `Collector.base_policy` is `Team.training_policy`, and inside
    # `Collector.fight` the catcher's ball policy is only the OUTER layer --
    # every declined encounter is handed to a policy whose job is to feed exp
    # to the laggard. Measured in Shoal Cave: one L30 ZUBAT (already in the
    # dex, so correctly declined) benched the L100 lead for the L29 party
    # member, spent three turns on BUBBLEBEAM without landing a KO and
    # switched back at 49% HP -- the whole first pace slice, with the stall
    # watchdog reporting the player pinned, because a battle is not a step.
    # `damage_first` fixes the training but not the PP bill; see
    # `collection_policy` for why that bill is the one that actually ends a
    # hunt.
    col.base_policy = lambda: collection_policy(col)
    install_battle_bridge(col)
    unwedge(d)
    log.info("start: %s %s, %d balls, %d money, dex %d",
             d.map_name(), d.pos(), col.balls(), d.state.money(),
             col._caught_count())
    before = col._caught_count()

    legs = [x.strip() for x in args.legs.split(",") if x.strip()]
    # NURSE AND SHOP ONCE, UP FRONT. Both legs are dungeons a fly plus a long
    # surf from any Centre or Mart, and going back mid-hunt costs the flight
    # out, the door and the whole warp chain in again.
    nurse = needs_nurse(col)
    if nurse:
        log.info("== nurse first: %s", nurse)
        arm(d, 480.0)
        col.heal()
    if not args.no_restock and col.balls() < args.balls \
            and col.can_afford_a_ball():
        log.info("== restocking to %d balls (have %d, %d money)",
                 args.balls, col.balls(), d.state.money())
        arm(d, 480.0)
        col.restock_balls()

    # IN THE ORDER GIVEN. Both dungeons are a fly plus a long surf from
    # anywhere, so which one is nearer to where the save is parked is worth
    # several minutes: `--legs shoal,pyre` from Mossdeep skips a flight to
    # Lilycove and back.
    for leg in legs:
        if leg == "pyre":
            leg_incense(d, col)
            log.info("pyre leg left: %s",
                     leg_pyre(d, col, args.pyre_budget))
        elif leg == "shoal":
            log.info("shoal leg left: %s",
                     leg_shoal(d, col, args.shoal_budget,
                               want_low_tide=args.tide == "low"))
        else:
            ap.error("unknown leg %r; expected pyre or shoal" % leg)

    col.save()
    report(col, before,
           ["VULPIX", "DUSKULL", "CHIMECHO", "SPHEAL", "SNORUNT"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
