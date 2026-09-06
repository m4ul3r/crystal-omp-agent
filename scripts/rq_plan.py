#!/usr/bin/env python
"""Sky Pillar route planner: the MACH BIKE momentum model, exactly.

Why this file exists at all: nothing in the harness models
`MB_CRACKED_FLOOR` (0xD2), so `goto` plans straight over Sky Pillar's
crumbling tiles and the player falls a floor. The rules below are read off
the ROM, not guessed:

* `PerStepCallback_806A07C` (pret/src/field_tasks.c:669-712) is a TASK, so it
  runs every frame. On the frame the player's dest coords change to a
  `MB_CRACKED_FLOOR` tile it sets `VAR_ICE_STEP_COUNT` to 0 unless
  `GetPlayerSpeed() == 4`, and `CaveHole_CheckFallDownHole`
  (pret/data/scripts/cave_hole.inc:1-2) turns a zero var into
  `EventScript_FallDownHole` -- checked by `TryRunOnFrameMapScript`
  UNCONDITIONALLY in `ProcessPlayerFieldInput` (field_control_avatar.c:230),
  i.e. every frame.
* The same callback SCHEDULES the tile to collapse three frames later
  (`data[4] = 3` -> `sub_806A040`, which rewrites metatile 0x236 to 0x237 =
  `MB_CRACKED_FLOOR_HOLE`, field_tasks.c:663-667 + :698-709). A hole sets the
  var to 0 on every frame it is stood on, so a tile occupied for longer than
  three frames after being entered drops the player -- which is why only the
  four-frame movement is survivable, and why the run must never come to REST
  on a cracked tile.
* `GetPlayerSpeed` is `sMachBikeSpeeds[bikeFrameCounter]` = {1,2,4}
  (bike.c:121, :1044-1059), and `MachBikeTransition_TrySpeedUp` moves with
  `sMachBikeSpeedCallbacks[counter]` = {Speed1 (16 frames/tile), Speed2 (8),
  Speed4 (4)} and increments the counter afterwards (bike.c:218-257). So a
  run from a standstill is: tile 1 sixteen frames, tile 2 eight, tile 3+ four
  -- and the first cracked tile must therefore be the THIRD tile of a
  continuous hold. Two solid tiles of run-up, always.
* A TURN keeps the speed. `MACH_TRANS_START_MOVING` is index 3 of
  `sMachBikeTransitions`, which is `MachBikeTransition_TrySlowDown`
  (bike.c:76-82, include/bike.h:29-34): it does `counter = --bikeSpeed`, and
  from full speed (counter 2, bikeSpeed 3) that is counter 2 again -- a
  four-frame tile. So one turn per straight tile is free, and a cracked tile
  may be entered immediately after a turn. TWO turns back to back are not:
  the second sees bikeSpeed 2 and drops the counter.
* Hitting a WALL calls `Bike_SetBikeStill` (bike.c:244) and kills the speed,
  so a wall-terminated ride must end on solid ground.
* A fall lands on the floor below at the SAME (x, y): `warphole MAP_UNDEFINED`
  -> `SetFixedHoleWarpAsDestination(x - 7, y - 7)` (scrcmd.c:762-777) against
  the `setholewarp` fixed each floor sets on resume (2F -> 1F, 4F -> 3F;
  data/maps/SkyPillar_{2,4}F/scripts.inc).

And the route that falls out of it is NOT "cross 2F, cross 4F". Sky Pillar's
staircases are ASYMMETRIC (4F (11,1) leads DOWN to 3F (3,1), not back to
where you came from) and 4F's western half plus its northern strip -- the
strip that holds (3,1), the only way to 5F -- cannot be reached from 4F's
eastern half at all. The way in is a DELIBERATE FALL at 4F (6,4)/(7,4), which
lands in the sealed pocket of 3F that holds the (7,1) staircase.
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.nav import MapData  # noqa: E402

CRACK = 0xD2
HOLE = 0x66
DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
#: frames per tile for movement callback index 0/1/2
FRAMES = (16, 8, 4)

FLOORS = ("SkyPillar_1F", "SkyPillar_2F", "SkyPillar_3F", "SkyPillar_4F",
          "SkyPillar_5F", "SkyPillar_Top")
#: which floor a fall lands on, from each floor's own `setholewarp`
FALL_TO = {"SkyPillar_2F": "SkyPillar_1F", "SkyPillar_4F": "SkyPillar_3F"}


class Pillar:
    """The eight Sky Pillar maps, with the momentum rules applied."""

    def __init__(self, maps=None, live=None):
        self.md = maps or MapData()
        #: {map: {(x,y): behavior}} overrides read from the running game
        self.live = live or {}

    def cell(self, m, x, y):
        g = self.md.grid(m)
        if not (0 <= y < len(g) and 0 <= x < len(g[0])):
            return None
        return g[y][x]

    def behavior(self, m, x, y):
        over = self.live.get(m, {}).get((x, y))
        if over is not None:
            return over
        c = self.cell(m, x, y)
        return None if c is None else c.behavior

    def open(self, m, x, y):
        """Walkable at all: not a wall and not an open hole."""
        c = self.cell(m, x, y)
        return (c is not None and c.collision == 0
                and self.behavior(m, x, y) != HOLE)

    def solid(self, m, x, y):
        """Walkable AND will not crumble: safe to enter slowly and to rest on."""
        return self.open(m, x, y) and self.behavior(m, x, y) != CRACK

    def cracked(self, m, x, y):
        return self.open(m, x, y) and self.behavior(m, x, y) == CRACK

    def warps(self, m):
        return {(w.x, w.y): (w.dest_map, w.dest_warp_id) for w in self.md.info(m).warps}

    def warp_target(self, m, x, y):
        """Where the warp at (x,y) lands: (map, x, y). Resolves the ID."""
        hit = self.warps(m).get((x, y))
        if hit is None:
            return None
        dest_const, wid = hit
        for name in FLOORS + ("SkyPillar_Entrance", "SkyPillar_Outside"):
            info = self.md.info(name)
            if info.const == dest_const:
                if not (0 <= wid < len(info.warps)):
                    return None
                w = info.warps[wid]
                return (name, w.x, w.y)
        return None

    # ---- rides ---------------------------------------------------------

    def run(self, m, x, y, legs):
        """Simulate one continuous hold with turns. `legs` is [(dir, tiles)].

        Returns (cells, ok, why). `cells` is every tile entered in order,
        each as (x, y, callback_index).
        """
        counter, speed = 0, 0
        cells = []
        cx, cy = x, y
        prev_dir = None
        for d, tiles in legs:
            for _ in range(tiles):
                turning = prev_dir is not None and d != prev_dir
                if turning:
                    if speed == 0:
                        counter = 0          # TURN_DIRECTION: turn in place
                    else:
                        speed -= 1
                        counter = speed
                    c = counter
                else:
                    c = counter
                dx, dy = DIRS[d]
                nx, ny = cx + dx, cy + dy
                prev_dir = d
                if not self.open(m, nx, ny):
                    # wall: Bike_SetBikeStill, and we stay put
                    if self.cracked(m, cx, cy):
                        return cells, False, f"stopped on cracked {(cx, cy)}"
                    return cells, True, f"wall at {(nx, ny)}"
                if self.cracked(m, nx, ny) and c != 2:
                    return cells, False, f"cracked {(nx, ny)} at callback {c}"
                cells.append((nx, ny, c))
                if not turning:
                    speed = counter + (counter >> 1)
                    if counter < 2:
                        counter += 1
                else:
                    speed = counter + (counter >> 1)
                    if counter < 2:
                        counter += 1
                cx, cy = nx, ny
                if (cx, cy) in self.warps(m):
                    return cells, True, "warp"
        if self.cracked(m, cx, cy):
            return cells, False, f"released on cracked {(cx, cy)}"
        return cells, True, "end-of-legs"

    def straight(self, m, x, y, d):
        """A wall-terminated hold in one direction. None when it would fall."""
        cells, ok, why = self.run(m, x, y, [(d, 40)])
        if not ok or not cells:
            return None
        return cells, why

    # ---- the search ----------------------------------------------------

    def plan(self, start, goal, allow_falls=True, max_turns=2):
        """BFS over (map, x, y) with walk steps, wall-terminated rides
        (optionally with turns), warps and deliberate falls."""
        q = deque([start])
        prev = {start: None}
        while q:
            cur = q.popleft()
            if cur == goal:
                break
            m, x, y = cur
            for d, (dx, dy) in DIRS.items():
                tx, ty = x + dx, y + dy
                if self.solid(m, tx, ty):
                    hit = self.warp_target(m, tx, ty)
                    nxt = hit if hit else (m, tx, ty)
                    op = ("warp", d, (tx, ty)) if hit else ("walk", d, (tx, ty))
                    if nxt not in prev:
                        prev[nxt] = (cur, op)
                        q.append(nxt)
                elif allow_falls and self.cracked(m, tx, ty) and m in FALL_TO:
                    land = (FALL_TO[m], tx, ty)
                    if self.open(*land) and land not in prev:
                        prev[land] = (cur, ("fall", d, (tx, ty)))
                        q.append(land)
            for legs in self._ride_shapes(m, x, y, max_turns):
                cells, ok, why = self.run(m, x, y, legs)
                if not ok or not cells:
                    continue
                ex, ey, _c = cells[-1]
                hit = self.warp_target(m, ex, ey)
                nxt = hit if hit else (m, ex, ey)
                op = ("ride", legs, (ex, ey), why, hit)
                if nxt not in prev:
                    prev[nxt] = (cur, op)
                    q.append(nxt)
        if goal not in prev:
            return None
        out = []
        cur = goal
        while prev[cur]:
            at, op = prev[cur]
            out.append((at, op))
            cur = at
        return out[::-1]

    def _ride_shapes(self, m, x, y, max_turns):
        """Every hold-and-turn shape worth trying from (x, y).

        A turn is only useful ON a tile, so the shapes are enumerated by
        turn tile rather than by frame count: `legs` counts TILES, and the
        driver switches keys when the player starts moving onto the tile the
        plan turns at.
        """
        for d in DIRS:
            yield [(d, 40)]
        if max_turns < 1:
            return
        for d in DIRS:
            first, _ok, _why = self.run(m, x, y, [(d, 40)])
            for i in range(1, len(first) + 1):
                for d2 in DIRS:
                    if d2 == d or DIRS[d2] == (-DIRS[d][0], -DIRS[d][1]):
                        continue
                    yield [(d, i), (d2, 40)]
                    if max_turns < 2:
                        continue
                    mid, _o, _w = self.run(m, x, y, [(d, i), (d2, 40)])
                    for j in range(1, len(mid) - i + 1):
                        for d3 in DIRS:
                            if d3 == d2 or DIRS[d3] == (-DIRS[d2][0], -DIRS[d2][1]):
                                continue
                            yield [(d, i), (d2, j), (d3, 40)]


def describe(steps):
    out = []
    for at, op in steps:
        if op[0] == "ride":
            out.append(f"{at} RIDE {op[1]} -> {op[2]} ({op[3]})"
                       + (f" warp {op[4]}" if op[4] else ""))
        else:
            out.append(f"{at} {op[0].upper()} {op[1]} -> {op[2]}")
    return "\n".join(out)


if __name__ == "__main__":
    p = Pillar()
    legs = [
        (("SkyPillar_1F", 6, 13), ("SkyPillar_2F", 10, 1)),
        (("SkyPillar_2F", 10, 1), ("SkyPillar_3F", 3, 1)),
        (("SkyPillar_3F", 3, 1), ("SkyPillar_4F", 11, 1)),
        (("SkyPillar_4F", 11, 1), ("SkyPillar_3F", 7, 4)),
        (("SkyPillar_3F", 7, 4), ("SkyPillar_4F", 7, 1)),
        (("SkyPillar_4F", 7, 1), ("SkyPillar_5F", 3, 1)),
        (("SkyPillar_5F", 3, 1), ("SkyPillar_Top", 16, 14)),
        (("SkyPillar_Top", 16, 14), ("SkyPillar_Top", 14, 7)),
    ]
    for a, b in legs:
        print(f"=== {a} -> {b}")
        steps = p.plan(a, b)
        print(describe(steps) if steps else "   NO ROUTE")
