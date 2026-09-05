#!/usr/bin/env python
"""Walk an egg until it hatches, somewhere nothing can interrupt.

The wild sweeper does not do this on its own: it FLIES between maps and Fly
covers no ground, so fifteen minutes of sweeping moved the egg's counter
barely at all.

The counter is `eggCycleStepsRemaining`, a u8 incremented on EVERY overworld
step by `ShouldEggHatch()` (pret/src/field_control_avatar.c:583). Because it
is a u8 it wraps every 256 steps, and each wrap decrements the egg's
friendship from `eggCycles` (10 for Wynaut) until it hits zero -- so
2561-2816 steps, and it works on any map
(pret/src/daycare.c:721-733, :758-775). No Flame Body halving in R/S; that
is a later-generation feature and `_ShouldEggHatch` has no ability check.

Walk map: `Route117_PokemonDayCare`. It is MAP_TYPE_INDOOR, 12x9, and absent
from `wild_encounters.json`, so there is nothing to interrupt the walk -- no
encounters, no trainers, no scene scripts.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402

log = logging.getLogger("hatch")

WALK_MAP = "Route117_PokemonDayCare"
#: Route117's door into the building (Route117/map.json:302-309).
DOOR = (51, 5)


def has_egg(d) -> bool:
    try:
        return any(m.is_egg for m in d.state.party())
    except Exception:  # noqa: BLE001
        return False


def reach_walk_map(d) -> bool:
    if d.map_name() == WALK_MAP:
        return True
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        for town in ("MauvilleCity", "VerdanturfTown"):
            if d.fly_to(town):
                break
    except Exception as exc:  # noqa: BLE001
        log.debug("fly: %s", str(exc)[:80])
    for _ in range(3):
        if d.map_name() == "Route117":
            break
        try:
            d.travel("Route117", on_battle="fight", budget_s=200)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("travel Route117: %s", str(exc)[:100])
            break
    if d.map_name() == "Route117" and not d.take_warp(*DOOR):
        log.info("could not enter the Day Care (%s)", d.last_warp_reason)
    return d.map_name() == WALK_MAP


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=45.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())
    if not has_egg(d):
        log.info("no egg in the party -- nothing to hatch")
        return 0

    if not reach_walk_map(d):
        log.info("could not reach %s (at %s) -- walking here instead",
                 WALK_MAP, d.map_name())
    log.info("walking on %s %s", d.map_name(), d.pos())

    deadline = time.time() + a.minutes * 60.0
    steps = 0
    # Back and forth on one axis. Each held press is one tile; the room is
    # 12 wide so a long hold just bounces off the wall, which still counts
    # as a step attempt but NOT as a step -- so alternate deliberately.
    while time.time() < deadline:
        for mv in ("LEFT", "RIGHT"):
            d.emu.run_sequence(f"{mv}:10 .:2")
            steps += 1
        if steps % 200 == 0:
            d.advance_scene(20_000)
            if not has_egg(d):
                break
            log.info("  %d steps; egg still cooking at %s", steps, d.pos())
    d.advance_scene(40_000)

    # ANSWER THE NICKNAME PROMPT. A hatch opens
    # "Would you like to nickname the newly hatched X?" and the script sits
    # there forever until it is answered -- with `Task_NamingScreenMain`
    # already loaded, so `scene_active` stays True and EVERY later input is
    # eaten. That wedged the canonical save at dex 87: 14 B presses,
    # `close_menus` and `resolve_choice('NO')` all failed, and `take_warp`
    # hung for 17 minutes. The wild sweeper then sat in the Day Care doing
    # nothing, which is why the dex stopped moving.
    #
    # RECOVERY (and prevention) is to COMPLETE the keyboard, not to dodge it:
    # `NamingScreen.accept()` confirms whatever is typed and hands control
    # back. Verified: scene True -> False, normal overworld tasks, save clean.
    if d.scene_active():
        try:
            from pokeagent.naming import NamingScreen

            ns = NamingScreen(d.emu, d.state)
            if ns.is_open() or "nickname" in (d.state.message() or "").lower():
                d.emu.run_sequence("A:4 .:60")
                d.advance_scene(20_000)
                log.info("naming screen: accepted %r", ns.accept())
                d.advance_scene(60_000)
        except Exception as exc:  # noqa: BLE001
            log.info("naming screen: %s", str(exc)[:110])
    if d.scene_active():
        log.info("REFUSING TO BANK: scene still active (tasks %s)",
                 d.state.tasks())
        return 1

    hatched = not has_egg(d)
    log.info("RESULT hatched=%s after ~%d steps | party %s", hatched, steps,
             [(d.names.species(m.species), m.level,
               "EGG" if m.is_egg else "") for m in d.state.party()])
    d.save(a.state)
    return 0 if hatched else 1


if __name__ == "__main__":
    raise SystemExit(main())
