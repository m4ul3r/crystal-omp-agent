#!/usr/bin/env python
"""Throwaway: compare nav's SkyPillar_4F/2F grid against the LIVE grid.

Static analysis says 4F's y=4 corridor is sealed from both regions, which
cannot be true of a completable game. Either the momentum model is too
conservative, or nav's decoded collision differs from the live map. This
answers the second question directly by warping onto the floor and reading
the live grid via sync_grid().
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

from pokeagent.trek import Driver  # noqa: E402
from rq_plan import Pillar, CRACK  # noqa: E402

state = sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state"
d = Driver(state, live=False)
if d.at_title():
    print("resume:", d.resume_from_title())
print("at", d.map_name(), d.pos())

p = Pillar()


def dump(m):
    """Static (nav) view of a floor's collision/behaviour rows."""
    g = d.nav.grid(m)
    print(f"--- {m} live-nav {len(g[0])}x{len(g)}")
    for y in range(len(g)):
        row = ""
        for x in range(len(g[0])):
            c = g[y][x]
            if c.collision != 0:
                row += "#"
            elif c.behavior == CRACK:
                row += "x"
            elif c.behavior == 0x66:
                row += "o"
            else:
                row += "."
        print(f"  {y:2d} {row}")


for m in ("SkyPillar_4F", "SkyPillar_2F"):
    dump(m)

# Now: does sync_grid change anything once we are actually standing there?
print()
print("sync_grid from current map:", d.map_name())
try:
    n = d.sync_grid()
    print("synced cells:", n)
except Exception as exc:  # noqa: BLE001
    print("sync_grid failed:", exc)
