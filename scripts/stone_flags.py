#!/usr/bin/env python
"""Which stone/shard ground items this save has ALREADY taken.

Every one-shot item ball has an event flag (pret map.json `flag:`), so the
question "is the Fiery Path FIRE STONE still there" is answerable cold, in a
second, instead of by a twenty-minute walk that finds an empty tile.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeagent.trek import Driver  # noqa: E402

CHECKS = (
    ("FLAG_ITEM_FIERY_PATH_2", "FieryPath (7,32) FIRE STONE"),
    ("FLAG_ITEM_FIERY_PATH_1", "FieryPath (8,3) TM"),
    ("FLAG_ITEM_ROUTE124_2", "Route124 (28,12) RED SHARD"),
    ("FLAG_ITEM_ROUTE124_3", "Route124 (31,53) BLUE SHARD"),
    ("FLAG_ITEM_ROUTE124_1", "Route124 (58,11) YELLOW SHARD"),
    ("FLAG_ITEM_ROUTE126_1", "Route126 (14,1) GREEN SHARD"),
    ("FLAG_ITEM_METEOR_FALLS_1F_1R_3", "MeteorFalls_1F_1R (2,14) MOON STONE"),
    ("FLAG_ITEM_ABANDONED_SHIP_HIDDEN_FLOOR_ROOM_3_WATER_STONE",
     "AbandonedShip hidden floor (31,11) WATER STONE"),
    ("FLAG_HIDDEN_ITEM_18", "Underwater3 (72,20) hidden RED SHARD"),
    ("FLAG_HIDDEN_ITEM_C", "Underwater2 hidden BLUE SHARD"),
    ("FLAG_HIDDEN_ITEM_12", "Underwater2 hidden YELLOW SHARD"),
    ("FLAG_HIDDEN_ITEM_9", "Underwater1 hidden GREEN SHARD"),
)


def main() -> int:
    d = Driver(sys.argv[1])
    d.advance_scene(20_000)
    for flag, what in CHECKS:
        try:
            taken = d.state.flag(flag)
        except Exception as exc:  # noqa: BLE001
            print(f"{flag}: ERROR {exc}")
            continue
        print(f"{'TAKEN  ' if taken else 'PRESENT'}  {what}  ({flag})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
