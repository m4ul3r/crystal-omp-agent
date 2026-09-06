#!/usr/bin/env python
"""Catch KOFFING (Fiery Path) and SPOINK (Jagged Pass).

Two species, two neighbouring maps on Mt Chimney's slope, and both unlock a
by-level evolution the grind engine already knows how to run (WEEZING at 35,
GRUMPIG at 32) -- so the only thing worth doing here is standing in the right
grass with balls in the bag.

Everything below `Collector` is reused: its `pace_map` is the walker that
actually generates encounters (hand-stepping does not -- see collect.py:468),
its `fight` routes a dex-new wild to the catcher, and the catcher already
answers "KOFFING is new to the Pokedex" ahead of its own ball reserve
(catching.py:249). What this adds is a stop condition -- pace in short chunks
and quit the map the moment the species' CAUGHT flag flips, instead of burning
a fixed per-map budget -- and an entry routine that does not give up when
`travel` refuses.

Both target maps are grass-tiled in nav's terms (FieryPath: 261 grass cells of
1330; JaggedPass: 34), so the ordinary land pacer works on both; no cave-floor
special case is needed.
"""

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver  # noqa: E402
from collect import Collector  # noqa: E402

log = logging.getLogger("fiery_jagged")

#: map -> the species we are there for.
HUNTS = (("FieryPath", "KOFFING"), ("JaggedPass", "SPOINK"))

#: Keep at least this many balls before starting a map; both targets are
#: common slots, but a dry bag turns a found encounter into a wasted one.
BALL_MIN = 12


def clear_journey(d):
    """Drop a leftover walk deadline.

    `pace_map` sets `Driver._journey_deadline` before every `goto`
    (collect.py:534) and never clears it, and the attribute is PERSISTENT
    rather than per-call: `goto` refuses outright once it is in the past
    ("journey budget spent at (7, 8) heading for (0, 6)"), which is how the
    first run of this script lost Jagged Pass -- it healed at Slateport,
    could not walk out of the Pokemon Centre door, could not Fly (indoors),
    and declared the map unroutable twice from inside the lobby.
    """
    d._journey_deadline = None


def step_outside(d, tries: int = 3) -> bool:
    """Get out of a building, so Fly is available again.

    Indoor map names in this ROM are all `Parent_Something`; the outdoors are
    bare (`LavaridgeTown`, `Route112`). Take the first door that lands on a
    bare name.
    """
    for _ in range(tries):
        here = d.map_name()
        if "_" not in here:
            return True
        try:
            doors = [e for e in d.nav.exits(here)
                     if e.get("dest") and "_" not in e["dest"]]
        except Exception as exc:  # noqa: BLE001
            log.info("   no exits from %s: %s", here, str(exc)[:70])
            return False
        if not doors:
            return False
        clear_journey(d)
        door = doors[0]
        log.info("   stepping outside %s via (%s,%s) -> %s", here,
                 door.get("x"), door.get("y"), door.get("dest"))
        try:
            d.take_warp(int(door["x"]), int(door["y"]))
        except Exception as exc:  # noqa: BLE001
            log.info("   door refused: %s", str(exc)[:70])
            return False
    return "_" not in d.map_name()


def needs_heal(col) -> bool:
    """Heal only when the party cannot fight, not when anything is scratched.

    `Collector.hurt()` trips on ANY member under 40%, and the nurse trip it
    triggered cost NINE MINUTES (Fiery Path -> Slateport's centre and back)
    for a party whose L100 lead one-shots everything on this slope.
    """
    if col.pp_dry():
        return True
    party = [m for m in col.d.state.party() if not getattr(m, "is_egg", False)]
    lead = party[0] if party else None
    if lead is not None and lead.max_hp and lead.hp <= lead.max_hp * 0.4:
        return True
    alive = sum(1 for m in party if m.hp)
    return alive <= 2


class Hunter(Collector):
    """A collector that will not decide a catch off a stale battle frame.

    `battle_frame()["enemy"]` reads gBattleMons[1], which still holds the
    PREVIOUS battle's mon for a while after `state.battle_ready()` goes true
    -- so a plan built on the first readable frame can decline a dex-new
    species because it thinks it is looking at the TORKOAL from a minute ago.
    Two extra gates, both cheap: wait for the ACTION MENU (the engine is only
    there once the battle is fully set up), and require the species read to be
    one this map's encounter table can actually produce.
    """

    def map_species(self) -> set:
        try:
            rows = self.target.wild.for_map(self.d.map_name())
        except Exception:  # noqa: BLE001
            return set()
        out = set()
        for row in rows:
            try:
                out.add(self.d.names.species(row.species))
            except Exception:  # noqa: BLE001
                continue
        return out

    def settled_frame(self):
        d = self.d
        for _ in range(160):
            if d.in_battle() and d.battle.at_action_menu():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        here = self.map_species()
        frame = None
        for _ in range(6):
            try:
                frame = d.battle_frame()
            except Exception as exc:  # noqa: BLE001
                log.debug("battle_frame: %s", str(exc)[:70])
                frame = None
            species = ((frame or {}).get("enemy") or {}).get("species") or ""
            if not here or (species and species in here):
                return frame
            log.info("[catch] enemy reads %r, not in %s's table -- re-reading",
                     species, d.map_name())
            d.emu.tick(30)
        return frame

    def fight(self):
        d = self.d
        frame = self.settled_frame()
        if not d.in_battle():
            return None
        policy = self.base_policy()
        plan = None
        if frame is not None:
            try:
                plan = self.catcher.plan(frame)
            except Exception as exc:  # noqa: BLE001 - never lose a battle here
                log.info("[catch] plan raised: %s", str(exc)[:90])
        if plan:
            log.info("[catch] going for it -- %s", plan.reason)
            policy = self.catcher.policy(plan, inner=policy)
        else:
            enemy = ((frame or {}).get("enemy") or {}).get("species")
            log.info("[catch] declined %s: %s", enemy,
                     getattr(plan, "reason", None) or "no frame to judge")
        return d.fight(policy=policy)


def catch_new_policy(col):
    """A standing battle policy: spend a ball on anything the dex lacks.

    Re-decided EVERY frame rather than once per battle, which is the cheap
    answer to gBattleMons[1] being stale on the first readable frame: a first
    turn judged on the previous battle's mon corrects itself on the next one,
    and the ball still gets thrown before the target is KO'd as long as the
    enemy is above zero.
    """

    def decide(frame):
        inner = col.base_policy()
        try:
            enemy = frame.get("enemy") or {}
            species = enemy.get("species") or ""
            hp = enemy.get("hp") or 0
            if (frame.get("wild") and species and hp > 0
                    and not col.catcher.dex_caught(species)
                    and species in (col.map_species() or {species})):
                ball = col.catcher._pick_ball()
                if ball:
                    return ("ball", ball)
        except Exception as exc:  # noqa: BLE001 - a policy must never raise
            log.debug("catch policy: %s", str(exc)[:70])
        return inner(frame) if inner else None

    return decide


def natdex_of(col, want: str):
    """The national-dex number the CAUGHT flag is keyed by, by name.

    Species ids and dex numbers are different namespaces, and mixing them is
    the documented way to get a silently-always-False comparison here.
    """
    for entry in col.target.achievable:
        try:
            if col.d.names.species(entry.species) == want:
                return entry.natdex
        except Exception:  # noqa: BLE001
            continue
    return None


def is_caught(col, natdex) -> bool:
    caught, _seen = col.target.dex_flags(col.d.state)
    return natdex in caught


def enter(col, name: str, budget: float = 300.0) -> bool:
    """Stand on `name`, trying routing then warps by hand.

    `goto_map` flies to the nearest landing and routes; when that refuses,
    the next thing to disbelieve is nav's collision, which decodes the
    SHIPPED layout and can be stale after a game-clear rewrite -- hence
    `sync_grid` before the second attempt. Last resort is the warp itself:
    walk to a neighbour that lists `name` as a destination and step onto the
    door (standing on a warp does not fire it -- trek.py:1972).
    """
    d = col.d
    clear_journey(d)
    if d.map_name() == name:
        return True
    # FLY IS REFUSED INDOORS, and `goto_map`'s first move is to fly to the
    # nearest landing -- so being stood in a Pokemon Centre lobby silently
    # skipped the hop and then reported "no route to JaggedPass from
    # SlateportCity_PokemonCenter_1F". Walk out of the building first.
    step_outside(d)
    try:
        if col.goto_map(name, budget=budget):
            return True
    except Exception as exc:  # noqa: BLE001
        log.info("   travel to %s raised %s", name, str(exc)[:80])
    log.info("   %s refused (%s); syncing the live grid and retrying",
             name, d.last_goto_reason)
    try:
        changed = d.sync_grid()
        log.info("   sync_grid on %s: %d cells changed", d.map_name(), changed)
    except Exception as exc:  # noqa: BLE001
        log.info("   sync_grid raised %s", str(exc)[:80])
    col._unroutable.discard(name)
    try:
        if col.goto_map(name, budget=budget):
            return True
    except Exception as exc:  # noqa: BLE001
        log.info("   retry raised %s", str(exc)[:80])
    # By hand, through a neighbour's door.
    try:
        neighbours = [e.get("dest") for e in d.nav.exits(name) if e.get("dest")]
    except Exception as exc:  # noqa: BLE001
        log.info("   no exit list for %s: %s", name, str(exc)[:80])
        neighbours = []
    for src in dict.fromkeys(neighbours):
        col._unroutable.discard(src)
        clear_journey(d)
        if d.map_name() != src and not col.goto_map(src, budget=budget):
            log.info("   no route to %s either", src)
            continue
        try:
            d.sync_grid()
        except Exception:  # noqa: BLE001
            pass
        doors = [(int(e["x"]), int(e["y"])) for e in d.nav.exits(src)
                 if e.get("dest") == name]
        log.info("   on %s; %d door(s) into %s: %s", src, len(doors), name,
                 doors)
        for x, y in doors:
            clear_journey(d)
            try:
                d.take_warp(x, y)
            except Exception as exc:  # noqa: BLE001
                log.info("   warp (%d,%d) raised %s", x, y, str(exc)[:80])
            if d.map_name() == name:
                return True
    return d.map_name() == name


def hunt(col, name: str, species: str, deadline: float,
         chunk: float = 75.0) -> bool:
    """Pace `name`'s grass in chunks until `species` is CAUGHT or time is up."""
    d = col.d
    natdex = natdex_of(col, species)
    if natdex is None:
        log.info("!! %s is not in the achievable dex -- skipping %s",
                 species, name)
        return False
    if is_caught(col, natdex):
        log.info("== %s already caught", species)
        return True
    if needs_heal(col):
        log.info("   healing before %s", name)
        col.heal()
        clear_journey(col.d)
    if col.balls() < BALL_MIN:
        log.info("   %d balls -- restocking", col.balls())
        try:
            col.restock_balls()
        except Exception as exc:  # noqa: BLE001
            log.info("   restock raised %s", str(exc)[:80])
    log.info("-> %s for %s (%d balls, dex %d)", name, species, col.balls(),
             col._caught_count())
    col.publish("hunting %s on %s" % (species, name))
    if not enter(col, name):
        log.info("!! could not reach %s (now %s at %s)", name, d.map_name(),
                 d.pos())
        return False
    log.info("   on %s at %s", d.map_name(), d.pos())
    col.save()
    while time.time() < deadline:
        if d.map_name() != name:
            # An encounter can end somewhere else (a whiteout moves the
            # player), and pacing a different map catches a different species.
            log.info("   drifted to %s -- re-entering %s", d.map_name(), name)
            if not enter(col, name, budget=180.0):
                return is_caught(col, natdex)
        col.pace_map(min(deadline, time.time() + chunk), "grass")
        clear_journey(d)
        if is_caught(col, natdex):
            log.info("== CAUGHT %s (dex %d)", species, col._caught_count())
            col.save()
            return True
        if col.balls() < 3:
            log.info("   out of balls mid-hunt; restocking")
            try:
                col.restock_balls()
            except Exception as exc:  # noqa: BLE001
                log.info("   restock raised %s", str(exc)[:80])
        log.info("   still hunting %s (dex %d, %d balls, %ds left)", species,
                 col._caught_count(), col.balls(), int(deadline - time.time()))
    return is_caught(col, natdex)


def verify(state_path: str) -> int:
    """Cold-read a banked save and report the two flags. No driving."""
    from pokeagent import dex as dexmod

    d = Driver(state_path)
    target = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    caught, seen = target.dex_flags(d.state)
    print("state       :", state_path)
    print("map         :", d.map_name(), d.pos())
    print("dex caught  :", len(caught))
    print("dex seen    :", len(seen))
    for _map, species in HUNTS:
        nat = None
        for entry in target.achievable:
            try:
                if d.names.species(entry.species) == species:
                    nat = entry.natdex
                    break
            except Exception:  # noqa: BLE001
                continue
        print("%-10s: natdex %s CAUGHT=%s SEEN=%s"
              % (species, nat, nat in caught, nat in seen))
    party = [(m.nickname, getattr(m, "species_name", ""), m.level)
             for m in d.state.party()]
    print("party       :", party)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/fj.state",
                    help="working state (never line3/milestone)")
    ap.add_argument("--fork-from", default=None,
                    help="copy this state over --state before starting")
    ap.add_argument("--out", default="saves/fj-out.state")
    ap.add_argument("--minutes", type=float, default=45.0)
    ap.add_argument("--feed", default="fj")
    ap.add_argument("--verify", default=None,
                    help="cold-read a state and exit")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    if a.verify:
        return verify(a.verify)
    if "line3" in a.state or "milestone" in a.state:
        raise SystemExit("refusing to drive %s" % a.state)
    if a.fork_from:
        shutil.copyfile(a.fork_from, a.state)
        meta = Path(a.fork_from + ".meta")
        if meta.exists():
            shutil.copyfile(meta, a.state + ".meta")
        log.info("forked %s -> %s", a.fork_from, a.state)

    d = Driver(a.state)
    d.advance_scene(40000)
    col = Hunter(d, feed_name=a.feed or None)
    # THE ONLY HOOK THAT WORKS. `encounter_policy` is Crystal's API and has no
    # consumer here; `Driver.battle_policy` (trek.py:3159) is what an
    # unattended `fight()` reads when no policy is passed -- so a battle that
    # starts somewhere this script did not route (a travel leg, a step out of
    # a door) still throws at something the dex has never recorded.
    d.battle_policy = catch_new_policy(col)
    start = col._caught_count()
    log.info("start: %s at %s, dex %d caught, %d balls, %d money",
             d.map_name(), d.pos(), start, col.balls(), d.state.money())
    stop = time.time() + a.minutes * 60.0
    done = {}
    for name, species in HUNTS:
        left = stop - time.time()
        if left <= 30:
            log.info("!! out of budget before %s", species)
            done[species] = False
            continue
        # Split what is left between the maps still owed, so a stubborn first
        # map cannot eat the second one's whole turn.
        owed = sum(1 for _m, s in HUNTS if s not in done)
        share = left if owed <= 1 else max(left * 0.55, 240.0)
        try:
            done[species] = hunt(col, name, species,
                                 min(stop, time.time() + share))
        except Exception as exc:  # noqa: BLE001 - one map must not end the run
            log.exception("hunt %s raised %s", name, exc)
            done[species] = False
        col.save()
    # Bank it, whatever happened.
    for _ in range(8):
        if not d.scene_active():
            break
        d.emu.run_sequence("B:4 .:30")
    d.advance_scene(40000)
    d.save(a.state)
    d.save(a.out)
    log.info("banked %s and %s", a.state, a.out)
    log.info("result: dex %d -> %d; %s", start, col._caught_count(),
             ", ".join("%s=%s" % kv for kv in done.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
