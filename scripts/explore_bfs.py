#!/usr/bin/env python
"""Breadth-first search over REAL game states, not over a decoded grid.

Every model this repo has built for Victory Road is now faithful -- the
`.blk` decode matches `gBackupMapLayout` with zero drift, elevation is carried
through the search the way `ObjectEventUpdateZCoord` does, water needs Surf,
breakable rocks are removable, boulders are believed correctly -- and the
Pokemon League is *still* unreachable. When the data is honest and the search
agrees with it, the remaining suspect is our INTERPRETATION, and the only way
to settle that is to stop interpreting.

So this asks the emulator. From a savestate it tries a direction, reads where
the avatar actually ended up, and forks: `save_raw_state` / `load_raw_state`
are in-memory, so a state is a value we can put in a queue. No walkability
model is consulted at any point -- if the engine moves the player, the edge
exists.

    explore_bfs.py --state S --from 8,3 --to 17,16 --map VictoryRoad_B1F

A path means the model refuses a move the game allows, and the log names the
first such move. No path -- with the frontier genuinely exhausted -- means the
floor needs something we do not have yet (an item, a script, a scene), which
turns the question into a decomp read instead of a search.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("bfs")

DIRS = ("U", "D", "L", "R")

#: Same order as DIRS, for reading the tile a move faces.
_DELTA = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


#: Fork through the DRIVER's own save/load, not the bare core calls.
#: `save_raw_state`/`load_raw_state` skip the bookkeeping `emu.load_state`
#: does, and a BFS built on them reached 35 cells from a tile the boulder
#: solver demonstrably walks hundreds of cells from -- it was measuring the
#: restore, not the dungeon. Slower per node and correct, which is the only
#: useful kind of fast here.
_SCRATCH = Path("/tmp/.explore_bfs_fork.state")


def _raw(d):
    d.emu.save_state(_SCRATCH)
    return _SCRATCH.read_bytes()


def _restore(d, blob) -> None:
    _SCRATCH.write_bytes(blob)
    d.emu.load_state(_SCRATCH)
    # LET THE RESTORE LAND BEFORE READING WRAM. `step_dir(verify=True)` reads
    # `pos()` before and after the press, so a stale "before" makes a real
    # move look like a no-op and the branch is pruned. This is the leading
    # suspect for the reachable set stalling at 35 cells across five other
    # configurations -- UNVERIFIED at time of writing, kept because it is
    # cheap and correct regardless.
    d.settle(8)


def explore(d, goal, budget_s=1800.0, max_nodes=4000):
    """BFS over game states. Returns the move string to `goal`, or None."""
    # ARM THE PLAYER'S ACTUAL OPTIONS FIRST. The first run of this reached
    # exactly 23 cells and emptied its frontier, which looked like a damning
    # result and was really just an unarmed player: Strength was off, so no
    # boulder would budge, and nothing ever pressed A, so the rock at (18,12)
    # -- the gate the working route smashes -- stayed put. A search that
    # cannot do what the player can proves nothing.
    try:
        d.use_strength()
    except Exception:  # noqa: BLE001
        pass
    # The map's own breakable rocks, so the A press can be aimed rather than
    # sprayed. Off-camera objects are in this table too, which live_npcs()
    # cannot see.
    rocks = set()
    try:
        for obj in (d.nav.info(d.map_name()).objects or []):
            if str(obj.get("script") or "") == "S_BreakableRock":
                rocks.add((obj["x"], obj["y"]))
    except Exception:  # noqa: BLE001
        pass
    log.info("  %d breakable rocks on this floor", len(rocks))
    start_map = d.map_name()
    start = d.pos()
    seen = {start}
    queue = deque([(start, _raw(d), "")])
    stop = time.time() + budget_s
    expanded = 0

    while queue and time.time() < stop and expanded < max_nodes:
        pos, blob, path = queue.popleft()
        expanded += 1
        if expanded % 25 == 0:
            log.info("  %d expanded, frontier %d, at %s", expanded,
                     len(queue), pos)
        for mv in DIRS:
            _restore(d, blob)
            try:
                # PRESS A ONLY AT A ROCK. An unconditional face-then-A cost
                # this search the dungeon: at a boulder that A re-opens the
                # Strength prompt, and the close_menus() after it eats the
                # step -- 35 cells reached where the boulder walk crosses
                # hundreds from the same tile. The proven walk presses A
                # deliberately, from an adjacent cell, at a rock it means to
                # smash, and never otherwise.
                dx, dy = _DELTA[mv]
                ahead = (pos[0] + dx, pos[1] + dy)
                if ahead in rocks:
                    d.emu.run_sequence(f"{mv}:4 .:8 A:4 .:24")
                    if d.in_battle():
                        d.fight(policy=Driver.damage_first)
                        d.advance_scene(30000)
                    d.close_menus()
                d.step_dir(mv)
                # SETTLE LONG ENOUGH FOR A BATTLE TO EXIST. 24 frames is not
                # enough: these corridors are GRASS, a step into them starts a
                # wild encounter, and reading `pos()` mid-transition returns
                # the cell we came from -- which this search scored as "did not
                # move" and pruned. That is the entire reason the reachable set
                # stopped at 35 cells with (9,12) in it and (9,13) -- ordinary
                # walkable grass one step south -- outside. The corridor to
                # the rest of B1F runs along that row.
                for _ in range(4):
                    d.settle(90)
                    if d.in_battle():
                        d.fight(policy=Driver.damage_first)
                        d.advance_scene(40000)
                        continue
                    if d.scene_active():
                        d.advance_scene(40000)
                        d.close_menus()
                        continue
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("  %s at %s raised %s", mv, pos, type(exc).__name__)
                continue

            here, now = d.map_name(), d.pos()
            if here != start_map:
                # Left the floor: interesting, but a different search.
                log.info("  %s%s leaves %s at %s -> %s %s", path, mv,
                         start_map, pos, here, now)
                continue
            if now == pos or now in seen:
                continue
            seen.add(now)
            nxt = path + mv
            if now == goal:
                log.info("REACHED %s in %d moves: %s", goal, len(nxt), nxt)
                return nxt
            queue.append((now, _raw(d), nxt))

    log.info("no path to %s (%d expanded, %d reached, frontier %d)", goal,
             expanded, len(seen), len(queue))
    # NAME THE POCKET. "35 cells" is not a diagnosis; the cells are. Comparing
    # them against a route the boulder solver has verifiably walked shows the
    # exact step where the two instruments part company.
    log.info("reached: %s", sorted(seen))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--to", required=True, help="x,y goal on this map")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--max-nodes", type=int, default=4000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    gx, gy = (int(v) for v in a.to.split(","))
    d = Driver(a.state)
    log.info("START %s %s -> (%d,%d)", d.map_name(), d.pos(), gx, gy)
    path = explore(d, (gx, gy), a.minutes * 60.0, a.max_nodes)
    log.info("RESULT %s", path if path else "unreachable")
    return 0 if path else 1


if __name__ == "__main__":
    raise SystemExit(main())
