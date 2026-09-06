#!/usr/bin/env python
"""Register MEDICHAM from Victory Road, without crossing Victory Road.

The dungeon gates the League EXIT, not its wildlife. Encounter tables are
per-map, so standing anywhere on a floor rolls that floor's table, and
MEDICHAM sits on the two floors this run has already proved it can stand on:

* `VictoryRoad_B1F` land slot 3, **10%**, L40-42
* `VictoryRoad_B2F` land slot 3, **15%**, L40-42

(`docs/gen3/guide/encounters.json:12888+` and `:13026+`, cross-checked against
the live `gWildMonHeaders` through `dex.WildTable.for_map` -- B1F reads
`10% MEDICHAM / 35% GOLBAT / 35% HARIYAMA / 15% LAIRON / 5% MEDITITE` on
land, plus `70% GRAVELER / 30% GEODUDE` under Rock Smash.)

**GRAVELER was on this ticket and is already CAUGHT.** natdex 75 is set in
the dex flags on the current line, so the Rock Smash half of B1F is not worth
a single rock: its only unique species is Graveler and Geodude is registered
too. Verified before sailing, not assumed -- `missing_targets()` re-checks the
flags every pass and stops the moment the objective is closed.

Why this is not `vr_hunt.py`: that script hands the floor choice to
`Collector.run`, which orders maps by species count and reaches a floor only
if `goto_map` happens to route there. The 1F -> B1F descent is the one leg
plain routing cannot do -- (9,14) is behind boulders on 1F, so `find_path`
returns a plan the engine refuses on move one. `boulder_solver.walk` is the
proven way down and this script does it explicitly, then paces exactly one
floor.

Route, all of it previously verified (`docs/gen3/PROGRESS.md` session
port-64):

    EverGrandeCity(18,41) -> 1F(15,40) -> 1F(9,14) -> B1F(8,3)
      [optional] B1F(30,25) -> B2F(30,25)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from collect import Collector  # noqa: E402
import boulder_solver as bs  # noqa: E402
import league_chain  # noqa: E402
import league_run  # noqa: E402
import share_grind  # noqa: E402
# `skypillar_grind` owns the shop driver because that is where it was needed
# first, and duplicating it here would mean two copies to keep in step. If a
# third caller turns up it belongs in the package proper. Title-screen
# recovery is already there: `Driver.at_title` / `Driver.resume_from_title`.
from skypillar_grind import (  # noqa: E402
    BALL_FLOOR, BALL_STOCK, HEAL_TOWN, go, stock_balls, to_open_air,
)

log = logging.getLogger("vichunt")

#: The dex slot this hunt owes. One name, because it is the only Victory Road
#: species left: ARON, MAKUHITA, HARIYAMA, LAIRON, LOUDRED, WHISMUR, MEDITITE,
#: MAWILE, SABLEYE, GEODUDE and GRAVELER are all registered already.
TARGETS = ("MEDICHAM",)

#: 1F's own stairs down, and where it lands. From the SOUTH entrance (15,40)
#: this is the only door out of the entrance component that reaches B1F's
#: entrance pocket.
DESCENT = ((9, 14), "VictoryRoad_B1F")

#: The floors worth pacing, best rate first. B2F is the fallback and costs the
#: proven (30,25) crossing to reach.
FLOORS = ("VictoryRoad_B1F", "VictoryRoad_B2F")

#: A cell that is only in the ENTRANCE region of each floor, used to tell the
#: two halves of a floor apart. B1F's entrance region is 301 cells with 277
#: encounter tiles; the goal-side pocket the League door leads into has NINE,
#: and pacing it tripped the stall watchdog outright ("abandoning
#: VictoryRoad_B1F: pinned at (22,22) for 300s") -- nine cells is not enough
#: movement for the published picture to change, and it is not enough walking
#: to roll many encounters either: six in twelve minutes, against a 10%
#: MEDICHAM slot. Same table, a fraction of the throughput.
GOOD_POCKET = {"VictoryRoad_B1F": (8, 3), "VictoryRoad_B2F": (30, 25)}


def in_good_pocket(d) -> bool:
    want = GOOD_POCKET.get(d.map_name())
    if want is None:
        return True
    try:
        return want in d.nav.reachable(d.map_name(), d.pos(), d.elevation())
    except Exception:  # noqa: BLE001 - an unreadable floor is not a reason to leave
        return True


#: Ball economy is `skypillar_grind`'s: MEDICHAM's catch rate is 90 and an
#: L100 lead makes `Catcher._would_ko` true on turn one, so the ball goes in
#: at FULL HP and an ULTRA BALL lands ~24% of the time. Nothing in this cave
#: flees, so a battle runs until the ball sticks -- about 4 throws per catch.
#: The plateau has a Center but NO Mart, so shopping happens before the
#: waterfall or not at all.


class Hunt(Collector):
    """`Collector`, restricted to Victory Road and to one species.

    Subclassed rather than re-implemented because the catch decision is the
    part that is hard and it is already right in there: the plan is computed
    ONCE from a settled frame (`state.battle_ready()` first, or the enemy
    species reads None and every wild is declined as a "trainer battle"), the
    dex check runs AHEAD of the ball reserve, and `pace_map` walks with `goto`
    rather than hand-stepping -- which spun 7.5 million refused steps in 150
    seconds the one time it was hand-rolled.
    """

    def missing_targets(self) -> set:
        caught, _seen = self.target.dex_flags(self.d.state)
        out = set()
        for entry in self.target.achievable:
            name = (entry.rom_name or entry.name).upper()
            if name in TARGETS and entry.natdex not in caught:
                out.add(name)
        return out


def settle(d) -> None:
    """Absorb whatever the dungeon threw at us. It throws a lot."""
    for _ in range(8):
        if d.in_battle():
            # `fight` first, and only advance the scene once the battle is
            # actually over -- advance_scene's A press lands on the battle
            # menu otherwise and parks it on the move list.
            d.fight(policy=Driver.damage_first)
            if not d.in_battle():
                d.advance_scene(60000)
        elif d.scene_active():
            d.advance_scene(60000)
            d.close_menus()
        else:
            return


#: Ever Grande's TWO doors into Victory Road 1F. Read off the live warp
#: table: (18,5)->League, (27,48)->Centre, (18,41)->VICTORY_ROAD_1F,
#: (18,27)->VICTORY_ROAD_1F.
#:
#: LOWER_DOOR lands on 1F (15,40), whose component reaches (9,14) -> B1F
#: (8,3): the 277-encounter-tile entrance region. It is the one worth having
#: and it is behind the waterfall.
#:
#: UPPER_DOOR lands on 1F (39,5), whose component reaches 1F (21,32) -> B1F
#: (20,21): the 9-cell goal pocket. SAME B1F table, same 10% MEDICHAM slot,
#: far less walking per minute -- but it needs no waterfall, and on this line
#: that is decisive. **`can_waterfall()` is False**: HM07 sits in the bag and
#: no party member was ever taught WATERFALL (PELIPPER carries SURF, SPIT UP,
#: FLY, HYDRO PUMP), so `league_run.climb` can never ride (18,68) and logged
#: "could not climb the falls (at (18, 68))" on every attempt. Teach WATERFALL
#: over SPIT UP and the lower door -- and the good pocket -- opens up.
LOWER_DOOR = league_run.VICTORY_ROAD_DOOR          # (18,41) -> 1F (15,40)
UPPER_DOOR = (18, 27)                              # -> 1F (39,5)

#: 1F (39,5)'s own way down to B1F, for the upper route.
UPPER_DESCENT = ((21, 32), "VictoryRoad_B1F")


#: The sea legs onto the beach, one map connection each. Route128's `right`
#: connection is Ever Grande (`pret/data/maps/Route128/map.json`), so this
#: lands at SEA LEVEL, below the falls.
BEACH_LEGS = ("Route127", "Route128", "EverGrandeCity")


def at_south_door(d) -> bool:
    """Can we actually walk to the dungeon's south door from where we stand?

    THE FLY LANDING IS THE WRONG PLATEAU. Once the League has been entered,
    flying to Ever Grande puts the player at (18,6) -- the UPPER plateau, 232
    cells holding the League door and nothing else -- and `league_run.climb`
    then returns True on the spot, because `on_plateau` is only `y <= 59`.
    Row 37 is solid wall across the full map width, so the lower plateau
    cannot be walked to from up there at all. The symptom was four rounds of
    "attempt N landed on VictoryRoad_B1F (21,26) -- retrying": `take_warp`
    could not approach (18,41), so the hunt ended up in B1F's 9-cell goal
    pocket instead of the 277-cell entrance region, and paced a floor it had
    reached by accident.
    """
    if d.map_name() != "EverGrandeCity":
        return False
    below = (LOWER_DOOR[0], LOWER_DOOR[1] + 1)
    try:
        comp = set(d.nav.reachable("EverGrandeCity", d.pos(), d.elevation()))
    except Exception:  # noqa: BLE001
        return False
    return LOWER_DOOR in comp or below in comp


def to_plateau(d) -> bool:
    """Stand on Ever Grande somewhere a Victory Road door can be reached.

    Flying is tried first because it usually wins. The sea legs plus the
    waterfall at (18,68) are the only way onto the LOWER plateau, and
    `league_run.climb` is what rides them -- but only if somebody can
    actually use Waterfall, which on this line nobody can. So the falls are
    attempted exactly once, and never when `can_waterfall()` already says no:
    sailing to the beach to be refused at (18,68) cost four full round trips
    of "landed on the wrong plateau -- sailing in from MossdeepCity" before
    this was gated.
    """
    if d.map_name().startswith("VictoryRoad"):
        return True
    share_grind.unwedge(d)
    if d.map_name() != "EverGrandeCity" and not league_run.to_city(d):
        log.info("could not reach Ever Grande (at %s)", d.map_name())
        return False
    if at_south_door(d):
        return True
    if not d.can_waterfall():
        # The upper door is on the fly landing's own component, so there is
        # nothing left to do here -- `descend` picks the door.
        log.info("no WATERFALL in the party -- taking the upper door %s "
                 "from %s", UPPER_DOOR, d.pos())
        return True
    if league_run.climb(d) and at_south_door(d):
        return True
    log.info("landed on the wrong plateau (%s) -- sailing in from %s",
             d.pos(), HEAL_TOWN)
    if not to_open_air(d) or not d.fly_to(HEAL_TOWN):
        log.info("   could not fly to %s from %s", HEAL_TOWN, d.map_name())
        return at_south_door(d)
    for leg in BEACH_LEGS:
        if not go(d, leg, tries=3):
            log.info("   stuck at %s heading for %s", d.map_name(), leg)
            return at_south_door(d)
    if not league_run.climb(d):
        log.info("   could not climb the falls (at %s)", d.pos())
    return at_south_door(d)


def descend(d, tries=8) -> bool:
    """Ever Grande -> 1F -> B1F, the descent plain routing cannot plan.

    Picks the door by REACHABILITY, then the matching stairs. The two routes
    are separate systems on 1F -- (15,40)'s component reaches (9,14) and
    (39,5)'s reaches (21,32), and neither reaches the other -- so the door and
    the descent cell have to be chosen as a pair.
    """
    if d.map_name() in FLOORS:
        return True
    south = at_south_door(d)
    if d.map_name() == "EverGrandeCity":
        league_run.heal_on_plateau(d)
        door = LOWER_DOOR if south else UPPER_DOOR
        if not league_chain.enter(d, door, "VictoryRoad_1F"):
            # Getting in by another door is still getting in: judge by the
            # floor we are standing on, not by the plan we had.
            if d.map_name() in FLOORS:
                log.info("   entered %s %s by another door", d.map_name(),
                         d.pos())
                return True
            log.info("could not enter Victory Road by %s from %s", door,
                     d.pos())
            return False
    if d.map_name() != "VictoryRoad_1F":
        return d.map_name() in FLOORS
    cell, lands = DESCENT if south else UPPER_DESCENT
    if not south:
        # The upper stairs need no Sokoban -- (21,32) is a plain walk from
        # (39,5) -- so use nav and skip the boulder solver entirely.
        for _ in range(4):
            try:
                if d.pos() != cell and not d.goto(*cell, on_battle="fight"):
                    log.info("   could not reach 1F%s (at %s): %s", cell,
                             d.pos(), d.last_goto_reason)
                break
            except TravelInterrupted:
                settle(d)
            if d.map_name() != "VictoryRoad_1F":
                break
        if d.map_name() == lands:
            league_chain.step_off(d, cell)
            return True
        return league_chain.enter(d, cell, lands)
    # `bs.walk`, NOT `goto`. 1F's route to (9,14) needs boulder pushes and
    # `find_path` cannot see boulders at all, so preferring nav here produced
    # a plan the engine refused on move one.
    walked = False
    for _ in range(4):
        try:
            walked = bs.walk(d, cell, tries=tries, smashing=True)
            break
        except TravelInterrupted:
            log.info("   battle mid-descent at %s", d.pos())
            settle(d)
            if d.pos() == cell:
                walked = True
                break
    # ARRIVING IS WORKING. Walking onto the warp cell fires it, so the map
    # changes mid-walk and a position check then compares against the floor we
    # just left -- which reported "could not reach (9,14)" while standing on
    # B1F, having descended correctly.
    if d.map_name() == lands:
        league_chain.step_off(d, cell)
        log.info("   warped mid-walk: now %s %s", d.map_name(), d.pos())
        return d.map_name() == lands
    if not walked and d.pos() != cell:
        log.info("   could not reach %s on 1F (at %s)", cell, d.pos())
        return False
    return league_chain.enter(d, cell, lands)


def cross_to_b2f(d) -> bool:
    """B1F(30,25) -> B2F, for MEDICHAM's better 15% table.

    The two cells share coordinates, so arriving lands the player straight
    onto the destination floor's own warp and the still-held direction fires
    it again -- the crossing bounced B1F -> B2F -> B1F while `take_warp`
    correctly reported True. `league_chain.enter` steps off the cell before
    judging, which is what makes it stick.
    """
    if d.map_name() == "VictoryRoad_B2F":
        return True
    if d.map_name() != "VictoryRoad_B1F":
        return False
    cell = (30, 25)
    for _ in range(4):
        try:
            if bs.walk(d, cell, tries=8, smashing=True):
                break
        except TravelInterrupted:
            settle(d)
        if d.pos() == cell or d.map_name() == "VictoryRoad_B2F":
            break
    if d.map_name() == "VictoryRoad_B2F":
        league_chain.step_off(d, cell)
        return True
    if d.pos() != cell:
        log.info("   could not reach B1F%s (at %s)", cell, d.pos())
        return False
    return league_chain.enter(d, cell, "VictoryRoad_B2F")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--feed", default="default")
    ap.add_argument("--minutes", type=float, default=150.0)
    ap.add_argument("--slice", type=float, default=150.0)
    #: Passes to spend on B1F before paying for the (30,25) crossing to B2F's
    #: 15% table. Five slices is ~12 minutes, which at B1F's rate is enough
    #: encounters that a miss means something is wrong with the pacing rather
    #: than with the dice.
    ap.add_argument("--b1f-passes", type=int, default=5)
    ap.add_argument("--no-shop", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = a.out or a.state

    d = Driver(a.state)
    if d.at_title() and not d.resume_from_title():
        log.info("ABORT: the save never came back to the field (cb=%s)",
                 d.state.callback_name())
        return 1
    # OUTDOORS FIRST. `Driver.resume_from_title` lands in a bedroom, and Fly
    # is refused indoors -- so both the shop trip and `to_city` need open air
    # before they can plan anything.
    if not to_open_air(d):
        log.info("WARNING: cannot Fly from %s -- carrying on", d.map_name())
    hunt = Hunt(d, feed_name=a.feed or None)
    deadline = time.time() + a.minutes * 60

    before = hunt._caught_count()
    want = hunt.missing_targets()
    log.info("START %s %s | dex %d caught | want %s", d.map_name(), d.pos(),
             before, sorted(want) or "nothing")
    if not want:
        log.info("MEDICHAM is already registered -- nothing to do")
        return 0
    if not a.no_shop:
        stock_balls(hunt)
    # PP, NOT HP, IS THE BINDING CONSTRAINT. The heal branch below only looks
    # at the lead's HP, so a lead arriving with its main attack spent spends
    # the hunt punching -- and every turn `fight` cannot take is a turn the
    # catcher never gets. Heal on the mainland, before the waterfall: Ever
    # Grande's Centre is reachable but getting back down to it costs the
    # climb.
    try:
        log.info("pre-hunt heal: %s", d.heal_at_nearest_center())
    except Exception as exc:  # noqa: BLE001 - a missed heal never stops the hunt
        log.info("pre-hunt heal raised %s: %s", type(exc).__name__,
                 str(exc)[:70])
    d.save(out)

    passes = 0
    #: Re-entry attempts spent trying to trade a small pocket for the
    #: entrance region. Bounded: a floor we cannot swap for a better one is
    #: still the right table, and pacing the small pocket beats spending the
    #: budget on the plateau.
    reentries = 0
    while time.time() < deadline:
        # LET `fight` OWN THE BATTLE. NEVER PRESS INTO ONE. `advance_scene`
        # presses A when it judges the frame stalled, and A on the battle
        # ACTION menu selects FIGHT and opens the MOVE menu -- so calling it
        # while a battle is still live parks the battle there and the catcher
        # is never asked for its turn. See the long note in
        # `skypillar_grind.main`; this loop had the identical bug.
        if d.in_battle():
            for _ in range(4):
                hunt.fight()
                if not d.in_battle():
                    break
            if d.in_battle():
                log.info("battle still live after 4 fight() passes "
                         "(cb=%s msg=%r)", d.state.callback_name(),
                         (d.state.message() or "")[:40])
            else:
                d.advance_scene(40000)
            continue
        d.close_menus()
        lead = d.state.party()[0]
        if lead.hp * 4 < lead.max_hp:
            # The plateau's own Center is the only one in reach, and reaching
            # it means leaving the dungeon -- so heal only when the lead is
            # genuinely critical, and re-descend afterwards.
            log.info("lead at %d/%d -- healing", lead.hp, lead.max_hp)
            if not share_grind.to_center(d):
                log.info("   no Centre reachable; carrying on hurt")
            continue
        # ONLY CHASE THE ENTRANCE REGION IF IT IS ACTUALLY REACHABLE. It sits
        # behind the waterfall, so with no WATERFALL in the party this guard
        # would send the hunt out of the dungeon and back in forever, never
        # arriving anywhere better and never pacing at all -- strictly worse
        # than the small pocket it was trying to improve on.
        wrong_pocket = d.map_name() in FLOORS and not in_good_pocket(d) \
            and reentries < 2 and d.can_waterfall()
        if d.map_name() not in FLOORS or wrong_pocket:
            if wrong_pocket:
                reentries += 1
                log.info("on %s %s but outside the entrance region "
                         "-- leaving to re-enter (%d)", d.map_name(), d.pos(),
                         reentries)
                # Fly is refused inside a cave, so the way out is warps until
                # somewhere Fly accepts. `to_center` already knows that walk;
                # it was written for exactly this shape of one-way interior.
                if not share_grind.to_center(d):
                    log.info("   could not leave; pacing this pocket instead")
                    reentries = 99
                    continue
            if not to_plateau(d) or not descend(d):
                log.info("could not get onto a floor (at %s %s)",
                         d.map_name(), d.pos())
                d.settle(120)
            d.save(out)
            continue
        if hunt.balls() < 1:
            log.info("out of balls on %s -- leaving to shop", d.map_name())
            if not share_grind.to_center(d):
                log.info("   could not leave the dungeon; stopping")
                break
            stock_balls(hunt)
            continue
        if passes >= a.b1f_passes and d.map_name() == "VictoryRoad_B1F":
            log.info("B1F gave nothing in %d passes -- crossing to B2F (15%%)",
                     passes)
            if cross_to_b2f(d):
                log.info("   on %s %s", d.map_name(), d.pos())
                d.save(out)
            else:
                log.info("   crossing refused; staying on B1F")
                a.b1f_passes = passes + a.b1f_passes
        passes += 1
        got = hunt.pace_map(min(deadline, time.time() + a.slice),
                            terrain="grass")
        want = hunt.missing_targets()
        log.info("pass %d on %s: +%d new | dex %d | still want %s | "
                 "%.0f min left", passes, d.map_name(), got,
                 hunt._caught_count(), sorted(want) or "nothing",
                 (deadline - time.time()) / 60)
        d.save(out)
        if not want:
            break

    after = hunt._caught_count()
    log.info("DONE at %s %s | dex %d -> %d | still missing %s",
             d.map_name(), d.pos(), before, after,
             sorted(hunt.missing_targets()) or "nothing")
    d.save(out)
    return 0 if not hunt.missing_targets() else 1


if __name__ == "__main__":
    raise SystemExit(main())
