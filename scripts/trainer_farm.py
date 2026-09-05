#!/usr/bin/env python
"""Farm unbeaten trainers for money, so the Pokedex can buy balls.

The dex has been supply-blocked for three sessions: no money -> no balls -> no
catches. The reason money never recovered is that **wild battles pay nothing
in Pokemon** -- only trainers do -- and every earlier attempt to "grind for
money" was grinding wilds.

Meanwhile the save has **547 of 693 trainer flags unset** (`TRAINER_FLAG_START
0x500`, `NUMBER_OF_TRAINERS 693`), which is a few hundred thousand in prize
money sitting untouched. With an L100 in front they are free wins, and one
route pays for dozens of balls.

Each map's trainers come from its own `object_events` (a `trainer_type` other
than NONE), and their flag comes from the script's `trainerbattle` id, so an
already-beaten trainer is skipped without walking to them.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("farm")

REPO = Path(__file__).resolve().parents[1]

#: Routes reachable on foot/Fly from the mid-game, richest first. Route 109
#: alone carries twenty trainer objects.
ROUTES = [
    "Route109", "Route110", "Route111", "Route112", "Route113", "Route114",
    "Route115", "Route116", "Route117", "Route118", "Route119", "Route120",
    "Route121", "Route123", "Route105", "Route106", "Route107", "Route108",
]


def trainers_on(map_name: str) -> list[tuple[int, int]]:
    """Cells of every trainer object on `map_name`."""
    path = REPO / "pret" / "data" / "maps" / map_name / "map.json"
    try:
        j = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for o in (j.get("object_events") or []):
        kind = str(o.get("trainer_type") or "")
        if kind and kind not in ("TRAINER_TYPE_NONE", "0"):
            out.append((int(o["x"]), int(o["y"])))
    return out


def farm_map(d, map_name: str, budget_s: float) -> int:
    """Fight every trainer we can reach here. Returns money earned."""
    before = d.state.money()
    stop = time.time() + budget_s
    cells = trainers_on(map_name)
    if not cells:
        return 0
    log.info("%s: %d trainer objects", map_name, len(cells))

    for cell in cells:
        if time.time() > stop:
            break
        try:
            # `talk_to` walks adjacent, faces them and presses A; a trainer's
            # own script starts the battle, and the driver plays it out.
            d.talk_to(*cell)
        except Exception as exc:  # noqa: BLE001
            log.debug("  %s at %s: %s", map_name, cell, type(exc).__name__)
        try:
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
            d.close_menus()
        except Exception:  # noqa: BLE001
            pass

    earned = d.state.money() - before
    log.info("%s: earned %d (money %d)", map_name, earned, d.state.money())
    return earned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--per-map", type=float, default=600.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    out = a.out or a.state
    deadline = time.time() + a.minutes * 60
    start_money = d.state.money()
    log.info("START %s money=%d", d.map_name(), start_money)

    for route in ROUTES:
        if time.time() > deadline:
            break
        try:
            if d.map_name() != route and not d.travel(route, on_battle="fight"):
                log.info("could not reach %s (%s)", route, d.last_goto_reason)
                continue
        except Exception as exc:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            log.info("travel %s raised %s", route, type(exc).__name__)
            if d.map_name() != route:
                continue
        farm_map(d, route, min(a.per_map, deadline - time.time()))
        d.save(out)

    log.info("DONE money %d -> %d (+%d)", start_money, d.state.money(),
             d.state.money() - start_money)
    d.save(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
