#!/usr/bin/env python
"""Catch CACNEA and TRAPINCH on Route 111.

Both live in Route 111's LAND table, and the whole map shares one wildlife
header -- `t.wild.for_map("Route111")` reads twelve land slots, SANDSHREW x3,
TRAPINCH x3, CACNEA x2 and 318 x4, and there is no second sub-area to find.
The desert sand itself decodes as `kind == "grass"` because
`MetatileBehavior_IsLandWildEncounter` is what `Behaviors.kind` asks
(behaviors.py:155), so `Collector.pace_map(terrain="grass")` paces sand and
grass alike -- 690 cells on this map, y 37..104.

Two things about the desert gate, both already true on the canonical save and
both checked here rather than assumed:

* the GO-GOGGLES are in the bag, so `nav.blocked["Route111"]` comes back EMPTY
  (`gates.required_item` reads `checkitem ITEM_GO_GOGGLES` and answers it from
  the bag -- gates.py:221-253). Without them the same ten entrance cells are
  impassable and the route is severed at y=61; errands.py:276-283 clears them
  by hand for exactly that reason. This script clears them too, but only if
  something put them back.

* sandstorm is weather, not a wall. It costs the wild 1/16 HP a turn, which is
  the only reason the throw loop below is bounded in practice.

WHY THE POLICY THROWS ON TURN ONE. `Catcher.policy` weakens first and throws
under 34% HP (catching.py:364), which is the right rule for a balanced party
and unusable with this one: the lead is a LEVEL 100 Pelipper and every mon in
the party one-shots a level-20 desert wild, so `_would_ko` fires on turn one
anyway and the "weakening" phase cannot happen. Rather than gamble on the
damage estimate being right, this never attacks at all: it throws an ULTRA
BALL every single turn. `Battle.play` supports that directly -- a thrown ball
is measured by the BAG, not by an HP bar (battle.py:1936-1952), so repeated
throws are progress and never retired as a dead action.

That trades catch rate for safety, so it is paid for with balls: the run tops
the bag up to 91 ULTRA BALLs first (money on this save is 999,999 and
Mossdeep's shelf stocks them at 1200).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import Collector  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("desert111")

#: What this trip is for, by dex NAME (the battle frame reports names).
TARGETS = ("CACNEA", "TRAPINCH")

#: Balls to hold before hunting. A bag slot caps at 99; `Mart.buy` can only
#: raise the quantity box `MAX_PRESSES` = 40 times per call (mart.py:38, 209),
#: so this is reached in two purchases.
BALL_TARGET = 91

#: Marts that stock ULTRA BALL, nearest-useful first. Mossdeep's shelf is
#: ULTRA (1200) / NET (1000) / DIVE (1000) -- collect.py:710-714.
ULTRA_MARTS = ("MossdeepCity_Mart", "SootopolisCity_Mart", "LilycoveCity_Mart")

#: Balls we will never spend: one-of-a-kind, and the save is shared.
NEVER_THROW = {"MASTER BALL", "SAFARI BALL"}

#: Preference order for a throw.
BALL_ORDER = ("ULTRA BALL", "GREAT BALL", "NET BALL", "POKE BALL",
              "TIMER BALL", "REPEAT BALL", "NEST BALL", "DIVE BALL",
              "LUXURY BALL", "PREMIER BALL")


class DesertCollector(Collector):
    """`Collector` that throws instead of fighting whenever the dex gains."""

    def ball(self):
        """The best throwable ball in the bag, or None."""
        try:
            have = {n.upper(): q
                    for n, q in (self.d.state.bag().get("poke_balls") or {}).items()
                    if isinstance(q, int) and q > 0}
        except Exception:  # noqa: BLE001 - an unreadable bag is not a decision
            return None
        for name in BALL_ORDER:
            if name in have:
                return name
        for name in sorted(have):
            if name not in NEVER_THROW:
                return name
        return None

    def ultras(self) -> int:
        try:
            return int((self.d.state.bag().get("poke_balls") or {})
                       .get("ULTRA BALL", 0))
        except Exception:  # noqa: BLE001
            return 0

    def wanted(self, species: str) -> bool:
        """Is this wild worth a ball? Any species the dex lacks is."""
        if not species:
            return False
        try:
            return not self.catcher.dex_caught(species)
        except Exception as exc:  # noqa: BLE001 - err toward throwing
            log.info("[d111] dex_caught(%s) raised %s -- throwing anyway",
                     species, str(exc)[:60])
            return True

    def policy(self):
        """Decide EVERY TURN, from the action-menu frame.

        NOT once up front. `Battle.frame()["enemy"]` is `gBattleMons[1]`, and
        right after `state.battle_ready()` it can still hold the PREVIOUS
        battle's mon -- a decision taken off that first read will KO a
        dex-new species believing it is something already registered. This
        was reported on this run by two other agents (two SPHEAL and a
        NOSEPASS lost to it). `Battle.play` only calls the policy once
        `tactics.outlook()` is non-None AND the action menu owns input
        (battle.py:1842-1871), which is the freshest read the harness has, and
        asking there costs one dex-flag lookup a turn.

        Throw at anything the dex lacks; knock out everything else. Never
        weaken first: the lead is a level-100 Pelipper and `Catcher.policy`'s
        "weaken to 34% then throw" (catching.py:364) cannot happen when every
        move one-shots the target.
        """
        def decide(frame):
            enemy = frame.get("enemy") or {}
            species = (enemy.get("species") or "").strip()
            hp = enemy.get("hp") or 0
            if not frame.get("wild") or hp <= 0:
                return Driver.damage_first(frame)
            if species and self.wanted(species):
                ball = self.ball()
                if ball is None:
                    log.info("[d111] no ball to throw at %s", species)
                    return Driver.damage_first(frame)
                if species != self._announced:
                    self._announced = species
                    log.info("[d111] THROWING at %s (%d ULTRA BALLs)",
                             species, self.ultras())
                return ("ball", ball)
            if species and species != self._announced:
                self._announced = species
                log.info("[d111] knocking out %s (already in the dex)",
                         species)
            return Driver.damage_first(frame)
        return decide

    #: Last species this run logged a decision about, so the per-turn policy
    #: does not print the same line every turn.
    _announced = ""

    def fight(self):
        d = self.d
        for _ in range(80):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        self._announced = ""
        return d.fight(policy=self.policy())

    def pace_map(self, deadline, terrain: str = "grass") -> int:
        """`Collector.pace_map` with the battle check FIRST.

        A LIVE BATTLE IS NOT A SCENE TO ADVANCE. The base version tests
        `d.scene_active()` before anything else (collect.py:523-526) and
        answers a true reading with `advance_scene(40000)` -- and
        `advance_scene` now correctly REFUSES while a battle is live, so the
        pair spins: the encounter never reaches `fight()`, `stalled` climbs to
        six, `pace_map` breaks, the hunt loop calls it again and the whole
        thing repeats. Measured on this run, from the published feed: a wild
        SANDSHREW L24 sat at the FIGHT/BAG/POKeMON/RUN menu for six minutes
        while the log printed
        "pacing stalled on Route111 (journey budget spent at (22, 60) heading
        for (32, 102))" and "not saving: a script still owns input" over and
        over, several times a second, without a single ball thrown.

        `TravelInterrupted` is not enough on its own either: the battle that
        wedged this one had already begun when `goto` RETURNED FALSE, so
        nothing raised. Asking `in_battle()` at the top of every iteration
        catches both shapes.
        """
        d = self.d
        got = 0
        cells = self.terrain_cells(terrain)
        if not cells:
            log.info("   no reachable %s on %s", terrain, d.map_name())
            return 0
        log.info("   %d reachable %s cells, nearest %s", len(cells), terrain,
                 cells[0])
        stalled = 0
        i = 0
        while time.time() < deadline:
            if d.in_battle():
                before = self._caught_count()
                self.publish()
                self.fight()
                d.advance_scene(20_000)
                stalled = 0
                if self._caught_count() > before:
                    got += 1
                    self.save()
                continue
            if stalled >= 8:
                log.info("   pacing stalled on %s (%s)", d.map_name(),
                         d.last_goto_reason)
                break
            if self.watch.stalled:
                log.info("   abandoning %s: %s", d.map_name(),
                         self.watch.detail)
                self.watch.clear()
                break
            if d.scene_active():
                d.advance_scene(40_000)
                stalled += 1
                continue
            i += 1
            target = cells[(i * 7) % len(cells)]
            if target == d.pos():
                continue
            try:
                d._journey_deadline = min(deadline, time.time() + 60.0)
                if d.goto(*target, on_battle="raise"):
                    stalled = 0
                else:
                    stalled += 1
            except TravelInterrupted:
                continue          # the in_battle branch above plays it
            except Exception as exc:  # noqa: BLE001
                log.debug("pace: %s", str(exc)[:70])
                stalled += 1
            finally:
                # `_journey_deadline` is PERSISTENT, not per-call: left
                # expired it makes every later `take_warp`/`goto` refuse its
                # approach cells, so the walk out of here (a Centre, a mart,
                # the next map) fails for a reason that has nothing to do
                # with the map. Confirmed on this run by a sibling agent.
                d._journey_deadline = None
        d._journey_deadline = None
        return got


# ------------------------------------------------------------------ shopping

def buy_ultras(c, want=BALL_TARGET) -> int:
    d = c.d
    if c.ultras() >= want:
        log.info("balls: already holding %d ULTRA BALLs", c.ultras())
        return c.ultras()
    for mart in ULTRA_MARTS:
        # FLY TO THE TOWN FIRST. `Collector.goto_map` probes with
        # `nav.route_legs`, which cannot plan INTO an indoor map from another
        # region -- collect.py:684-688 hit exactly this and answered None for
        # all eleven marts. Landing in the city makes the mart a one-warp hop.
        city = mart.rsplit("_Mart", 1)[0]
        if d.map_name() != city:
            try:
                if not d.fly_to(city):
                    log.info("balls: could not fly to %s", city)
            except Exception as exc:  # noqa: BLE001
                log.info("balls: fly to %s: %s", city, str(exc)[:70])
        c._unroutable.discard(mart)
        if not c.goto_map(mart, budget=300.0):
            log.info("balls: could not reach %s from %s (%s)", mart,
                     d.map_name(), d.last_goto_reason)
            continue
        cell = c.clerk_cell(mart)
        if cell is None:
            log.info("balls: no clerk on %s", mart)
            continue
        try:
            d.talk_to(*cell)
        except Exception as exc:  # noqa: BLE001
            log.info("balls: clerk on %s: %s", mart, str(exc)[:70])
            continue
        for _ in range(8):
            if c.mart.is_open():
                break
            d.emu.run_sequence("A:4 .:30")
        if not c.mart.is_open():
            log.info("balls: %s never opened a shop", mart)
            d.advance_scene(20_000)
            continue
        shelf = {r["name"].upper() for r in c.mart.items()}
        if "ULTRA BALL" not in shelf:
            log.info("balls: %s does not stock ULTRA BALL (%s)", mart,
                     sorted(shelf)[:6])
            c.mart.leave()
            continue
        while c.ultras() < want:
            before = c.ultras()
            if not c.mart.buy("ULTRA BALL", min(40, want - before)):
                log.info("balls: buy failed -- %s", c.mart.last_reason)
                break
            if c.ultras() <= before:
                break
        c.mart.leave()
        d.advance_scene(20_000)
        log.info("balls: holding %d ULTRA BALLs after %s", c.ultras(), mart)
        if c.ultras() > 0:
            return c.ultras()
    return c.ultras()


# ------------------------------------------------------------------ the hunt

def caught_names(c) -> set:
    """The dex NAMES this save has registered as CAUGHT, upper-cased."""
    caught, _seen = c.target.dex_flags(c.d.state)
    return {e.name.strip().upper()
            for e in c.target.entries
            if getattr(e, "natdex", None) in caught}


def still_missing(c) -> list:
    have = caught_names(c)
    return [n for n in TARGETS if n not in have]


def open_the_desert(c):
    """Answer the GO-GOGGLES gate if nav has it shut."""
    d = c.d
    if "GO-GOGGLES" not in (d.state.bag().get("key_items") or {}):
        log.warning("no GO-GOGGLES: the desert entrances shove the player "
                    "back (gates.py:188-191)")
        return
    gate = d.nav.blocked.get("Route111")
    if gate:
        log.info("clearing %d Go-Goggles gate cells on Route111", len(gate))
        d.nav.blocked["Route111"] = set()
        try:
            d.nav._reach_cache.clear()
        except Exception:  # noqa: BLE001
            pass


#: The Centre next door to Route 111.
HEAL_CITY = "MauvilleCity"
HEAL_CENTER = "MauvilleCity_PokemonCenter_1F"


def hurt(d) -> bool:
    """Does anything in the party actually need a Centre?"""
    try:
        return any(m.hp < m.max_hp for m in d.state.party())
    except Exception:  # noqa: BLE001
        return False


def heal(c, force=False) -> bool:
    """Heal at Mauville, the Centre next door to Route 111.

    NOT `Driver.heal_at_nearest_center`. That method runs one
    `nav.route_legs(here, cell, centre, max_hops=12)` for EVERY
    `*_PokemonCenter_1F` in the 394-map index, and from inside a building
    those are the pathological pure-Python fills `collect.py:326-338`
    measured: launched from `MossdeepCity_Mart` it blocked this process for
    over 150 seconds with the emulator advancing ZERO frames, and the
    collector's own watchdog printed the stack --
    "wedged at MossdeepCity_Mart (3,3) for 150s ... nav.py:1063 in
    exit_landing / nav.py:1148 in route_legs / trek.py:3005 in
    heal_at_nearest_center". Naming the Centre skips the search entirely.
    """
    d = c.d
    if not force and not hurt(d):
        log.info("heal: party is at full HP, skipping the Centre")
        return True
    if d.map_name() != HEAL_CENTER:
        if not d.map_name().startswith(HEAL_CITY):
            try:
                d.fly_to(HEAL_CITY)
            except Exception as exc:  # noqa: BLE001
                log.info("heal: fly to %s: %s", HEAL_CITY, str(exc)[:70])
        c._unroutable.discard(HEAL_CENTER)
        if not c.goto_map(HEAL_CENTER, budget=240.0):
            log.info("heal: could not reach %s (%s)", HEAL_CENTER,
                     d.last_goto_reason)
            return False
    ok = bool(d.heal())
    log.info("heal: %s at %s", "done" if ok else "refused", d.map_name())
    return ok


def hunt(c, deadline) -> int:
    d = c.d
    got = 0
    while time.time() < deadline:
        # A battle left live by anything above owns input: nothing else in
        # this loop can make progress until it is played out.
        if d.in_battle():
            c.fight()
            d.advance_scene(20_000)
            continue
        left = still_missing(c)
        if not left:
            log.info("both targets registered -- stopping")
            break
        log.info("still missing %s; %d ULTRA BALLs, %ds left", left,
                 c.ultras(), int(deadline - time.time()))
        if c.ultras() <= 2 and c.ball() is None:
            log.info("no balls left to throw")
            break
        if d.map_name() != "Route111":
            open_the_desert(c)
            if not c.goto_map("Route111", budget=420.0):
                log.info("could not reach Route111 (%s)", d.last_goto_reason)
                time.sleep(1)
                continue
        # HEAL only when it matters. A level-100 lead does not need a Centre
        # every lap, and each trip costs a fly plus a walk back.
        if hurt(d):
            heal(c)
            continue
        chunk = min(300.0, max(30.0, deadline - time.time()))
        c.watch.clear()
        got += c.pace_map(time.time() + chunk, terrain="grass")
        c.save()
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/d111.state")
    ap.add_argument("--out", default="saves/d111-out.state")
    ap.add_argument("--minutes", type=float, default=150.0)
    ap.add_argument("--feed", default="d111")
    ap.add_argument("--no-shop", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "line3" in args.state or "milestone" in args.state:
        raise SystemExit(f"refusing to drive {args.state} in place -- fork it")

    d = Driver(args.state)
    c = DesertCollector(d, feed_name=args.feed)
    # THE ONE HOOK THAT WORKS. `encounter_policy` is Crystal's API and has no
    # consumer in this package; `Driver.battle_policy` is what every internal
    # `fight()` -- the ones `goto`/`travel`/`_cross_seam` call with no policy
    # of their own (trek.py:3159) -- actually reads. Without it a wild met
    # mid-walk is knocked out by tactics before the catch decision is ever
    # asked for.
    d.battle_policy = c.policy()
    before = caught_names(c)
    log.info("start: %s %s, dex %d caught, targets missing %s",
             d.map_name(), d.pos(), len(before), still_missing(c))
    log.info("summary: %s", c.target.summary(d.state))

    if not args.no_shop:
        buy_ultras(c)

    c.save()
    heal(c)

    open_the_desert(c)
    if not c.goto_map("Route111", budget=420.0):
        log.warning("could not reach Route111: %s", d.last_goto_reason)
    log.info("on %s at %s", d.map_name(), d.pos())

    got = hunt(c, time.time() + args.minutes * 60.0)

    c.save()
    after = caught_names(c)
    for _ in range(8):
        if not d.scene_active():
            break
        d.emu.run_sequence("B:4 .:30")
    if d.scene_active():
        d.advance_scene(40_000)
    d.save(args.out)
    log.info("done: %d new dex entries this run; targets %s; caught %d -> %d",
             got, still_missing(c), len(before), len(after))
    log.info("gained: %s", sorted(after - before))
    return 0 if not still_missing(c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
