#!/usr/bin/env python
"""Decisive test: can a same-frame key swap hold mach speed through a turn?

Why this matters: trial A of rq_offbyone.py looked like a survival but ended
ON (11,5) having moved a single tile in 40 frames -- that is
EventScript_FallDownHole mid-flight (lockall, delay 20, applymovement,
playse, delay 60 -- cave_hole.inc:9-18), not a survival. Trial B, with 60
frames, completed the same fall. So BOTH trials fell, the original
"a cracked tile must be the 3rd+ tile of a continuous hold" rule stands, and
my retraction of it over-corrected.

That puts the whole climb back on the held TURN, because with legs that each
start from rest the BFS is NO ROUTE. `run_sequence` cannot express a turn
(emu.py:342 clears every key before each step), but the core can. My first
attempt at that (rq_turn.py) read speed 4 -> 1 and called it dead, but its
phase 1 landed on (11,11) where an identical run_sequence("DOWN:60") landed
on (11,13) -- so it was sampling d.pos() mid-tile and is not trustworthy.

This runs the real 2F route as ONE uninterrupted hold with three direction
changes, driving the core directly and never releasing a frame:

    DOWN  60f  (11,2)  -> (11,13)   crossing (11,5)(11,6)(11,10)(11,11)(11,12)
    LEFT  56f  (10,13) -> (1,13)    crossing (5,13)(4,13)(3,13)
    UP    40f  (1,12)  -> (1,7)     crossing (1,10)(1,9)(1,8)
    RIGHT  4f  (2,7)               crossing (2,7)
    UP    20f  (2,6)   -> (2,2)

Surviving means ending on 2F at (2,2); falling lands on SkyPillar_1F.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.emu import KEYS  # noqa: E402

STATE = sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state"

d = Driver(STATE, live=False)
if d.at_title():
    d.resume_from_title()
print("floor", d.map_name(), "pos", d.pos())
if d.map_name() != "SkyPillar_2F":
    print("need a save on SkyPillar_2F")
    raise SystemExit(2)
if not d.on_bike():
    d.mount_bike()
if d.pos() != (11, 2):
    d.goto(11, 2, label="runup", on_battle="fight")
    d.settle(120)
print("run-up", d.pos(), "speed", d.bike_speed(), "on_bike", d.on_bike())

LEGS = [("DOWN", 60), ("LEFT", 56), ("UP", 40), ("RIGHT", 4), ("UP", 20)]

core = d.emu.core
core.clear_keys(*KEYS.values())
try:
    for i, (key, frames) in enumerate(LEGS):
        # Add the new direction FIRST, then drop the others, so no frame is
        # ever ticked with no direction held.
        core.add_keys(KEYS[key])
        for other in ("DOWN", "UP", "LEFT", "RIGHT"):
            if other != key:
                core.clear_keys(KEYS[other])
        d.emu.tick(frames)
        print(f"  leg {i} {key}:{frames} -> {d.pos()} speed {d.bike_speed()} "
              f"map {d.map_name()}")
        if d.map_name() != "SkyPillar_2F":
            print("  FELL during leg", i)
            break
finally:
    core.clear_keys(*KEYS.values())
    d.emu._held = frozenset()

d.settle(180)
print("final", d.map_name(), d.pos(), "speed", d.bike_speed())
print("SURVIVED the turns" if d.map_name() == "SkyPillar_2F" and d.pos() == (2, 2)
      else "did not reach (2,2)")
