#!/usr/bin/env python
"""Throwaway: reachability of the Ice Room floor from its only warp, and
whether a firing warp chain exists from the entrance to the Ice Room on the
SHIPPED (low tide) layouts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from pyre_shoal import warp_route, SHOAL_ROOMS  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/shoal.state")
nav = d.nav

for name, cell in (("ShoalCave_LowTideIceRoom", (17, 10)),
                   ("ShoalCave_LowTideLowerRoom", (28, 11)),
                   ("ShoalCave_LowTideEntranceRoom", (20, 30))):
    lands = None
    for e in nav.exits(name):
        if (e["x"], e["y"]) == cell:
            lands = e.get("lands_at")
    print("== %s warp %s lands_at %s" % (name, cell, lands))
    for probe in ({tuple(lands)} if lands else set()) | {cell}:
        c = nav.cell(name, *probe)
        if c is None:
            print("   %s: no cell" % (probe,))
            continue
        reach = nav.reachable(name, probe, c.elevation)
        grass = set(nav.find_tiles(name, "grass")) & set(map(tuple, reach))
        print("   from %s (beh 0x%02X kind %s elev %s): %d reachable, "
              "%d grass" % (probe, c.behavior, c.kind, c.elevation,
                            len(reach), len(grass)))

allowed = frozenset(SHOAL_ROOMS)
start = "ShoalCave_LowTideEntranceRoom"
# Landing when arriving from Route 125.
lands = None
for e in nav.exits(start):
    if e.get("dest") == "Route125":
        lands = tuple(e["lands_at"])
print("route125 warp lands_at", lands)
for goal in ("ShoalCave_LowTideInnerRoom", "ShoalCave_LowTideLowerRoom",
             "ShoalCave_LowTideIceRoom"):
    r = warp_route(nav, start, lands or (20, 29), goal, None, allowed)
    print("chain to %-32s %s" % (goal, [(m, c, dm) for m, c, dm, _ in r]))
