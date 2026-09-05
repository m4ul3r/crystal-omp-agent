#!/usr/bin/env python
"""Money is the wall at Drake. Go earn some, come back, and fight properly.

The gauntlet is now completely predictable: Sidney, Phoebe and Glacia all
fall, and then Drake wipes the party. Three reasons, and only one of them is
fixable tonight:

* **Drake is dragons.** Shelgon, Altaria, Kingdra and two Flygon. OVERHEAT is
  0.5x into every one of them and SURF only reaches 2x on Flygon; the bag has
  no Ice TM, so there is no type answer available.
* **The items are gone before he starts.** One lap's prize money is about
  8,000, which buys six Hyper Potions, and the first three leaders spend them.
* **A whiteout costs nothing**, so more attempts is not the lever -- more
  ITEMS per attempt is.

So: leave the plateau, farm trainers for real money, cross Victory Road again
(scripted and reliable now), and arrive with three or four times the healing.
`trainer_farm` reported ~5,000 per fifteen minutes against 500+ unbeaten
trainer flags, so an hour is roughly twenty Hyper Potions rather than six.

Each stage runs as its own process on purpose: the emulator holds one savestate
at a time, and a crash in one stage must not take the others with it.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402

log = logging.getLogger("fund")

HERE = Path(__file__).resolve().parent
PLATEAU = "EverGrandeCity"


def settle(d) -> None:
    for _ in range(8):
        if d.in_battle():
            d.fight()
            d.advance_scene(60000)
        elif d.scene_active():
            d.advance_scene(60000)
            d.close_menus()
        else:
            return


def guard(d, fn, *a, **k):
    for _ in range(4):
        try:
            return fn(*a, **k)
        except TravelInterrupted:
            settle(d)
    return None


def out_of_the_league(state: str, feed: str) -> bool:
    """Get from wherever the gauntlet left us onto the open plateau.

    Elite Four rooms seal their doors until their trainer is beaten, so the
    exit is either the already-beaten walk-out or engaging and losing.
    `league_loop.leave_a_room` knows both cases.
    """
    import league_loop

    d = Driver(state)
    if getattr(d.emu, "observer", None) is not None:
        d.emu.observer = None
    LiveFeed(feed).attach(d)
    settle(d)
    log.info("start %s %s", d.map_name(), d.pos())
    for _ in range(12):
        name = d.map_name()
        if name == PLATEAU:
            break
        if name.endswith("Room") and name.startswith("EverGrandeCity_"):
            league_loop.leave_a_room(d)
        elif "Corridor" in name:
            # CORRIDORS ARE NOT SEALED -- only the rooms are -- so walk. Go
            # UP, into the next room: that room's own exit logic (win, or lose
            # and whiteout to the nurse) lands us where we need to be, and it
            # is far shorter than retreating room by room to the hall. Leaving
            # this case out stranded the run in Corridor3, where the farm then
            # earned exactly nothing because it could not travel.
            warps = [(w.x, w.y) for w in d.nav.info(name).warps]
            if not warps:
                break
            cell = min(warps, key=lambda c: c[1])       # the upper door
            for dx, dy, mv in ((0, 1, "U"), (-1, 0, "R"), (1, 0, "L")):
                stand = (cell[0] + dx, cell[1] + dy)
                c = d.nav.cell(name, *stand)
                if c is None or c.collision:
                    continue
                guard(d, d.goto, stand[0], stand[1], on_battle="fight")
                settle(d)
                if d.pos() != stand:
                    continue
                guard(d, d.step_dir, mv)
                settle(d)
                if d.map_name() != name:
                    break
            if d.map_name() == name:
                log.info("  stuck in %s at %s", name, d.pos())
                break
            log.info("  advanced to %s %s", d.map_name(), d.pos())
        elif name == league_loop.LEAGUE:
            guard(d, d.goto, 9, 10, on_battle="fight")
            for _ in range(4):
                guard(d, d.take_warp, 9, 11)
                settle(d)
                if d.map_name() == PLATEAU:
                    break
        else:
            break
    log.info("on the plateau: %s %s", d.map_name(), d.pos())
    ok = d.map_name() == PLATEAU
    d.save(state)
    return ok


def stage(name, argv) -> int:
    log.info("=== %s ===", name)
    r = subprocess.run([sys.executable, *argv], cwd=str(HERE.parent))
    log.info("=== %s exited %d ===", name, r.returncode)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--farm-minutes", type=float, default=60.0)
    ap.add_argument("--fight-minutes", type=float, default=300.0)
    ap.add_argument("--feed", default="default")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not out_of_the_league(a.state, a.feed):
        log.info("could not get onto the plateau -- farming from here anyway")

    stage("farm", [str(HERE / "trainer_farm.py"), "--state", a.state,
                   "--out", a.state, "--minutes", str(a.farm_minutes)])

    d = Driver(a.state)
    log.info("after farming: %s %s money %d", d.map_name(), d.pos(),
             d.state.money())
    del d

    stage("cross", [str(HERE / "league_chain.py"), "--state", a.state,
                    "--out", a.state, "--feed", a.feed])

    d = Driver(a.state)
    log.info("after crossing: %s %s money %d", d.map_name(), d.pos(),
             d.state.money())
    del d

    return stage("fight", [str(HERE / "league_loop.py"), "--state", a.state,
                           "--minutes", str(a.fight_minutes),
                           "--feed", a.feed])


if __name__ == "__main__":
    raise SystemExit(main())
