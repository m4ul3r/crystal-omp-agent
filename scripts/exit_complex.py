#!/usr/bin/env python
"""Get out of the Pokemon League building with the wallet intact.

Every high-money savestate this run produced is sitting INSIDE the League
complex, and the complex is one-way: the same `setmetatile` that seals a
leader's room behind you seals the corridors too, so there is no walking back
to the hall (proved by stepping onto Corridor5's (4,12), which the map data
lists as a warp to the hall -- the map did not change).

So the only exits are winning and a whiteout, and a whiteout is what this
takes: advance forward through the already-beaten rooms until the Champion
ends the run, and the game puts the player back on the plateau OUTDOORS,
where Fly works and `collect.py` can take over.

The wallet is the point. `league_loop.py` also gets out, but it spends the
money on Hyper Potions on the way (measured: 15,878 -> 1,089 in one lap),
which is exactly wrong when the money is earmarked for Poke Balls. This does
no shopping and no healing.

Money is halved by the whiteout -- that is the game's rule and there is no way
around it from in here. Better to lose half of a big number once than to
re-earn it from scratch.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from scripts.league_loop import (  # noqa: E402
    PLATEAU, leave_a_room, settle, guard,
)

log = logging.getLogger("exit")


def outdoors(d) -> bool:
    return d.map_name() == PLATEAU


def run(state: str, out: str, hops: int = 30) -> int:
    d = Driver(state)
    d.advance_scene(40_000)
    log.info("start %s %s money %d", d.map_name(), d.pos(), d.state.money())

    for hop in range(hops):
        if outdoors(d):
            log.info("OUT at %s %s money %d", d.map_name(), d.pos(),
                     d.state.money())
            d.save(out)
            log.info("banked %s", out)
            return 0
        before = d.map_name()
        try:
            # Forward is the only way, and a beaten room's leader just chats,
            # so this walks rather than fights most rooms.
            guard(d, leave_a_room, d)
        except TravelInterrupted as exc:
            # A wild or trainer battle mid-walk is normal in Victory Road.
            log.info("battle: %s", exc)
            guard(d, d.fight)
        settle(d)
        log.info("hop %d: %s -> %s %s money %d", hop, before, d.map_name(),
                 d.pos(), d.state.money())
        if d.map_name() == before:
            # Nothing moved. Being stuck in a sealed room with a live Champion
            # ahead means the fight is the exit; take it.
            log.info("stuck in %s -- fighting forward", before)
            try:
                guard(d, d.fight)
            except Exception as err:  # noqa: BLE001
                log.info("no battle to fight (%s)", type(err).__name__)
                break

    log.info("did not get out; still %s %s", d.map_name(), d.pos())
    d.save(out)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hops", type=int, default=30)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(a.state, a.out, a.hops)


if __name__ == "__main__":
    raise SystemExit(main())
