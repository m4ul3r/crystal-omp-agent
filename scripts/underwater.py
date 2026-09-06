#!/usr/bin/env python
"""RELICANTH, off the Route 124 seafloor.

RELICANTH lives in exactly one kind of place: the ``dive`` table of
Underwater1/Underwater2, which fires only on the seaweed tiles down there --
``MetatileBehavior_IsWaterWildEncounter`` is "surfable AND encounter"
(pret/src/metatile_behavior.c:902-909), and on Underwater1 that is 406
MB_SEAWEED_NO_SURFACING cells inside 6400. Nothing in the canonical party can
dive (PELIPPER's TMHM learnset stops at HM02/HM03) but box 0 slot 7 holds a
L52 LOMBRE that already KNOWS Dive, so no HM is spent and no move is lost.

Three numbers decide how this is done, and all three come off the cartridge:

* the dive table's ``encounter_rate`` is 4 (pret/src/data/wild_encounters.json,
  Underwater1_Sapphire), and ``DoWildEncounterTest`` rolls
  ``Random() % 2880 < rate * 16`` -- so 64/2880, about one encounter every 45
  steps ON seaweed;
* RELICANTH holds slots 3 and 4 of five, which at the water slot weights
  (60/30/5/4/1) is 5% of those encounters -- roughly 900 steps per RELICANTH;
* a step underwater costs about 0.45 s of wall clock, MEASURED, so that is a
  seven-minute walk and change.

Which is why this does NOT pace with `goto` the way `Collector.pace_map` does.
Measured on the same seafloor, `pace_map` produced ONE encounter in 240
seconds: the planner is pure Python over an 80x80 grid and re-plans per leg,
so nearly all of the budget went into `reachable`/`find_path` fills instead of
into steps. Oscillating between two ADJACENT seaweed cells with `step_dir`
needs no planning at all, and it keeps `prevMetatileBehavior ==
curMetatileBehavior`, which skips the extra `DoGlobalWildEncounterDiceRoll`
the engine applies when you step onto a different behaviour
(wild_encounter.c:480-487). Same seafloor, same save: 86 steps and 2
encounters in 68 seconds.

Everything that is not RELICANTH is FLED, not fought. The party holds ~75
attacking PP in total and the seafloor is wall-to-wall encounters, so fighting
them would run the party dry twenty battles into a hunt that needs twenty
more -- and a party that cannot damage anything cannot travel back to a nurse.

    python scripts/underwater.py --state saves/uw-out.state

WAILORD, the other half of this errand's original brief, was registered
upstream by the grind engine before this run reached it (milestone-dex134),
so the WAILMER machinery that used to live here is gone rather than left
lying around.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from collect import Collector  # noqa: E402
from share_grind import to_center, unwedge  # noqa: E402
from shard_trade import _enable_surf, _reach, _here_reachable  # noqa: E402

log = logging.getLogger("underwater")

BOX_SIZE = 30

#: The seafloor under Route 124. Route124's connections name it as a `dive`
#: seam (pret/data/maps/Route124/map.json) and a dive lands on the SAME
#: coordinates on the far map (`SetDiveWarp`, src/overworld.c:583-600).
SURFACE = "Route124"
SEAFLOOR = "Underwater1"

#: The mart that stocks the only ball with an underwater multiplier.
BALL_MART = "MossdeepCity_Mart"

#: Balls to take down. A DIVE BALL is 3.5x while diving and RELICANTH's catch
#: rate is 25, so a full-HP throw lands about one in five; twenty balls is a
#: 99% chance of keeping the first one we meet.
BALL_TARGET = 60
BALL_PREFS = ("DIVE BALL", "ULTRA BALL", "NET BALL", "GREAT BALL",
              "POKE BALL")

QUARRY = "RELICANTH"

NEIGHBOURS = (("L", -1, 0), ("R", 1, 0), ("U", 0, -1), ("D", 0, 1))
OPPOSITE = {"L": "R", "R": "L", "U": "D", "D": "U"}


def dex_target(d) -> DexTarget:
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def registered(d, target, name: str) -> bool:
    """Is `name`'s CAUGHT flag set right now?

    Asked of the live dex flags rather than of the party, because a catch made
    with a full party goes straight to a box (`GiveMonToPlayer` ->
    `SendMonToPC`, src/pokemon_2.c:964-983) and the flag is what is being
    scored.
    """
    sid = d.consts.species.get("SPECIES_" + name.upper())
    if not sid:
        return False
    return target.evolutions.natdex(sid) in target.dex_flags(d.state)[0]


def party_index(d, name: str):
    """Party slot of a species or nickname, or None.

    Deposits COMPACT `gPlayerParty`, so an index read before one is wrong
    after it; everything here looks the slot up by name when it uses it.
    """
    want = name.upper()
    for i, m in enumerate(d.state.party()):
        if getattr(m, "is_egg", False):
            continue
        if want in (d.names.species(m.species).upper(),
                    (m.nickname or "").upper()):
            return i
    return None


def boxed_slot(d, target, name: str):
    """``(box, slot)`` of the first boxed mon of a species, or None."""
    want = name.upper()
    for flat, mon in target.boxed():
        if d.names.species(mon.species).upper() == want:
            return (flat // BOX_SIZE, flat % BOX_SIZE)
    return None


# ---- getting a diver into the party --------------------------------------

DIVER = "LOMBRE"


def prep(d, target) -> bool:
    """Put the boxed LOMBRE in the party so DIVE exists.

    A slot has to be freed first -- WITHDRAW with six answers "Your party is
    full!" -- and the mon that gives it up must be carrying NOTHING, because a
    mon deposited while holding an item takes the item into the box with it
    and the SHELGON is holding another agent's EXP. SHARE.
    """
    if d.can_dive():
        return True
    if party_index(d, DIVER) is None:
        if not to_center(d):
            log.info("no Pokemon Center reached (at %s)", d.map_name())
            return False
        st = Storage(d)
        if not st.pc_cells():
            log.info("no PC on %s", d.map_name())
            return False
        party = [m for m in d.state.party() if not getattr(m, "is_egg", False)]
        if len(party) >= 6:
            free = [(i, m) for i, m in enumerate(party) if not m.held_item]
            if not free:
                log.info("every party member holds an item")
                return False
            i, victim = min(free, key=lambda p: p[1].level or 0)
            log.info("depositing %s L%s to free a slot",
                     d.names.species(victim.species), victim.level)
            if not st.deposit(i):
                log.info("deposit refused: %s", st.last_reason)
                return False
        where = boxed_slot(d, target, DIVER)
        if where is None:
            log.info("no boxed %s", DIVER)
            return False
        log.info("withdrawing %s from box %d slot %d", DIVER, *where)
        if not st.withdraw(*where):
            log.info("withdraw refused: %s", st.last_reason)
            return False
        st.close()
    log.info("party: %s | DIVE = %s",
             [f"{d.names.species(m.species)} L{m.level}"
              for m in d.state.party()], d.field_moves().get("DIVE"))
    return d.can_dive()


# ---- shopping -------------------------------------------------------------

def buy_balls(d, col, want=BALL_TARGET) -> int:
    """Stock DIVE BALLs at Mossdeep.

    Money is not the constraint (999,999 in the bank); the catch rate is. The
    DIVE BALL is the only ball in the game whose multiplier applies while
    diving, and Mossdeep is the mart that stocks it -- ULTRA 1200 / NET 1000 /
    DIVE 1000, read off the shelf rather than assumed.
    """
    have = col.balls()
    if (d.state.bag().get("poke_balls") or {}).get("DIVE BALL", 0) >= 20:
        log.info("%d balls already, DIVE BALLs among them -- not shopping",
                 have)
        return have
    if not col.goto_map(BALL_MART, budget=300.0):
        log.info("could not reach %s (%s)", BALL_MART, d.last_goto_reason)
        return have
    cell = col.clerk_cell(BALL_MART)
    if cell is None:
        log.info("no clerk on %s", BALL_MART)
        return have
    try:
        d.talk_to(*cell)
    except Exception as exc:  # noqa: BLE001
        log.info("clerk: %s", str(exc)[:70])
        return have
    d.settle(120)
    for _ in range(4):
        if col.mart.is_open():
            break
        d.emu.run_sequence("A:4 .:40")
    if not col.mart.is_open():
        log.info("the clerk did not open a shop")
        d.emu.run_sequence("B:4 .:20 B:4 .:20")
        return have
    shelf = {r["name"].upper(): r["price"] for r in col.mart.items()}
    log.info("shelf: %s", shelf)
    for name in BALL_PREFS:
        if name not in shelf:
            continue
        qty = max(0, want - col.balls())
        if qty <= 0:
            break
        log.info("buying %dx %s at %d", qty, name, shelf[name])
        if col.mart.buy(name, qty):
            break
        log.info("%s: %s", name, col.mart.last_reason)
    # B ONLY on the way out: a blind A in a shop list buys things.
    for _ in range(12):
        if not d.scene_active() and not col.mart.is_open():
            break
        d.emu.run_sequence("B:4 .:24")
    d.advance_scene(40000)
    log.info("balls %d -> %d (%s)", have, col.balls(),
             d.state.bag().get("poke_balls"))
    return col.balls()


# ---- the dive -------------------------------------------------------------

def dive_gate(d):
    """The nearest diveable tile we can actually surf to, or None.

    `nav.dive_gates` applies the engine's own `MetatileBehavior_IsDiveable`
    test to all 6400 cells of Route124 and answers with 188; the ones that
    matter are the ones in OUR component, because Route124 is a reef maze and
    most of its water sits behind collision.
    """
    gates = set(d.nav.dive_gates(SURFACE, "dive"))
    mine = _here_reachable(d, SURFACE)
    here = d.pos()
    usable = sorted(gates & mine,
                    key=lambda c: abs(c[0] - here[0]) + abs(c[1] - here[1]))
    log.info("%d dive gates on %s, %d reachable from %s; nearest %s",
             len(gates), SURFACE, len(usable), here,
             usable[0] if usable else None)
    return usable[0] if usable else None


def walk_to(d, x: int, y: int, budget: float = 150.0) -> bool:
    """`goto`, with the journey deadline RE-ARMED instead of inherited.

    `Driver._journey_deadline` is set by callers and never cleared, so an
    expired one left over from an earlier leg makes `goto` and `take_warp`
    refuse every approach cell -- with no reason that names the clock.
    Reported live by a sibling agent; armed per call and cleared here.
    """
    d._journey_deadline = time.time() + budget
    try:
        return bool(d.goto(x, y, on_battle="fight"))
    finally:
        d._journey_deadline = None


def descend(d) -> bool:
    """Get onto the Route 124 seafloor."""
    if d.map_name() == SEAFLOOR:
        return True
    _enable_surf(d)
    if d.map_name() != SURFACE and not _reach(d, SURFACE, budget=420.0):
        log.info("could not reach %s (at %s)", SURFACE, d.map_name())
        return False
    for _ in range(3):
        gate = dive_gate(d)
        if gate is None:
            return False
        if d.pos() != gate:
            try:
                if not walk_to(d, *gate, budget=240.0):
                    log.info("could not reach the dive tile %s: %s", gate,
                             d.last_goto_reason)
                    continue
            except TravelInterrupted:
                d.fight()
                d.advance_scene(40000)
                continue
        if d.dive():
            log.info("dived at %s -> %s %s", gate, d.map_name(), d.pos())
            return True
        log.info("dive refused at %s: %s", gate, d.last_field_reason)
    return False


# ---- the hunt -------------------------------------------------------------

class Diver(Collector):
    """A collector that FLEES anything it is not shopping for.

    `Collector.fight` plays every wild with the training policy, which is
    right for a sweep that also wants levels and wrong on a seafloor where the
    only thing wanted is one 5% slot: the party has ~75 attacking PP and there
    is no nurse for four map hops.
    """

    def map_species(self) -> frozenset:
        """Species ids this map's tables can actually produce.

        The sanity check on a battler read. `gBattleMons[1]` is STALE for a
        while after `state.battle_ready()` goes true -- it can still hold the
        PREVIOUS battle's mon -- so a decision taken off the first readable
        frame will happily flee a dex-new species because it thinks it is
        looking at the CHINCHOU from two encounters ago. Reported live by
        RodHunt; the fix is to wait for the action menu AND to disbelieve any
        species this map cannot yield.
        """
        try:
            return frozenset(r.species
                             for r in self.target.wild.for_map(
                                 self.d.map_name()))
        except Exception:  # noqa: BLE001
            return frozenset()

    def enemy_species_id(self) -> int:
        d = self.d
        table = self.map_species()
        sid = 0
        for _ in range(150):
            if not d.state.in_battle():
                break
            try:
                sid = int(d.battle.battler(1).species)
            except Exception:  # noqa: BLE001
                sid = 0
            if d.battle.at_action_menu() and sid and (
                    not table or sid in table):
                return sid
            d.emu.tick(20)
        return sid

    def wanted(self, sid: int) -> bool:
        if not sid:
            return False
        try:
            nat = self.target.evolutions.natdex(sid)
        except Exception:  # noqa: BLE001
            return False
        return nat not in self.target.dex_flags(self.d.state)[0]

    def ball(self):
        balls = self.d.state.bag().get("poke_balls") or {}
        for name in BALL_PREFS:
            if balls.get(name):
                return name
        return next((n for n, q in balls.items() if q), None)

    def wanted_name(self, name: str) -> bool:
        sid = self.d.consts.species.get(
            "SPECIES_" + str(name).replace(" ", "_").replace("-", "_").upper())
        return self.wanted(sid) if sid else False

    def road_policy(self):
        """The policy for battles NOBODY here asked for.

        `goto`, `travel` and `_cross_seam` fight with whatever
        `Driver.battle_policy` holds and no call site is asked
        (trek.py:3148-3162), so without this a dex-new wild met on the way to
        the dive tile is simply knocked out by tactics. Installed once in
        `main`; the frames a policy is handed come from a decision point, so
        the species in them is not the stale first-frame read.
        """

        def decide(frame):
            enemy = frame.get("enemy") or {}
            if not frame.get("wild") or (enemy.get("hp") or 0) <= 0:
                return None
            if not self.wanted_name(enemy.get("species") or ""):
                return None
            pick = self.ball()
            return ("ball", pick) if pick else None

        return decide

    def fight(self):
        d = self.d
        for _ in range(80):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        sid = self.enemy_species_id()
        name = d.names.species(sid) if sid else "?"
        if self.wanted(sid):
            ball = self.ball()
            log.info("[catch] %s is new to the dex -- throwing %s", name, ball)
            if ball is None:
                log.info("[catch] NO BALLS -- fleeing instead of losing it "
                         "to a KO")
                return d.fight(policy=lambda frame: "flee")

            # Throw on turn one and every turn after. Weakening it first is
            # the better play in general and the wrong play here: the only
            # attackers in this party are L100s that one-shot a L30-35
            # RELICANTH, and a dead RELICANTH is a dex entry lost for another
            # nine hundred steps.
            def throw(frame):
                enemy = frame.get("enemy") or {}
                if (enemy.get("hp") or 0) <= 0:
                    return None
                pick = self.ball()
                return ("ball", pick) if pick else "flee"

            return d.fight(policy=throw)
        log.info("[flee] %s", name)
        return d.fight(policy=lambda frame: "flee")


def encounter_tile(d, x: int, y: int) -> bool:
    """Does a step onto (x,y) roll the wild table?

    The engine's own test, not a guess: surfable AND encounter in
    `sTileBitAttributes`, which underwater means seaweed.
    """
    cell = d.nav.cell(d.map_name(), x, y)
    if cell is None or cell.collision:
        return False
    return d.nav.beh.is_water_encounter(cell.behavior)


def find_perch(d):
    """``((x, y), key)`` -- a seaweed cell with a seaweed neighbour.

    Two adjacent encounter tiles are all the machinery this hunt needs: step
    between them forever and every single step rolls the table, with no
    behaviour change to trigger the engine's anti-chaining dice roll and no
    path to plan.
    """
    here = d.map_name()
    try:
        reach = set(d.nav.reachable(here, d.pos(), d.elevation()))
    except Exception as exc:  # noqa: BLE001
        log.info("no reachable set on %s: %s", here, str(exc)[:60])
        return None
    px, py = d.pos()
    best = None
    for (x, y) in reach:
        if not encounter_tile(d, x, y):
            continue
        for key, dx, dy in NEIGHBOURS:
            nb = (x + dx, y + dy)
            if nb in reach and encounter_tile(d, *nb):
                dist = abs(x - px) + abs(y - py)
                if best is None or dist < best[0]:
                    best = (dist, (x, y), key)
                break
    if best is None:
        return None
    log.info("perch %s stepping %s (%d cells away, %d reachable)",
             best[1], best[2], best[0], len(reach))
    return (best[1], best[2])


def churn(d, col, target, deadline) -> bool:
    """Oscillate on the seaweed until RELICANTH is registered.

    The loop is deliberately dumb: two cells, one key, flip on refusal. Every
    clever version of this -- `goto` between patches, `travel` legs, a random
    walk -- spends its budget in the Python planner instead of in the
    emulator, and the emulator is where encounters come from.
    """
    perch = find_perch(d)
    if perch is None:
        log.info("no adjacent seaweed pair reachable from %s", d.pos())
        return False
    (cx, cy), key = perch
    if d.pos() != (cx, cy):
        try:
            if not walk_to(d, cx, cy, budget=240.0):
                log.info("could not reach the perch %s: %s", (cx, cy),
                         d.last_goto_reason)
                return False
        except TravelInterrupted:
            col.fight()
            d.advance_scene(40000)
    steps = fights = 0
    refused = 0
    last_save = time.time()
    while time.time() < deadline:
        if registered(d, target, QUARRY):
            return True
        before = d.pos()
        try:
            d.step_dir(key)
        except TravelInterrupted:
            pass
        except Exception as exc:  # noqa: BLE001
            log.info("step %s raised %s", key, str(exc)[:60])
            break
        if d.pos() != before:
            steps += 1
            refused = 0
            # FLIP EVERY TIME. Both cells of the perch are seaweed, so an
            # unconditional flip means every step lands on an encounter tile
            # AND `prevMetatileBehavior == curMetatileBehavior`, which is the
            # branch that skips `DoGlobalWildEncounterDiceRoll`
            # (wild_encounter.c:480-487). Walking onward instead would
            # eventually step onto MB_NORMAL: a wasted roll and then a
            # discounted one.
            key = OPPOSITE[key]
        if d.state.in_battle() or d.scene_active():
            fights += 1
            col.publish()
            col.fight()
            d.advance_scene(40000)
            unwedge(d)
            if registered(d, target, QUARRY):
                col.save()
                log.info("%s CAUGHT after %d steps and %d encounters",
                         QUARRY, steps, fights)
                return True
            # A won battle leaves the player where it started, but a whiteout
            # does not -- and neither does a scene that moved us. Re-perch
            # rather than step into a wall for the rest of the budget.
            if not encounter_tile(d, *d.pos()):
                perch = find_perch(d)
                if perch is None:
                    return False
                (cx, cy), key = perch
                if d.pos() != (cx, cy) and not walk_to(d, cx, cy):
                    return False
            continue
        if d.pos() == before:
            refused += 1
            key = OPPOSITE[key]
            if refused >= 6:
                log.info("stuck at %s (%s) -- re-perching", d.pos(),
                         d.last_step_reason)
                perch = find_perch(d)
                if perch is None:
                    return False
                (cx, cy), key = perch
                refused = 0
                if d.pos() != (cx, cy) and not walk_to(d, cx, cy):
                    return False
        if time.time() - last_save > 120:
            last_save = time.time()
            log.info("  %d steps, %d encounters, %d balls, at %s", steps,
                     fights, col.balls(), d.pos())
            col.save()
    log.info("out of time: %d steps, %d encounters", steps, fights)
    return registered(d, target, QUARRY)


def hunt(d, col, target, deadline) -> bool:
    while time.time() < deadline:
        if registered(d, target, QUARRY):
            return True
        if d.map_name() != SEAFLOOR and not descend(d):
            return False
        if col.balls() < 1:
            log.info("out of balls")
            return False
        if churn(d, col, target, deadline):
            return True
    return registered(d, target, QUARRY)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/uw-out.state")
    ap.add_argument("--budget", type=float, default=9000.0)
    ap.add_argument("--stage", default="all",
                    choices=("all", "prep", "hunt"))
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "line3" in args.state or "milestone" in args.state:
        log.error("refusing to drive %s -- fork it first", args.state)
        return 2

    stop = time.time() + args.budget
    d = Driver(args.state)
    target = dex_target(d)
    if d.at_title():
        log.info("resuming from the title screen")
        d.resume_from_title()
    unwedge(d)
    log.info("start: %s %s | %s", d.map_name(), d.pos(),
             target.summary(d.state))
    col = Diver(d, feed_name=Path(args.state).stem)
    # ONE HOOK, EVERY CALL SITE. `battle_policy` is what `goto`/`travel`
    # actually consult (trek.py:3161); `encounter_policy` is the Crystal tree's
    # name and has no consumer in this package at all.
    d.battle_policy = col.road_policy()

    if args.stage in ("all", "prep"):
        if not prep(d, target):
            log.error("no diver in the party (can_dive=%s)", d.can_dive())
            return 1
        col.save()
        buy_balls(d, col)
        col.save()

    if args.stage in ("all", "hunt"):
        ok = hunt(d, col, target, stop)
        log.info("%s registered: %s | %s", QUARRY, ok,
                 target.summary(d.state))
        col.save()

    log.info("done: %s | %s=%s", target.summary(d.state), QUARRY,
             registered(d, target, QUARRY))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
