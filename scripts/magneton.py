#!/usr/bin/env python
"""MAGNETON: the 1% land slot inside New Mauville.

`pret/src/data/wild_encounters.json` MAP_NEW_MAUVILLE_INSIDE `land_mons` is the
stock twelve-slot land table and MAGNETON is slot 11 -- the 1% tail
(`ENCOUNTER_CHANCE_LAND_MONS_SLOT_11 - SLOT_10 == 1`,
pret/include/wild_encounter.h), L26, next to ELECTRODE. Every other slot is
VOLTORB or MAGNEMITE, both already registered on `milestone-dex132`, and so is
ELECTRODE -- MAGNETON is the only hole this table can close.

`NewMauville_Entrance` CANNOT close it: that table's tail is a sixth
VOLTORB/MAGNEMITE pair, no MAGNETON. So the hunt has to happen behind the
BASEMENT KEY door, in `NewMauville_Inside`.

Three measured facts shape the run:

* An encounter tile is not "any floor". `MetatileBehavior_IsLandWildEncounter`
  needs `TILE_FLAG_ENCOUNTER_TILE`, which MB_NORMAL does not have
  (pret/src/metatile_behavior.c:9) -- inside New Mauville the encounter floor
  is MB_INDOOR_ENCOUNTER (`:20`), 803 cells of it, which `nav` classifies as
  kind "grass". The pacing corridor below is checked against that set.
* `DoWildEncounterTest` rolls `Random() % 2880 < rate*16` per step, and the
  map's `encounter_rate` is 10 -- 5.56% a step, so a 1% slot is ~1800 steps
  away. It also multiplies the rate by 80/100 while on a BIKE
  (pret/src/wild_encounter.c:403-407), so this walks. Steps are driven by one
  long HELD direction (`Right:208`) instead of `step_dir` per tile: the engine
  keeps walking under a held button, so a leg of thirteen tiles costs thirteen
  step-times plus one wall bump instead of thirteen settle cycles, and the
  corridor's end walls make the leg self-correcting.
* MAGNETON's `catchRate` is 60 (`base_stats.h:2727`), not MAGNEMITE's 190. At
  full HP that is ~16% an ULTRA BALL, so the run buys a deep stack at Fortree
  (the nearest mart that stocks them) and NEVER attacks the target: our worst
  attacker is L36 and everything else is L64-L100, and a KO'd 1% slot costs
  another ~1800 steps. Flee everything else -- a declined encounter that gets
  fought is forty seconds of emulator on a map whose only value is the next
  encounter.

Getting in is `scripts/newmauville_hunt.py`'s work and this reuses it
(`Hunt.to_route110`, `Hunt.cross_cycling_road`, `Hunt.open_door`) with one
correction. `Hunt.route_in`'s `walk_leg(WEST_STAGE)` cannot be trusted to mount
Surf: on this save `goto` planned a LAND path off the west bank, took a one-way
ledge onto the cycling road at (26,24) and dead-ended --
"walked into a dead end at (26, 24) ... 106 cell(s) reachable from there and
(25, 25) is not one of them (one-way ledge)" -- then spent seven more attempts
re-asking. The mount and the swim are therefore hand-stepped: every cell of
SWIM below was read off `nav.grid('Route110')`.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

import collect  # noqa: E402
import newmauville_hunt as nm  # noqa: E402

log = logging.getLogger("magneton")

INSIDE = "NewMauville_Inside"
ENTRANCE = "NewMauville_Entrance"
ROUTE = "Route110"

#: National dex numbers. Only MAGNETON is missing on `milestone-dex132`;
#: ELECTRODE, VOLTORB and MAGNEMITE all read caught.
MAGNETON = 82

#: Held-button names, `emu.run_sequence` spelling.
HOLD = {"L": "Left", "R": "Right", "U": "Up", "D": "Down"}
#: Frames one walking step takes (`trek.STEP_FRAMES`).
STEP_FRAMES = 16

#: Elevation-3 north bank of Route110's west pond. The ONLY place a surfer can
#: mount on the walk-reachable side (`IsPlayerFacingSurfableFishableWater`
#: needs `PlayerGetZCoord() == 3`, pret/src/field_player_avatar.c:1121-1134).
NORTH_BANK = nm.WEST_SHORE
#: (14,19) -> (25,25), every cell water in `nav.grid('Route110')`: down the
#: x=14 column to row 22, east along row 22 (x7..x24 is unbroken water; x25 is
#: water with collision set on rows 20-22, hence the dogleg), then down.
SWIM = "D" + "DD" + "R" * 10 + "D" + "R" + "DD"

#: The pacing corridor: `NewMauville_Inside` row 36, x24..x36. Thirteen cells,
#: every one MB_INDOOR_ENCOUNTER and collision 0, walled at both ends (x23 is
#: the BLUE barrier column, x37 is rock), and it holds no coord_event -- the
#: arrival chamber's only barrier button is (30,38), one row down, and stepping
#: on it would swap the barriers and start a script. Reached from the warp
#: landing (32,33) by three steps south.
CORRIDOR_ROW = 36
CORRIDOR_X = (24, 36)
CORRIDOR_MID = (30, CORRIDOR_ROW)
#: Where `NewMauville_Entrance` warp 1 puts us (`nav.exits`).
INSIDE_LANDING = (32, 33)

#: Enough ULTRA BALLs that a 16%-a-throw target is not lost to bad luck:
#: 1 - 0.84**40 is 99.9%. Fortree is the nearest mart that stocks them
#: (`pret/data/maps/FortreeCity_Mart/scripts.inc`); Mauville's shelf stops at
#: GREAT BALL.
BALL_TARGET = 40
BALL_MART = "FortreeCity_Mart"
BALL_NAME = "ULTRA BALL"


def dump(d, map_name, x0, x1, y0, y1):
    """ASCII of the static grid: `.` floor, `#` blocked, `~` water, `,` grass."""
    grid = d.nav.grid(map_name)
    out = ["    " + "".join(str(x % 10) for x in range(x0, x1 + 1))]
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            c = grid[y][x] if y < len(grid) and x < len(grid[0]) else None
            if c is None:
                row.append("?")
                continue
            if d.nav._is_water(c):
                row.append("~" if c.collision == 0 else "w")
            elif c.collision:
                row.append("#")
            else:
                row.append("," if c.kind == "grass" else ".")
        out.append(f"{y:3d} " + "".join(row))
    return "\n".join(out)


def ball_in_bag(d):
    """The best ball actually in the bag: ULTRA, then GREAT, then anything."""
    balls = {k: v for k, v in (d.state.bag().get("poke_balls") or {}).items()
             if isinstance(v, int) and v > 0}
    for tag in ("ULTRA", "GREAT", "NET", "TIMER"):
        for name in balls:
            if tag in name.upper():
                return name
    return next(iter(balls), None)


def unarm(d):
    """Clear `Driver._journey_deadline`.

    MANDATORY BEFORE EVERY `goto`. It is a plain Driver-wide attribute that
    `goto` refuses against (`trek.py:857`) and nothing resets it, so a
    deadline left behind by somebody else's walk fails every later walk
    instantly. `Hunt.walk_leg` sets one per attempt and never clears it
    (`newmauville_hunt.py:248`) -- measured here as
    "GOTO False (3, 6) journey budget spent at (3, 6) heading for (1, 5)"
    on a three-step path inside a Mart, from a deadline set on Route110
    minutes earlier. `scripts/evolve_grind.py:151` and
    `scripts/pyre_shoal.py` carry the same warning.
    """
    d._journey_deadline = None


def off_warp(d, order="ULRD"):
    """Step off a door tile.

    A Gen 3 door/stair metatile sits at ELEVATION 0, and `find_path` is asked
    for a route at the elevation the PLAYER reads -- so a plan made while
    standing in a doorway starts from a z nothing else on the map shares.
    Measured in FortreeCity_Mart: `d.elevation()` was 0 on the entrance tile
    and 3 one step north.
    """
    unarm(d)
    if d.elevation() != 0:
        return True
    for direction in order:
        if d.step_dir(direction) and d.elevation() != 0:
            return True
    return d.elevation() != 0


def leave_shop(d, mart_map) -> bool:
    """Walk out of a Mart, because FLY is refused indoors.

    `Hunt.to_route110` handles "not flyable here" by walking to a Pokemon
    Center, which is a long way round from a shop that has its own door.
    """
    town = mart_map.split("_")[0]
    unarm(d)
    for exit_ in d.nav.exits(mart_map):
        if exit_["kind"] == "warp" and exit_.get("dest") == town:
            if d.take_warp(exit_["x"], exit_["y"]):
                break
    return d.map_name() == town


class Hunter:
    """Flee everything, throw at MAGNETON, count what walked past."""

    def __init__(self, driver, collector, want=("MAGNETON",)):
        self.d = driver
        self.c = collector
        self.want = {w.upper() for w in want}
        self.target = DexTarget(driver.emu, driver.names, driver.consts,
                                driver.nav, spec=driver.spec)
        self.encounters = 0
        self.thrown = 0
        self.steps = 0
        self.seen = {}
        # EVERY fight goes through this, not just the ones this file starts:
        # `goto`, `travel`, `take_warp` and `_cross_seam` all call
        # `Driver.fight()` with no policy and fall back to `battle_policy`
        # (trek.py:3159-3160). Left unset they would train on a VOLTORB.
        driver.battle_policy = self.policy
        collector.base_policy = lambda: self.policy

    # ---- decisions -------------------------------------------------------

    def policy(self, frame):
        """`("ball", ...)` for the target, `"flee"` for everything else.

        NEVER an attack on the target. Our weakest party member is L36 and the
        target is a L26 1% slot: a KO here is worth about 1800 steps, which is
        far more than the ~5 extra balls throwing at full HP costs.
        """
        enemy = frame.get("enemy") or {}
        species = (enemy.get("species") or "").upper()
        if species in self.want:
            ball = ball_in_bag(self.d)
            if ball:
                self.thrown += 1
                return ("ball", ball)
            log.info("  NO BALLS LEFT with a %s on the field", species)
        return "flee"

    def caught(self) -> set:
        got, _seen = self.target.dex_flags(self.d.state)
        return set(got)

    def done(self) -> bool:
        return MAGNETON in self.caught()

    # ---- battles ---------------------------------------------------------

    def battle(self):
        """Play whatever is on the field, and record what it was."""
        d = self.d
        if not d.in_battle():
            return
        for _ in range(120):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        species = "?"
        try:
            frame = d.battle_frame() or {}
            species = ((frame.get("enemy") or {}).get("species")
                       or "?").upper()
        except Exception as exc:  # noqa: BLE001 - a read never ends a run
            log.info("  battle_frame: %s", str(exc)[:70])
        self.encounters += 1
        self.seen[species] = self.seen.get(species, 0) + 1
        if species in self.want:
            log.info("*** encounter %d: %s -- throwing", self.encounters,
                     species)
        try:
            d.fight(policy=self.policy)
        except Exception as exc:  # noqa: BLE001 - never lose the run to one
            log.info("  fight raised: %s", str(exc)[:90])
        d.advance_scene(20000)

    def pump(self, rounds=8) -> bool:
        """Clear whatever owns input. True when the overworld is ours."""
        d = self.d
        for _ in range(rounds):
            if d.in_battle():
                self.battle()
                continue
            if d.scene_active():
                d.advance_scene(30000)
                continue
            return True
        return not d.scene_active() and not d.in_battle()

    # ---- movement --------------------------------------------------------

    def hand_steps(self, dirs, tries=6) -> bool:
        """Step a direction string by hand, absorbing encounters.

        `goto` is not usable for the crossing (see the module docstring), and
        `walk` alone stops dead on the first wild battle. Each letter is
        retried until the player actually moves.
        """
        d = self.d
        for i, ch in enumerate(dirs):
            for _ in range(tries):
                if not self.pump():
                    continue
                before = d.pos()
                d.walk(ch)
                if d.in_battle():
                    self.battle()
                if d.pos() != before:
                    break
            else:
                log.info("  stuck on step %d/%d (%s) at %s: %s", i + 1,
                         len(dirs), ch, d.pos(), d.last_step_reason)
                return False
        return True

    def sweep(self, direction, tiles) -> int:
        """Hold one direction for `tiles` steps. Returns cells actually moved.

        One held press, not `tiles` taps: the engine keeps walking while the
        button is down, so this is `tiles * STEP_FRAMES` of emulator instead of
        `tiles` settle cycles. Overshooting into the corridor's end wall is the
        point -- it re-anchors the position for free.
        """
        d = self.d
        before = d.pos()
        d.emu.run_sequence(f"{HOLD[direction]}:{tiles * STEP_FRAMES}")
        d.settle(180)
        now = d.pos()
        moved = abs(now[0] - before[0]) + abs(now[1] - before[1])
        self.steps += moved
        return moved

    def regroup(self) -> bool:
        """Get back onto the corridor from wherever we ended up."""
        d = self.d
        if d.map_name() != INSIDE:
            log.info("  off-map on %s at %s", d.map_name(), d.pos())
            return False
        if not self.pump():
            return False
        # The landing cell of a warp is a door metatile at elevation 0 and
        # `find_path` plans at the player's own elevation, so this has to
        # happen before any planning. Inside, the free direction is south.
        off_warp(d, "DLRU")
        for _ in range(6):
            if d.pos() == CORRIDOR_MID:
                return True
            path = d.nav.find_path(INSIDE, d.pos(), CORRIDOR_MID,
                                   d.elevation())
            if path is None:
                log.info("  no path from %s to the corridor", d.pos())
                return False
            if not self.hand_steps("".join(path)):
                continue
        return d.pos() == CORRIDOR_MID

    # ---- the hunt --------------------------------------------------------

    def route_in(self) -> bool:
        """Littleroot-to-the-corridor, resumable from anywhere on the way."""
        d = self.d
        h = nm.Hunt(d, self.c)
        # `Hunt.__init__` installs its own flee-everything policy; ours also
        # catches, so put it back.
        self.c.base_policy = lambda: self.policy
        if d.at_title():
            d.resume_from_title()
        self.pump()
        if d.map_name() == INSIDE:
            return self.regroup()
        if d.map_name() != ENTRANCE:
            if not self.swim_in(h):
                return False
        if not h.open_door():
            return False
        if d.map_name() != ENTRANCE:
            log.info("  not in the entrance room: %s", d.map_name())
            return False
        unarm(d)
        if not d.take_warp(*nm.INSIDE_WARP):
            log.info("  warp into Inside refused: %s", d.last_warp_reason)
            return False
        log.info("inside: %s %s", d.map_name(), d.pos())
        return self.regroup()

    def swim_in(self, h) -> bool:
        """MauvilleCity -> Route110 -> under the cycling road -> the door."""
        d = self.d
        # `Hunt.to_route110` short-circuits when already on Route110, and the
        # component we may be standing in (the cycling road, past a one-way
        # ledge) cannot reach the west pond at all. Fly out first.
        if d.map_name() == ROUTE and d.pos() not in (NORTH_BANK,):
            if not d.fly_to("MauvilleCity"):
                log.info("  could not fly out of %s: %s", ROUTE,
                         getattr(d, "last_field_reason", None))
                return False
        unarm(d)
        if not h.to_route110():
            return False
        unarm(d)
        if not h.walk_leg(*NORTH_BANK):
            log.info("  could not reach the north bank; on %s", d.pos())
            return False
        # `walk` only treats a land->water step as a MOUNT while `nav.surfing`
        # is set, and `_surf_sync` sets it from `can_surf()`.
        d._surf_sync()
        d.nav.surfing = True
        if not self.hand_steps(SWIM):
            return False
        if not d.is_surfing():
            log.info("  lost the surf blob at %s", d.pos())
            return False
        log.info("staged at %s elevation %s", d.pos(), d.elevation())
        if not h.cross_cycling_road():
            return False
        if d.is_surfing() and not self.hand_steps("R"):
            log.info("  could not dismount onto %s", nm.DISMOUNT)
            return False
        unarm(d)
        if not h.walk_leg(*nm.SHELF):
            return False
        unarm(d)
        if not d.take_warp(*nm.DOOR_WARP):
            log.info("  door warp refused: %s", d.last_warp_reason)
            return False
        off_warp(d)
        return d.map_name() in (ENTRANCE, INSIDE)

    def stock_balls(self) -> bool:
        """Buy a deep stack of ULTRA BALLs.

        Not `Collector.restock_balls`: that one buys the CHEAPEST ball on the
        shelf and would come home with GREAT BALLs (11.8% a throw here against
        ULTRA's 16.1%, catchRate 60 at full HP), and it walks off to a
        "basic-tier" mart when the shelf looks expensive. Money is 999,999 and
        the constraint is throws, not cost.
        """
        d = self.d
        have = (d.state.bag().get("poke_balls") or {}).get(BALL_NAME, 0)
        want = BALL_TARGET - have
        if want <= 0:
            return True
        log.info("stocking %s: have %d, want %d more", BALL_NAME, have, want)
        unarm(d)
        if not self.c.goto_map(BALL_MART, budget=300.0):
            log.info("  could not reach %s (%s)", BALL_MART,
                     d.last_goto_reason)
            return False
        off_warp(d)
        cell = self.c.clerk_cell(BALL_MART)
        if cell is None:
            log.info("  no clerk on %s", BALL_MART)
            return False
        unarm(d)
        if not d.talk_to(*cell):
            log.info("  clerk at %s: %s", cell, d.last_talk_reason)
            return False
        d.settle(120)
        for _ in range(5):
            if self.c.mart.is_open():
                break
            d.emu.run_sequence("A:4 .:40")
        if not self.c.mart.is_open():
            log.info("  the clerk did not open a shop")
            d.emu.run_sequence("B:4 .:20 B:4 .:20")
            return False
        ok = self.c.mart.buy(BALL_NAME, want)
        if not ok:
            log.info("  buy failed: %s", self.c.mart.last_reason)
        # B-only on the way out: a blind A in a shop list buys things.
        for _ in range(12):
            if not d.scene_active() and not self.c.mart.is_open():
                break
            d.emu.run_sequence("B:4 .:24")
        d.advance_scene(40000)
        now = (d.state.bag().get("poke_balls") or {}).get(BALL_NAME, 0)
        log.info("  %s %d -> %d", BALL_NAME, have, now)
        # OUT THE DOOR BEFORE ANYTHING TRIES TO FLY. Fly is refused indoors
        # and `to_route110`'s fallback for that is a walk to a Pokemon
        # Center, which from inside a shop is a long way round for a door
        # three tiles away.
        if not leave_shop(d, BALL_MART):
            log.info("  still inside %s", d.map_name())
        return now > have

    def pace(self, deadline) -> bool:
        """Walk the corridor until MAGNETON is registered or time runs out."""
        d = self.d
        lo, hi = CORRIDOR_X
        span = hi - lo + 2          # +1 so each leg ends against a wall
        direction = "L"
        dead = 0
        last_log = 0.0
        while time.time() < deadline:
            if self.done():
                return True
            if d.in_battle():
                self.battle()
                continue
            if d.scene_active():
                d.advance_scene(30000)
                continue
            x, y = d.pos()
            if d.map_name() != INSIDE or y != CORRIDOR_ROW \
                    or not lo <= x <= hi:
                if not self.regroup():
                    log.info("pacing lost the corridor at %s %s",
                             d.map_name(), d.pos())
                    return self.done()
                continue
            moved = self.sweep(direction, span)
            if d.in_battle():
                self.battle()
                continue
            dead = dead + 1 if moved == 0 else 0
            if dead >= 4:
                log.info("pacing pinned at %s (%s)", d.pos(),
                         d.last_step_reason)
                return self.done()
            direction = "R" if direction == "L" else "L"
            if time.time() - last_log > 60:
                last_log = time.time()
                log.info("%d steps, %d encounters, %d balls thrown, seen %s",
                         self.steps, self.encounters, self.thrown,
                         dict(sorted(self.seen.items())))
        return self.done()


def save(d, path):
    """Persist -- never while a script owns input (a wedged save poisons
    every later run; see `Collector.save`)."""
    for _ in range(8):
        if not d.scene_active():
            break
        d.emu.run_sequence("B:4 .:30")
    if d.scene_active():
        d.advance_scene(40000)
    if d.scene_active():
        log.info("NOT saving %s: a script still owns input", path)
        return False
    d.save(path)
    log.info("saved %s", path)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default="saves/mm.state")
    ap.add_argument("--out", default="saves/mm-out.state")
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--no-shop", action="store_true",
                    help="skip the Fortree ULTRA BALL run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(args.state)
    c = collect.Collector(d, feed_name=Path(args.state).stem)
    hunt = Hunter(d, c)

    before = hunt.caught()
    log.info("dex %d caught; MAGNETON in: %s", len(before),
             MAGNETON in before)
    if MAGNETON in before:
        log.info("nothing to do")
        return 0

    if not args.no_shop and not hunt.stock_balls():
        log.info("shopping failed; going in with %s",
                 d.state.bag().get("poke_balls"))
    if not hunt.route_in():
        log.info("FAILED to reach the corridor; on %s at %s",
                 d.map_name(), d.pos())
        return 1
    save(d, args.out)

    got = hunt.pace(time.time() + args.minutes * 60.0)
    after = hunt.caught()
    log.info("%d steps, %d encounters, %d balls thrown", hunt.steps,
             hunt.encounters, hunt.thrown)
    log.info("seen: %s", dict(sorted(hunt.seen.items())))
    log.info("dex %d -> %d; MAGNETON in: %s", len(before), len(after),
             MAGNETON in after)
    save(d, args.out)
    return 0 if got else 2


if __name__ == "__main__":
    raise SystemExit(main())
