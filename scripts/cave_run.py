#!/usr/bin/env python
"""Descend the Cave of Origin: HM07 WATERFALL on B3F, then Kyogre on B4F.

Resume-aware, because the Sootopolis escort cutscene walks the player INSIDE
the entrance on its own -- a fixed hop list starting in the city tried to
travel back out and reported the cave door unreachable while standing behind it.

Warp chain, from each map's own warp_events:
    Entrance (9,5) -> 1F | 1F (14,5) -> B1F | B1F (5,11) -> B2F
    B2F (8,14) -> B3F    | B3F (12,6) -> B4F
HM07 is the item ball at B3F (6,5); Kyogre is the coord_event at B4F (9,13).
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("cave")

HOPS = {
    "CaveOfOrigin_Entrance": (9, 5),
    "CaveOfOrigin_1F": (14, 5),
    "CaveOfOrigin_B1F": (5, 11),
    "CaveOfOrigin_B2F": (8, 14),
}


def step_through(d, door) -> bool:
    here = d.map_name()
    try:
        d.reach_cell(*door, map_name=here, on_battle="fight")
    except Exception as exc:  # noqa: BLE001
        log.info("  %s: %s", here, str(exc)[:70])
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
    if d.map_name() != here:
        return True
    if not d.take_warp(*door):
        log.info("  %s door %s refused: %s", here, door, d.last_warp_reason)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s", d.map_name(), d.pos())

    for _ in range(12):
        here = d.map_name()
        if here not in HOPS:
            break
        if not step_through(d, HOPS[here]):
            return 1
        log.info("%s -> %s %s", here, d.map_name(), d.pos())
        if a.out:
            d.save(a.out)

    if d.map_name() == "CaveOfOrigin_B3F":
        if not d.state.flag("FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1"):
            try:
                d.reach_cell(6, 5, map_name="CaveOfOrigin_B3F",
                             on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            d.advance_scene(90000)
        log.info("HM07 WATERFALL: %s",
                 "GOT IT" if d.state.flag("FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1")
                 else "MISSED")
        if a.out:
            d.save(a.out)
        if not step_through(d, (12, 6)):
            return 1

    if d.map_name() != "CaveOfOrigin_B4F":
        log.info("FAIL: expected B4F, in %s", d.map_name())
        return 1

    # Kyogre. The flag block runs after the battle whatever its outcome.
    try:
        d.reach_cell(9, 13, map_name="CaveOfOrigin_B4F", on_battle="fight")
    except Exception:  # noqa: BLE001
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
    for _ in range(10):
        d.advance_scene(120000)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
        if d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED"):
            break
    log.info("RESULT legendary=%s waterfall=%s at %s",
             d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED"),
             d.state.flag("FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1"), d.map_name())
    if a.out:
        d.save(a.out)
    return 0 if d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
