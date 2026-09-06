#!/usr/bin/env python
"""Throwaway: measure frames-per-tile on a leg with ROOM.

The last probe held LEFT from 2F (10,2), but row 2 is `........#.....` -- x=8
is wall, so it stopped after one tile at the wall and told us nothing about
momentum. Column 11 going DOWN has eleven tiles of room, so hold DOWN from
(11,2) inside ONE run_sequence step (no clear_keys between frames) and read
where each tile boundary falls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, _HOLD  # noqa: E402

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state", live=False)
if d.at_title():
    d.resume_from_title()
if not d.on_bike():
    d.mount_bike()
m = d.map_name()
print("floor", m, "pos", d.pos(), "speed", d.bike_speed())

# Get to the run-up cell first, on foot.
if d.pos() != (11, 2):
    print("goto (11,2):", d.goto(11, 2, label="runup", on_battle="fight"),
          d.pos(), d.last_goto_reason)
print("at", d.pos(), "speed", d.bike_speed(), "on_bike", d.on_bike())

g = d.nav.grid(m)
col = [(y, "#" if g[y][11].collision else ("x" if g[y][11].behavior == 0xD2 else "."))
       for y in range(len(g))]
print("column 11:", col)

# ONE step: the key stays down for the whole thing.
frames = int(sys.argv[2]) if len(sys.argv) > 2 else 60
before = d.pos()
d.emu.run_sequence(f"{_HOLD['D']}:{frames}")
d.settle(90)
print(f"DOWN:{frames}  {before} -> {d.pos()} speed {d.bike_speed()} "
      f"map {d.map_name()}")
