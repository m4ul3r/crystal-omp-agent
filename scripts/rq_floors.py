#!/usr/bin/env python
"""Throwaway: 4F fall geometry + per-floor warp/crack maps for the climb."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq_plan import Pillar, CRACK, HOLE, FLOORS  # noqa: E402

p = Pillar()

for m in FLOORS:
    info = p.md.info(m)
    print("===", m, f"{info.width}x{info.height}")
    print("  warps:", [(w.x, w.y, w.dest_map, w.dest_warp_id) for w in info.warps])
    cracks = []
    holes = []
    g = p.md.grid(m)
    for y in range(len(g)):
        for x in range(len(g[0])):
            b = p.behavior(m, x, y)
            if b == CRACK:
                cracks.append((x, y))
            elif b == HOLE:
                holes.append((x, y))
    print("  cracked:", len(cracks), cracks[:60])
    if holes:
        print("  holes:", len(holes), holes[:20])

# The specific claim to test: 4F (6,4)/(7,4) cracked, and 3F (6,4)/(7,4) open.
print()
for xy in ((6, 4), (7, 4)):
    print("4F", xy, "cracked?", p.cracked("SkyPillar_4F", *xy),
          "| 3F open?", p.open("SkyPillar_3F", *xy),
          "solid?", p.solid("SkyPillar_3F", *xy))
print("3F (7,1) warp ->", p.warp_target("SkyPillar_3F", 7, 1))
print("3F (7,4)->(7,1) column:",
      [(y, p.behavior("SkyPillar_3F", 7, y), p.open("SkyPillar_3F", 7, y))
       for y in range(0, 6)])
