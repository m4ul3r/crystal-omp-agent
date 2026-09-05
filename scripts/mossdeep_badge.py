#!/usr/bin/env python
"""Win badge 7 by SEARCHING Mossdeep's gym floor, not by planning across it.

The floor is 173 `MB_SLIDE_*` arrows (0x44 EAST, 0x45 WEST, 0x46 NORTH,
0x47 SOUTH) whose directions the four `FLAG_MOSSDEEP_GYM_SWITCH_*` re-point.
A step onto one does not end where it was aimed: measured live, LEFT from
(2,22) landed the player at (8,17), six columns across and five rows up. nav
plans across them as ordinary floor, reports the leaders reachable over 394
cells, and every walk "arrives" somewhere else -- which is why the play loop
logged "challenging TateAndLiza at (8,3)" forever without moving.

So the emulator is the transition function, exactly as for the rotating gates
and the warp mazes. A node is (map, position, switch flags): the map because a
gym door means the same coordinates exist on two maps, and the flags because
the same tile is a different place once a switch has flipped.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import Collector  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("badge7")

GYM = "MossdeepCity_Gym"
#: Tate stands at (8,3) and Liza at (9,3); both run the same script, and (8,4)
#: is the tile below Tate (pret/data/maps/MossdeepCity_Gym/map.json).
APPROACH = (8, 4)
LEADER = (8, 3)


def in_gym(d, collector, tries=3) -> bool:
    for _ in range(tries):
        if d.map_name() == GYM:
            return True
        try:
            d.travel(GYM, on_battle="fight", budget_s=240)
        except Exception as exc:  # noqa: BLE001 - a battle on the way
            log.info("travel: %s", str(exc)[:80])
    return d.map_name() == GYM


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--budget", type=float, default=1800.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40000)
    c = Collector(d, feed_name=None)

    if c.hurt():
        log.info("healing first")
        c.heal()
    if not in_gym(d, c):
        log.info("FAIL: not in the gym (at %s)", d.map_name())
        return 1
    log.info("in the gym at %s | switches %s", d.pos(), d.switch_signature())

    #: The four switches, from the map's bg_events. Pressing one re-points a
    #: block of arrows, which is the only way the room's shape changes.
    SWITCHES = [(5, 24), (17, 15), (2, 7), (8, 10)]
    #: BELOW FIRST. A Gen-3 `bg_event` sign is read from the tile beneath it
    #: while facing up; the other sides are usually solid or simply do not
    #: register. The search reached (1,7) -- immediately west of switch (2,7)
    #: -- pressed A three times and the flag never moved, which is what trying
    #: the wrong side looks like.
    NEIGHBOURS = ((0, 1), (-1, 0), (1, 0), (0, -1))
    FACE = {(0, 1): "U", (0, -1): "D", (1, 0): "L", (-1, 0): "R"}

    def search_to(cell, nodes, budget):
        """Stand on `cell` using the emulator as the transition function."""
        if d.pos() == cell:
            return True
        return d.solve_gate_maze(
            *cell, on_battle="fight", signature=d.switch_signature,
            require_signature=False, max_nodes=nodes, budget_s=budget,
            extra_moves=("A",),
        ) and d.map_name() == GYM and d.pos() == cell

    def press_switch(sw, nodes=1800, budget=150) -> bool:
        """Reach a switch THROUGH the slides, then press it.

        `talk_to` cannot do this: it routes with nav, and nav has no model of a
        slide floor -- which is why (2,7) and (8,10) were never pressed by any
        plan, in any switch configuration. The search can, because it asks the
        game. From an adjacent tile, facing the sign and pressing A is the
        press, and the switch flags say whether it landed.
        """
        before = d.switch_signature()
        for dx, dy in NEIGHBOURS:
            spot = (sw[0] + dx, sw[1] + dy)
            if not search_to(spot, nodes, budget):
                continue
            d.emu.run_sequence(f"{FACE[(dx, dy)]}:4 .:20")
            for _ in range(3):
                d.emu.run_sequence("A:4 .:40")
                d.advance_scene(30000)
                if d.switch_signature() != before:
                    log.info("  pressed %s from %s: %s -> %s", sw, spot,
                             before, d.switch_signature())
                    return True
        return False

    if d.pos() != APPROACH:
        solved = search_to(APPROACH, a.nodes, a.budget)
        # Each press re-points a block of arrows and can open the way to the
        # next switch, so keep pressing whatever has become reachable.
        for _round in range(6):
            if solved:
                break
            progressed = False
            for sw in SWITCHES:
                if press_switch(sw):
                    progressed = True
                    if search_to(APPROACH, a.nodes, a.budget):
                        solved = True
                        break
            log.info("round %d: switches %s, at %s", _round,
                     d.switch_signature(), d.pos())
            if not progressed:
                break
        if not solved:
            log.info("FAIL: no reachable switch opened a route to %s",
                     APPROACH)
            if a.out:
                d.save(a.out)
            return 1

    before = len(d.state.badges())
    d.emu.run_sequence("UP:4 .:24")
    for i in range(8):
        d.emu.run_sequence("A:4 .:45")
        if d.in_battle():
            log.info("battle started on press %d", i)
            break
    if d.in_battle():
        result = d.fight()
        log.info("battle outcome: %s", (result or {}).get("outcome"))
        for _ in range(4):
            d.advance_scene(150000)
    after = len(d.state.badges())
    log.info("BADGES %d -> %d", before, after)
    if a.out and after > before:
        d.save(a.out)
        log.info("saved %s", a.out)
    return 0 if after > before else 1


if __name__ == "__main__":
    raise SystemExit(main())
