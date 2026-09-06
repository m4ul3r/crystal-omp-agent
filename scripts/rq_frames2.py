#!/usr/bin/env python
"""Throwaway: does ONE uninterrupted hold build the mach counter?

`Emulator.run_sequence` calls `clear_keys` before EVERY step (emu.py:342), so
"DOWN:60 LEFT:40" releases everything between legs and a per-frame sample
(":1" repeated) releases every frame. Momentum needs an uninterrupted hold,
so measure the single-step case: one "LEFT:60" and read position/speed after,
then compare against stepping the core directly with the key still down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, _HOLD  # noqa: E402
from pokeagent.emu import KEYS  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()
if not d.on_bike():
    d.mount_bike()
m = d.map_name()
print("floor", m, "pos", d.pos(), "speed", d.bike_speed())

direction = sys.argv[2] if len(sys.argv) > 2 else "L"
key = _HOLD[direction]

# 1) one single-step hold via the DSL
before = d.pos()
d.emu.run_sequence(f"{key}:60")
print(f"run_sequence {key}:60  {before} -> {d.pos()} speed {d.bike_speed()}")

# 2) hold the key down ourselves and tick, sampling without releasing
core = d.emu.core
core.clear_keys(*KEYS.values())
core.add_keys(KEYS[key])
print("holding key down manually, ticking 4 frames at a time:")
prev = d.pos()
try:
    for i in range(30):
        d.emu.tick(4)
        pos = d.pos()
        if pos != prev:
            print(f"  tick {i*4+4:3d} pos {pos} speed {d.bike_speed()} "
                  f"map {d.map_name()}")
            prev = pos
        if d.map_name() != m:
            print("  LEFT THE FLOOR")
            break
finally:
    core.clear_keys(*KEYS.values())
print("final", d.pos(), "speed", d.bike_speed(), "map", d.map_name())
