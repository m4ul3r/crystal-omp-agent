#!/usr/bin/env python
"""What every party and boxed Pokemon is HOLDING, by name.

The Meteor Falls MOON STONE ball is already taken in this line, and a
wild-caught mon keeps its held item, so a Lunatone in a box may be carrying
the only Moon Stone this save can still reach.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402


def main() -> int:
    d = Driver(sys.argv[1])
    d.advance_scene(20_000)
    for i, m in enumerate(d.state.party()):
        item = getattr(m, "item", None) or getattr(m, "held_item", None)
        print("party", i, d.names.species(m.species), "item", item,
              d.names.item(item) if isinstance(item, int) and item else "")
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    for slot, b in dex.boxed():
        item = getattr(b, "item", None) or getattr(b, "held_item", None)
        if isinstance(item, int) and item:
            print("boxed", slot, d.names.species(b.species), "holds",
                  d.names.item(item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
