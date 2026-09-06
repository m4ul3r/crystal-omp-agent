#!/usr/bin/env python
"""Throwaway: MEASURE the mach-bike counter, do not model it.

The 2F ride fell at (10,11) with a 16/8/4 frame model, so the model is wrong.
`Driver.bike_speed()` reads `sMachBikeSpeeds[gPlayerAvatar+0x0A]` = {1,2,4},
so the counter is observable. Hold one direction on a KNOWN-SAFE floor (1F
has zero cracked tiles) and sample position + speed every frame-chunk to
learn: frames per tile at each counter, and how many tiles it takes to reach
speed 4.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from pokeagent.trek import Driver, _HOLD  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()
print("map", d.map_name(), "pos", d.pos(), "on_bike", d.on_bike())

if not d.on_bike():
    print("mount:", d.mount_bike(), d.last_bike_reason)
print("on_bike", d.on_bike(), "speed", d.bike_speed())

m = d.map_name()
g = d.nav.grid(m)


def row(y):
    out = ""
    for x in range(len(g[0])):
        c = g[y][x]
        out += "#" if c.collision else ("x" if c.behavior == 0xD2 else ".")
    return out


print("current floor", m)
for y in range(len(g)):
    print(f"  {y:2d} {row(y)}")

# Sample a held ride one frame at a time.
direction = sys.argv[2] if len(sys.argv) > 2 else "L"
key = _HOLD[direction]
print(f"\nholding {key} for 120 frames, sampling every frame:")
prev = None
for f in range(120):
    d.emu.run_sequence(f"{key}:1")
    pos = d.pos()
    spd = d.bike_speed()
    if pos != prev:
        print(f"  frame {f:3d} pos {pos} speed {spd} map {d.map_name()}")
        prev = pos
    if d.map_name() != m:
        print("  LEFT THE FLOOR")
        break
print("final", d.pos(), "speed", d.bike_speed(), "map", d.map_name())
