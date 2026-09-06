#!/usr/bin/env python
"""SPHEAL and SNORUNT out of Shoal Cave, at a timezone-forced LOW tide.

Three dex entries ride on the first of those: SPHEAL is the base of SEALEO
(L32) and WALREIN (L44), both of which the by-level grind engine closes on
its own once the base species is in the book. SNORUNT is one entry and it is
the expensive one -- it lives in exactly one encounter table in the game.

WHY THIS IS NOT JUST `pace_map` ON A MAP NAME
---------------------------------------------
The tide is the HOST WALL CLOCK. `UpdateShoalTideFlag`
(pret/src/time_events.c:54-92) indexes a 24-entry table by `gLocalTime.hours`
and sets FLAG_SYS_SHOAL_TIDE from it; read out of this ROM the table is
`[1,1,1,0,0,0,0,0,0,1,1,1,1,1,1,0,0,0,0,0,0,1,1,1]`, so low tide is game
hours 3-8 and 15-20. libmgba resolves the GBA RTC through libc
`localtime_r`, so TZ selects the in-game hour and `time.tzset()` makes it
bite mid-run.

The tide does NOT change the map id -- the entrance room's ON_TRANSITION
calls `setmaplayoutindex` 169/165 on the SAME map, and the inner room
170/166. So the encounter table never moves and SPHEAL is huntable at either
tide. SNORUNT is not: a warp only fires when the metatile BEHAVIOUR under
the player is a warp behaviour (`IsWarpMetatileBehavior`,
pret/src/field_control_avatar.c:696 and 731-743), and at high tide the inner
room's descents to the Lower Room are swapped to water metatiles. The Ice
Room -- SNORUNT's only home -- is then unreachable no matter how you surf.

So: pick the whole-hour TZ offset that puts the game clock as EARLY in a
low-tide window as possible, apply it, and only then walk in. Picking by
"is it low tide right now" is not enough; the window is six hours wide and a
run that starts on its last minute loses the Ice Room half way through, so
this maximises HEADROOM rather than accepting the first zone that qualifies.

The navigation, the warp-chain derivation and the battle bridge are all
`pyre_shoal`'s, imported rather than re-implemented -- a second copy of
`warp_route` beside the existing one would be a second convention. What is
new here is the headroom-maximising TZ choice and `pin_hunt`, which the Ice
Room needs and no other map does: its floor is 150 cells of MB_ICE-family
forced movement, and a walk that ends up on the cracked ice DROPS THE PLAYER
INTO THE LOWER ROOM. Pacing there therefore has to notice it is no longer on
the map it was pacing and climb back up, instead of quietly spending the
whole budget hunting SNORUNT in a room SNORUNT does not live in.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from collect import Collector  # noqa: E402
from share_grind import unwedge  # noqa: E402
from pyre_shoal import (  # noqa: E402
    SHOAL_ROOMS, arm, cross_dungeon, dungeon_of,
    enter_dungeon, game_hour, install_battle_bridge, local_time_offset,
    missing_of, natdex_map, needs_nurse, own_input, tide_table,
)
from unlocks import _enable_surf  # noqa: E402

log = logging.getLogger("shoal_hunt")

ENTRANCE = SHOAL_ROOMS[0]
ICE = SHOAL_ROOMS[-1]
#: Where the balls are. Mossdeep is the Fly landing this cave hangs off, and
#: its shelf is ULTRA (1200) / NET / DIVE -- worth the 1200 here, because
#: SNORUNT is a 10% slot and every failed throw is another twenty paces.
BALL_TOWN = "MossdeepCity"
#: And its shop map, PINNED. `Collector.nearest_mart` ranks eleven marts by
#: walking `nav.route_legs` from here to each of them, which is pure Python
#: over a 394-map graph: measured from MossdeepCity (28,17) it had not
#: returned after 174 seconds and the stall watchdog dumped the stack inside
#: `nav.py:1178 route_legs` with the core ticking zero frames. The answer is
#: also known in advance -- we have just flown to Mossdeep specifically for
#: its shelf -- so ranking it against Oldale's is time spent buying nothing.
BALL_MART = "MossdeepCity_Mart"

#: Which ball to throw at each target, and why. Both of these are thrown at
#: FULL HP -- an L100 PELIPPER's SURF does 267-315 to an 88-HP SPHEAL, so
#: there is no "weaken it first" available at all -- which makes the ball
#: multiplier the only lever there is.
#:
#: * SPHEAL is Water/Ice, and a NET BALL is 3x on Water. Catch rate 130 at
#:   full HP gives a = 130 with a 3x ball, about a 51% catch per throw.
#: * SNORUNT is pure Ice, so a NET BALL is 1x on it and an ULTRA BALL's 2x
#:   wins: rate 190 x 2 = 380, which clamps to a = 255 -- the guaranteed-
#:   catch ceiling. Spending a 1200 ball on the 10% slot rather than a 1000
#:   one is the cheapest decision in this script.
#:
#: `Catcher._pick_ball` cannot make this call: it picks the CHEAPEST ball in
#: the bag by the ROM's price (catching.py:376-395), which after a Mossdeep
#: restock is always the NET BALL -- so SNORUNT would be hunted with the one
#: ball that does nothing for it.
BALL_FOR = {"SPHEAL": "NET BALL", "SNORUNT": "ULTRA BALL"}


def pin_mart(col) -> None:
    """Shop where we flew to, at Mossdeep prices, and never recurse.

    Two overrides, both load-bearing, both measured on this run.

    * `nearest_mart` skips the 394-map scan described above.
    * `CHEAP_BALL_CEILING` is raised past Mossdeep's shelf. Left at 400,
      `restock_balls` logged "MossdeepCity_Mart only sells NET BALL at 1000
      -- shopping basic instead" and walked `BASIC_MARTS` calling
      `self.restock_balls()` recursively (collect.py:824-833) -- which asks
      `nearest_mart()` again, gets the PINNED Mossdeep back, finds the same
      expensive shelf and recurses forever. The ceiling exists to stop a dex
      sweep spending 18,000 on eighteen balls, and that reasoning does not
      apply here: this save holds 999,999, the hunt needs seventy balls
      once, and Mossdeep's cheapest is a NET BALL -- which is 3x on SPHEAL,
      a Water type, so it is the BETTER ball as well as the near one.
    """
    col.nearest_mart = lambda: BALL_MART
    col.CHEAP_BALL_CEILING = 1500


# ---- who decides a wild battle -------------------------------------------

def shoal_policy(col):
    """Throw the RIGHT ball at what the dex owes us, flee everything else.

    `pyre_shoal.collection_policy` with one change -- `BALL_FOR` instead of
    the cheapest ball in the bag -- and it is layered the same way: a trainer
    returns None so the harness's tactics fight it, an already-caught wild is
    fled because fleeing costs no PP, and an owed wild gets a ball every turn
    regardless of what the party can still hit.
    """
    def decide(frame):
        if not frame.get("wild"):
            return None
        enemy = frame.get("enemy") or {}
        species = str(enemy.get("species") or enemy.get("name") or "")
        try:
            owed = bool(species) and not col.catcher.dex_caught(species)
        except Exception:  # noqa: BLE001 - never lose a battle to this
            owed = False
        if not owed:
            return "flee"
        held = col.d.state.bag().get("poke_balls") or {}
        want = BALL_FOR.get(species.strip().upper())
        if want and held.get(want):
            return ("ball", want)
        ball = col.catcher._pick_ball()
        return ("ball", ball) if ball else None

    return decide


def install_policy(col) -> None:
    """Put the catch decision where the encounters ACTUALLY arrive.

    THIS IS THE BUG THAT COST THIS RUN TWO SPHEAL. `pace_map` walks with
    `goto(..., on_battle="raise")` and expects to catch `TravelInterrupted`,
    but a wild that starts mid-chunk makes `walk` fail with
    "scene-owns-input" and `goto` now handles that itself: trek.py:958-963
    calls **`self.fight()`** -- the DRIVER's -- and then continues the walk.
    `on_battle="raise"` never fires, `Collector.fight` is never entered, and
    `pyre_shoal.install_battle_bridge`, which wraps `advance_scene`, is not
    on the path either. Observed: two consecutive
    "[battle] T1 attack:0 SURF#0 | SPHEAL 88->0 (chosen by tactics)" with no
    `[catch]` line anywhere in the log -- the catcher was never asked, and
    the L100 lead one-shot the species the run came for.

    `Driver.fight()` falls back to `self.battle_policy` when called with no
    policy (trek.py:3159-3160), and that attribute is the only hook every
    path shares. Setting it is what makes a wild encounter a catch attempt
    no matter which layer picked the battle up.

    (`encounter_policy`, named in trek.py:956's comment, has no consumer
    anywhere in the package -- setting it does nothing.)
    """
    d = col.d
    d.battle_policy = shoal_policy(col)
    col.base_policy = lambda: shoal_policy(col)
    install_battle_bridge(col)


# ---- the tide -------------------------------------------------------------

def tide_at(table, offset, shift_h, when=None) -> tuple:
    """`(is_high, game_hour, minutes_until_it_flips)` for a whole-hour TZ."""
    when = (when or datetime.now(timezone.utc)).astimezone(
        timezone(timedelta(hours=shift_h)))
    hour = game_hour(when, offset)
    high = bool(table[hour])
    for step in range(1, 24 * 60 + 1):
        later = game_hour(when + timedelta(minutes=step), offset)
        if bool(table[later]) is not high:
            return high, hour, step
    return high, hour, 24 * 60


def tune_tide(d, want_high=False, minimum=45) -> tuple:
    """Point TZ at the whole-hour zone with the MOST low tide left in it.

    Returns `(tz, game_hour, minutes_of_headroom)`. Changes nothing in the
    save: the tide is a real-clock mechanic and TZ is how this process tells
    libmgba's `localtime_r` what o'clock it is.

    Deliberately different from `pyre_shoal.retune_for_low_tide`, which keeps
    the host's zone whenever it already qualifies. Measured on this save at
    01:48 local: the host zone (UTC-4) reads game hour 11 -- high tide -- and
    the best zone is UTC+0 at game 15:48 with 342 minutes left, against 42
    minutes for UTC+5, which also "qualifies". A 42-minute window is not a
    SNORUNT hunt.
    """
    table = tide_table(d.emu)
    offset = local_time_offset(d)
    best, best_room, best_hour = None, -1, None
    for shift in range(-11, 15):
        high, hour, room = tide_at(table, offset, shift)
        if high is not want_high:
            continue
        if room > best_room:
            best, best_room, best_hour = shift, room, hour
    if best is None:
        raise RuntimeError("no whole-hour zone puts this save at %s tide"
                           % ("high" if want_high else "low"))
    if best_room < minimum:
        raise RuntimeError("best zone leaves only %d minutes of %s tide"
                           % (best_room, "high" if want_high else "low"))
    # POSIX TZ names the offset you ADD to local time to reach UTC, so its
    # sign is inverted against the UTC offset it describes.
    tz = "UTC0" if best == 0 else "TIDE%+d" % -best
    os.environ["TZ"] = tz
    time.tzset()
    live = game_hour(datetime.now().astimezone(), offset)
    log.info("tide: TZ=%s -> game clock %02d:xx, %s tide, %d minutes of it "
             "left (host clock %s)", tz, live,
             "HIGH" if table[live] else "LOW", best_room,
             datetime.now(timezone.utc).strftime("%H:%MZ"))
    if live != best_hour:
        log.info("tide: WARNING -- tzset landed on hour %02d, wanted %02d",
                 live, best_hour)
    return tz, live, best_room


def tide_report(d) -> tuple:
    """`(engine_flag, table_says_high, game_hour)` -- both answers, compared.

    The FLAG is only true after the entrance room's ON_TRANSITION has run, so
    before the first entry it is whatever the last visit left behind. The
    TABLE is what the engine will compute next time. Printing both is how a
    failed SNORUNT hunt gets to prove which tide it was actually in.
    """
    table = tide_table(d.emu)
    hour = game_hour(datetime.now().astimezone(), local_time_offset(d))
    return bool(d.state.flag("FLAG_SYS_SHOAL_TIDE")), bool(table[hour]), hour


# ---- hunting a map that can drop you off it -------------------------------

def pin_hunt(d, col, rec, map_name, wanted, budget, slice_s=30.0) -> list:
    """Pace `map_name` for `wanted`, climbing back whenever we leave it.

    `pyre_shoal.hunt` is the general version and is right for a room with a
    floor. The Ice Room has 150 cells of forced-movement ice in a 20x30 room
    and its only warp is the one back DOWN to the Lower Room, so a pacing
    walk that touches cracked ice arrives in the Lower Room with the budget
    still running -- where SNORUNT does not exist. Detecting that and
    re-taking the five-warp chain is the difference between a hunt and a
    hunt-shaped way of spending an hour.

    The errand branch is on purpose narrower than `hunt`'s: leaving a
    five-warp-deep room to shop costs both traversals, so this only goes when
    the bag is genuinely spent, and it never goes for a nurse -- a fled
    encounter costs no PP and no HP, and everything here is fled or thrown
    at.
    """
    index = natdex_map(col)
    deadline = time.time() + budget
    lost = 0
    while time.time() < deadline:
        left = missing_of(col, wanted, index)
        if not left:
            log.info("   %s: %s all registered", map_name, ", ".join(wanted))
            return []
        if d.map_name() != map_name:
            if lost:
                log.info("   fell out of %s into %s %s -- climbing back",
                         map_name, d.map_name(), d.pos())
            own_input(d)
            d.nav.surfing = False
            arm(d, 300.0)
            if not cross_dungeon(d, map_name, rec.maps):
                log.info("   cannot get back onto %s from %s %s", map_name,
                         d.map_name(), d.pos())
                return left
            lost += 1
        if col.balls() <= col.BALL_FLOOR:
            log.info("   %d balls left -- out to restock", col.balls())
            if not restock(d, col):
                log.info("   restock failed; %d balls", col.balls())
                return left
            continue
        own_input(d)
        d.nav.surfing = False
        log.info("   pacing %s %s for %s (%d balls, %.0fs left)", map_name,
                 d.pos(), ", ".join(left), col.balls(),
                 deadline - time.time())
        got = col.pace_map(min(deadline, time.time() + slice_s), "grass")
        if got:
            log.info("   +%d new to the dex (now %d)", got,
                     col._caught_count())
            col.save()
    if lost:
        log.info("   %s: %d re-entries over the budget", map_name, lost)
    return missing_of(col, wanted, index)


def restock(d, col) -> bool:
    """Buy balls, from outside the cave, and come back in.

    Out FIRST, always. `Collector.restock_balls` plans with `travel`, and
    `travel` must never be asked to leave one of these dungeons -- see
    `pyre_shoal.DUNGEONS` for the 150-second pin that rule was written from.
    """
    rec = dungeon_of(d.map_name())
    here = d.map_name()
    if rec is not None:
        from pyre_shoal import leave_dungeon

        if not leave_dungeon(d, rec):
            return False
    arm(d, 480.0)
    if col.balls() < col.BALL_TARGET:
        col.goto_map(BALL_TOWN, budget=300.0)
        arm(d, 480.0)
        col.restock_balls()
    if rec is None:
        return True
    _enable_surf(d)
    arm(d, 600.0)
    if not enter_dungeon(d, col, rec):
        return False
    return cross_dungeon(d, here, rec.maps)


def cold_verify(state_path, names) -> dict:
    """Re-open the banked save from scratch and read the dex flags.

    A mid-run log line is not evidence: `pace_map` counts a catch off
    `_caught_count()` on a live emulator that has a battle fade in flight.
    This is the number that gets reported.
    """
    from pokeagent import dex as dexmod

    d = Driver(str(state_path))
    target = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    caught, seen = target.dex_flags(d.state)
    index = {e.name.strip().upper(): e.natdex
             for e in target.entries if e.natdex}
    out = {"dex_caught": len(caught), "dex_seen": len(seen)}
    for name in names:
        nat = index.get(name.upper())
        out[name] = ("CAUGHT" if nat in caught else
                     "seen only" if nat in seen else "unknown")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True, help="YOUR fork, never line3")
    ap.add_argument("--out", default=None,
                    help="extra savestate to bank at the end")
    ap.add_argument("--balls", type=int, default=70)
    ap.add_argument("--spheal-budget", type=float, default=720.0,
                    help="seconds in the entrance room; SPHEAL is 45%% there")
    ap.add_argument("--snorunt-budget", type=float, default=4800.0,
                    help="seconds in the Ice Room; SNORUNT is a 10%% slot")
    ap.add_argument("--slice", type=float, default=30.0)
    ap.add_argument("--tz", default=None,
                    help="force a POSIX TZ instead of picking one")
    ap.add_argument("--feed", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="cold-read --state's dex flags and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    state = Path(args.state)
    # READ-ONLY, so it is allowed on a milestone: this is how the BEFORE
    # number gets read out of the canonical save without driving it.
    if args.verify_only:
        for k, v in cold_verify(state, ("SPHEAL", "SNORUNT", "SEALEO",
                                        "WALREIN")).items():
            log.info("  %-12s %s", k, v)
        return 0
    if state.name.startswith("line3") or state.name.startswith("milestone-"):
        ap.error("refusing to drive %s -- fork it first" % state.name)

    d = Driver(str(state))
    col = Collector(d, feed_name=args.feed or state.stem)
    col.BALL_TARGET = args.balls
    pin_mart(col)
    # CATCH, OR RUN -- never train, and never let the tactics layer have a
    # wild turn. See `install_policy`.
    install_policy(col)
    unwedge(d)
    before = col._caught_count()
    log.info("start: %s %s, dex %d, %d balls, %d money", d.map_name(),
             d.pos(), before, col.balls(), d.state.money())

    if args.tz:
        os.environ["TZ"] = args.tz
        time.tzset()
        table = tide_table(d.emu)
        hour = game_hour(datetime.now().astimezone(), local_time_offset(d))
        log.info("tide: TZ=%s forced -> game %02d:xx, %s", args.tz, hour,
                 "HIGH" if table[hour] else "LOW")
        tz = args.tz
    else:
        tz, _hour, _room = tune_tide(d)

    nurse = needs_nurse(col)
    if nurse:
        log.info("== nurse first: %s", nurse)
        arm(d, 480.0)
        col.heal()
    if col.balls() < args.balls:
        log.info("== restocking to %d balls (have %d)", args.balls,
                 col.balls())
        arm(d, 480.0)
        col.goto_map(BALL_TOWN, budget=300.0)
        arm(d, 480.0)
        col.restock_balls()
        log.info("   %d balls, %d money", col.balls(), d.state.money())

    rec = dungeon_of(ENTRANCE)
    _enable_surf(d)
    arm(d, 900.0)
    if not enter_dungeon(d, col, rec, budget=900.0):
        log.info("could not get into Shoal Cave (%s)", d.last_goto_reason)
        return 1
    if not cross_dungeon(d, ENTRANCE, rec.maps):
        log.info("inside %s but not in the entrance room", d.map_name())
        return 1
    d.nav.surfing = False
    try:
        drift = d.sync_grid()
    except Exception as exc:  # noqa: BLE001
        log.info("sync_grid: %s", str(exc)[:80])
        drift = 0
    flag, table_high, hour = tide_report(d)
    log.info("in %s %s: FLAG_SYS_SHOAL_TIDE=%s, table says %s at game "
             "%02d:xx, %d cells of live grid drift", d.map_name(), d.pos(),
             flag, "HIGH" if table_high else "LOW", hour, drift)

    left_spheal = missing_of(col, ["SPHEAL"])
    if left_spheal:
        log.info("== %s for SPHEAL", ENTRANCE)
        left_spheal = pin_hunt(d, col, rec, ENTRANCE, ["SPHEAL"],
                               args.spheal_budget, args.slice)
        col.save()
    else:
        log.info("SPHEAL already registered")

    left_ice = missing_of(col, ["SNORUNT"])
    if not left_ice:
        log.info("SNORUNT already registered")
    elif flag:
        # Not a routing failure and not worth an hour of retries: the
        # descents are water metatiles and no warp on them can fire.
        log.info("HIGH tide inside the cave -- the Inner Room's descents are "
                 "water metatiles, so the Ice Room cannot be entered and "
                 "SNORUNT is off the table (field_control_avatar.c:696)")
    else:
        # SPHEAL is 45% of the Ice Room's table too, so anything still owed
        # from the entrance room rides along instead of costing a second leg.
        want = ["SNORUNT"] + [s for s in left_spheal if s == "SPHEAL"]
        log.info("== %s for %s", ICE, ", ".join(want))
        arm(d, 600.0)
        if not cross_dungeon(d, ICE, rec.maps):
            log.info("the Ice Room chain broke on %s %s", d.map_name(),
                     d.pos())
        else:
            try:
                log.info("   %d cells of grid drift in %s", d.sync_grid(),
                         d.map_name())
            except Exception as exc:  # noqa: BLE001
                log.debug("sync_grid: %s", str(exc)[:70])
            left_ice = pin_hunt(d, col, rec, ICE, want,
                                args.snorunt_budget, args.slice)
        col.save()

    col.save()
    if args.out:
        try:
            d.save(args.out)
            log.info("banked %s", args.out)
        except Exception as exc:  # noqa: BLE001
            log.info("could not bank %s: %s", args.out, exc)
    flag, table_high, hour = tide_report(d)
    log.info("---- shoal_hunt result ----")
    log.info("TZ=%s, game %02d:xx, table %s, FLAG_SYS_SHOAL_TIDE=%s", tz,
             hour, "HIGH" if table_high else "LOW", flag)
    log.info("dex %d -> %d (live)", before, col._caught_count())
    for name in ("SPHEAL", "SNORUNT"):
        log.info("  %-8s %s", name,
                 "missing" if missing_of(col, [name]) else "CAUGHT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
