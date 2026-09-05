#!/usr/bin/env python
"""Sweep the two underwater maps that actually hold wild Pokemon.

`Underwater1` (dive on Route 124) and `Underwater2` (dive on Route 126) are the
ONLY underwater maps in the game with encounter tables
(`pret/src/data/wild_encounters.json:18846-18916`): Clamperl 65%, Chinchou 30%,
Relicanth 5%, at a 4% encounter rate. Underwater3 and Underwater4 have no table
at all, so diving on Routes 127/128 is transit and nothing else.

Relicanth matters beyond the dex entry -- it is half the Sealed Chamber key
(`src/braille_puzzles.c:59-70`, Relicanth first slot, Wailord last).

The collector cannot reach any of this: it plans over walkable routes and has
no idea how to dive.
"""
import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent import nav as nav_mod  # noqa: E402
from pokeagent.catching import Catcher  # noqa: E402

log = logging.getLogger("dive")


def go(d, dest, tries=6) -> bool:
    if d.map_name() == dest:
        return True
    for _ in range(tries):
        try:
            if d.travel(dest, on_battle="fight"):
                return True
        except TravelInterrupted:
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        except Exception as exc:  # noqa: BLE001
            log.info("  travel %s: %s", dest, str(exc)[:70])
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        if d.map_name() == dest:
            return True
    return d.map_name() == dest


def dive_here(d, tries=14) -> bool:
    if d.underwater():
        return True
    here = d.map_name()
    grid = d.nav.grid(here)
    spots = [(x, y) for y, row in enumerate(grid) for x, c in enumerate(row)
             if c is not None and c.behavior in nav_mod.DIVEABLE]
    px, py = d.pos()
    spots.sort(key=lambda p: abs(p[0] - px) + abs(p[1] - py))
    for spot in spots[:tries]:
        try:
            if not d.goto(*spot, on_battle="fight"):
                continue
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            continue
        if d.dive():
            log.info("dived at %s -> %s", spot, d.map_name())
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=40.0)
    ap.add_argument("--surface", default="Route124")
    ap.add_argument("--field", default="Underwater1")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    rng = random.Random(3)
    deadline = time.time() + a.minutes * 60
    from collect import Collector
    collector = Collector(d, feed_name=None)   # the Driver already publishes
    catcher = collector.catcher
    caught, battles = [], 0
    log.info("START %s %s", d.map_name(), d.pos())

    if not d.underwater():
        if not go(d, a.surface):
            log.info("could not reach %s", a.surface)
            return 1
        if not dive_here(d):
            log.info("could not dive on %s", a.surface)
            return 1

    # Sootopolis is a crater: its only exit is a dive, and the basin under it
    # (`Underwater_SootopolisCity`) has no encounter table. Swim to the field
    # that does.
    if a.field and d.map_name() != a.field:
        if not go(d, a.field):
            log.info("could not swim to %s (in %s)", a.field, d.map_name())
            return 1

    home = d.map_name()
    log.info("sweeping %s for %.0f minutes", home, a.minutes)
    while time.time() < deadline:
        if d.in_battle():
            battles += 1
            before = {m.species for m in d.state.party()}
            frame = d.battle_frame()
            policy = None
            try:
                plan = catcher.plan(frame) if frame else None
                if plan is not None and getattr(plan, "wanted", True):
                    log.info("[catch] %s", getattr(plan, "reason", plan))
                    policy = catcher.policy(plan)
            except Exception as exc:  # noqa: BLE001
                log.info("[catch] plan raised: %s", str(exc)[:80])
            d.fight(policy=policy or Driver.damage_first)
            d.advance_scene(40000)
            after = {m.species for m in d.state.party()}
            new = after - before
            if new:
                caught.extend(new)
                log.info("CAUGHT %s (party now %d)", new, len(d.state.party()))
                if a.out:
                    d.save(a.out)
            continue
        d.close_menus()
        if d.map_name() != home:
            if not dive_here(d) and not go(d, a.surface):
                break
            continue
        for _ in range(8):
            if d.in_battle():
                break
            d.step_dir(rng.choice("UDLR"))
            d.settle(12)

    log.info("DONE %d battles, caught %s", battles, caught)
    if a.out:
        d.save(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
