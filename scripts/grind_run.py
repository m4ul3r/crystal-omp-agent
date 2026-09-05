#!/usr/bin/env python
"""Level the party on Sootopolis water until it can take Wallace.

The team reached badge 8's door at L36-48 still swinging CUT and HEADBUTT as
attacks, and lost to a 147 HP Milotic. Nothing on the roster is
super-effective against Water, so the only lever left in the time available is
levels.

Surf the city's lake, fight everything with `Driver.damage_first`, and duck
into the Pokemon Center whenever the lead drops -- the Center door is right
there at (43,31), which is why this grinds here rather than on a route.
"""
import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("grind")

CITY = "SootopolisCity"
CENTER_DOOR = (43, 31)


def heal_up(d) -> bool:
    """Into the Center, heal, back out."""
    try:
        d.goto(CENTER_DOOR[0], CENTER_DOOR[1] + 1, on_battle="fight")
    except Exception:  # noqa: BLE001
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
    if d.pos() != (CENTER_DOOR[0], CENTER_DOOR[1] + 1):
        return False
    if not d.take_warp(*CENTER_DOOR):
        return False
    ok = d.heal()
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    return ok


def hurt(d) -> bool:
    party = d.state.party()
    lead = party[0]
    return lead.hp * 3 < lead.max_hp or lead.hp == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=60.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    rng = random.Random(7)
    deadline = time.time() + a.minutes * 60
    battles = 0
    start_levels = [m.level for m in d.state.party()]
    log.info("START %s %s levels %s", d.map_name(), d.pos(), start_levels)

    while time.time() < deadline:
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
            battles += 1
            if battles % 10 == 0:
                log.info("%d battles | levels %s | %.0f min left", battles,
                         [m.level for m in d.state.party()],
                         (deadline - time.time()) / 60)
                if a.out:
                    d.save(a.out)
            continue
        d.close_menus()
        if d.map_name() != CITY:
            try:
                d.travel(CITY, on_battle="fight")
            except Exception:  # noqa: BLE001
                pass
            continue
        if hurt(d):
            if not heal_up(d):
                log.info("could not heal (at %s)", d.pos())
            continue
        # GET ONTO THE WATER FIRST. Wild encounters come from surfing steps;
        # a random walk that starts on the quay just tours the city, which is
        # what the first run did for four minutes without a single battle.
        d._surf_sync()
        if not d.is_surfing():
            grid = d.nav.grid(CITY)
            water = [(x, y) for y, row in enumerate(grid)
                     for x, c in enumerate(row)
                     if c is not None and not c.collision and c.kind == "water"]
            px, py = d.pos()
            water.sort(key=lambda w: abs(w[0] - px) + abs(w[1] - py))
            for spot in water[:12]:
                try:
                    d.goto(*spot, on_battle="fight")
                except Exception:  # noqa: BLE001
                    if d.in_battle():
                        break
                if d.is_surfing():
                    break
            if not d.is_surfing() and not d.in_battle():
                log.info("could not get onto the water from %s", d.pos())
                d.settle(60)
                continue
        for _ in range(6):
            if d.in_battle():
                break
            d.step_dir(rng.choice("UDLR"))
            d.settle(12)

    log.info("DONE %d battles | levels %s -> %s", battles, start_levels,
             [m.level for m in d.state.party()])
    if a.out:
        d.save(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
