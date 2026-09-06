#!/usr/bin/env python
"""Cold-read a save: bag stones/shards, party, and where the stone targets are.

Throwaway probe for the stone-evolution job. Every item name is resolved off
the cartridge (THUNDERSTONE is one word, WATER STONE is not).
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

STONES = ("ITEM_FIRE_STONE", "ITEM_MOON_STONE", "ITEM_WATER_STONE",
          "ITEM_SUN_STONE", "ITEM_LEAF_STONE", "ITEM_THUNDER_STONE")
SHARDS = ("ITEM_RED_SHARD", "ITEM_YELLOW_SHARD", "ITEM_BLUE_SHARD",
          "ITEM_GREEN_SHARD")
WANT = ("VULPIX", "SKITTY", "STARYU", "NINETALES", "DELCATTY", "STARMIE")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = Driver(sys.argv[1])
    d.advance_scene(40_000)
    print("map", d.map_name(), d.pos(), "money", d.state.money())
    for const in STONES + SHARDS:
        name = d.names.item(d.consts.items[const])
        print("  item", const, "->", repr(name))
    bag = d.state.bag()
    for pocket, items in bag.items():
        if isinstance(items, dict):
            print("BAG", pocket, dict(items))
    print("party", [(d.names.species(m.species), m.level, m.nickname)
                    for m in d.state.party()])
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    print("summary", dex.summary(d.state))
    for slot, b in dex.boxed():
        try:
            sp = d.names.species(b.species).upper()
        except Exception:  # noqa: BLE001
            continue
        if sp in WANT:
            print("BOXED", sp, "flat", slot, "box", slot // 30, "slot",
                  slot % 30, "nick", getattr(b, "nickname", None))
    flags = dex.dex_flags(d.state)
    print("flags type", type(flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
