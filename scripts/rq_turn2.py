#!/usr/bin/env python
"""Throwaway: is the turn reset REAL, or was rq_turn.py mismeasuring?

Suspicion: rq_turn.py phase 1 held DOWN for 60 frames via the core and landed
on (11,11), while an identical `run_sequence("DOWN:60")` landed on (11,13).
Same key state, different answer -- so that test was reading `d.pos()` while
the player was mid-tile, and its "speed 4 -> 1" verdict may be an artifact
rather than the engine.

This probe samples speed AND position every single frame across the swap,
without ever releasing a direction, so the counter's behaviour at the turn is
observed instead of inferred.
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
print("floor", m, "start", d.pos())

if d.pos() != (11, 2):
    d.goto(11, 2, label="runup", on_battle="fight")
    d.settle(90)
print("run-up", d.pos(), "speed", d.bike_speed(), "on_bike", d.on_bike())

core = d.emu.core
g = d.nav.grid(m)


def what(x, y):
    c = g[y][x]
    return "#" if c.collision else ("x" if c.behavior == 0xD2 else ".")


# Phase 1: hold DOWN frame by frame WITHOUT ever clearing, sampling each frame.
core.clear_keys(*KEYS.values())
core.add_keys(KEYS["DOWN"])
print("\nphase 1: DOWN held, sampling every frame")
prev = d.pos()
hist = []
for f in range(1, 81):
    d.emu.tick(1)
    pos, spd = d.pos(), d.bike_speed()
    hist.append((f, pos, spd))
    if pos != prev:
        print(f"  f{f:3d} {pos} {what(*pos)} speed {spd}")
        prev = pos
    if d.map_name() != m:
        print("  LEFT THE FLOOR at frame", f, d.map_name(), d.pos())
        break
    if pos == (11, 13):
        print(f"  reached (11,13) at frame {f}, speed {spd}")
        break

print("after phase 1:", d.pos(), "speed", d.bike_speed(), "map", d.map_name())

# Phase 2: swap to LEFT with NO released frame, sampling every frame.
if d.map_name() == m:
    print("\nphase 2: add LEFT, drop DOWN on the same boundary; sample each frame")
    core.add_keys(KEYS["LEFT"])
    core.clear_keys(KEYS["DOWN"])
    prev = d.pos()
    for f in range(1, 81):
        d.emu.tick(1)
        pos, spd = d.pos(), d.bike_speed()
        if pos != prev:
            print(f"  f{f:3d} {pos} {what(*pos)} speed {spd}")
            prev = pos
        if d.map_name() != m:
            print("  FELL at frame", f, "->", d.map_name(), d.pos())
            break

core.clear_keys(*KEYS.values())
d.emu._held = frozenset()
d.settle(90)
print("\nfinal", d.map_name(), d.pos(), "speed", d.bike_speed())
print("A surviving 2F run reaches (1,13); a fall lands on SkyPillar_1F.")
