#!/usr/bin/env python
"""Throwaway: PROVE whether a mid-ride turn can keep the mach counter.

Every crossing needs at least one turn taken at full speed. `run_sequence`
clears keys before each step (emu.py:342), so the DSL cannot express it. But
the core can: add_keys(DOWN), tick, then add DOWN+LEFT / swap to LEFT with no
released frame in between.

Test on 2F column 11 -> row 13, the leg that actually failed:
  hold DOWN from (11,2) for 60 frames  -> (11,13) at full speed (measured)
  then swap to LEFT with NO release    -> does it cross (5,13),(4,13),(3,13)?

If the swap keeps the counter, the crossing is drivable by talking to the
core directly and the leg is unblocked.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.emu import KEYS  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()
if not d.on_bike():
    d.mount_bike()
m = d.map_name()
print("floor", m, "pos", d.pos())

if d.pos() != (11, 2):
    d.goto(11, 2, label="runup", on_battle="fight")
print("run-up at", d.pos(), "speed", d.bike_speed())

core = d.emu.core


def hold(key, frames, label=""):
    """Press `key` WITHOUT clearing anything else first, then tick."""
    core.clear_keys(*KEYS.values())
    core.add_keys(KEYS[key])
    d.emu.tick(frames)
    print(f"  {label}{key}:{frames} -> {d.pos()} speed {d.bike_speed()} "
          f"map {d.map_name()}")


def swap(key, frames, label=""):
    """Swap the held direction with NO released frame: add the new key, then
    drop the old one on the SAME frame boundary."""
    core.add_keys(KEYS[key])
    for other in ("DOWN", "UP", "LEFT", "RIGHT"):
        if other != key:
            core.clear_keys(KEYS[other])
    d.emu.tick(frames)
    print(f"  {label}swap->{key}:{frames} -> {d.pos()} "
          f"speed {d.bike_speed()} map {d.map_name()}")


try:
    print("phase 1: DOWN 60 frames (expect (11,13), full speed)")
    hold("DOWN", 60)
    print("phase 2: swap straight to LEFT, 56 frames (10 tiles at speed 4)")
    swap("LEFT", 56)
finally:
    core.clear_keys(*KEYS.values())
    d.emu._held = frozenset()

d.settle(90)
print("final", d.map_name(), d.pos(), "speed", d.bike_speed())
print("VERDICT: on 2F a surviving run ends at (1,13); a fall lands on "
      "SkyPillar_1F at the tile it dropped through.")
