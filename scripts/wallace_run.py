#!/usr/bin/env python
"""Badge 8: into Sootopolis Gym and beat Wallace.

Two things this has to handle that the generic chain did not:

* The gym door (31,32) is a door METATILE -- collision 1 -- so nothing can ever
  stand on it. You stand on (31,33) and walk UP. `reach_cell(31,32)` simply
  exhausts.
* The escort cutscene leaves STEVEN standing on (31,33), the only approach.
  Object events return to their template cells on a map reload, so stepping
  into the Pokemon Center and back out clears him.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("wallace")

CITY = "SootopolisCity"
GYM = "SootopolisCity_Gym_1F"
DOOR, APPROACH = (31, 32), (31, 33)
CENTER = (43, 31)


def clear_doorway(d) -> bool:
    """Reload the map so Steven goes back where he belongs."""
    occupied = {(o["x"], o["y"]) for o in d.live_npcs() if not o.get("player")}
    if APPROACH not in occupied:
        return True
    # TALK TO HIM. Steven is MOVEMENT_TYPE_FACE_RIGHT and a map reload puts
    # him straight back -- but his script ("we owe it all to you") ends by
    # walking him off, which is how the game intends the doorway to clear.
    log.info("someone is standing on %s; talking to them", APPROACH)
    d.emu.run_sequence("LEFT:4 .:20")
    d.emu.run_sequence("A:4 .:60")
    d.advance_scene(90000)
    d.close_menus()
    occupied = {(o["x"], o["y"]) for o in d.live_npcs() if not o.get("player")}
    if APPROACH not in occupied:
        return True
    try:
        d.goto(CENTER[0], CENTER[1] + 1, on_battle="fight")
    except Exception:  # noqa: BLE001
        pass
    if not d.take_warp(*CENTER):
        return False
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    return d.map_name() == CITY


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s badges %d", d.map_name(), d.pos(),
             len(d.state.badges()))

    for attempt in range(4):
        if d.state.flag("FLAG_BADGE08_GET"):
            break
        if d.map_name() == CITY:
            # APPROACH FIRST, then look. Object events only load near the
            # camera, so checking the doorway from across the city reports it
            # empty every time -- Steven is simply not loaded yet.
            try:
                d.goto(32, 33, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if not clear_doorway(d):
                log.info("could not clear the doorway")
                return 1
            try:
                d.goto(*APPROACH, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if d.pos() != APPROACH:
                log.info("attempt %d: could not stand on %s (at %s)", attempt,
                         APPROACH, d.pos())
                continue
            d.emu.run_sequence("UP:24 .:40")
            d.advance_scene(60000)
            log.info("entered: %s %s", d.map_name(), d.pos())

        if d.map_name() == "SootopolisCity_Gym_B1F":
            d.reach_cell(11, 22, map_name="SootopolisCity_Gym_B1F",
                         on_battle="fight")
            d.take_warp(11, 22)

        if d.map_name() != GYM:
            continue

        # Wallace stands at (8,2); the ice floor between is the puzzle.
        try:
            d.reach_cell(8, 3, map_name=GYM, on_battle="fight")
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
        if d.pos() == (8, 3):
            d.emu.run_sequence("U:4 .:20")
            d.emu.run_sequence("A:4 .:60")
            d.advance_scene(90000)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            for _ in range(5):
                d.advance_scene(150000)
        log.info("attempt %d: badge08=%s at %s %s", attempt,
                 d.state.flag("FLAG_BADGE08_GET"), d.map_name(), d.pos())
        if a.out:
            d.save(a.out)

    log.info("RESULT badge08=%s badges=%d", d.state.flag("FLAG_BADGE08_GET"),
             len(d.state.badges()))
    if a.out:
        d.save(a.out)
    return 0 if d.state.flag("FLAG_BADGE08_GET") else 1


if __name__ == "__main__":
    raise SystemExit(main())
