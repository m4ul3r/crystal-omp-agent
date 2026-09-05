#!/usr/bin/env python
"""Catch the Victory Road species without solving Victory Road.

The dungeon gates the League EXIT, not its wildlife: encounter tables are
per-map, so standing anywhere on a floor rolls that floor's table. The parts
this run can already reach -- 1F's southern region, B1F's entrance pocket, and
B2F through the proven (30,25) crossing -- therefore yield ARON, MAKUHITA,
HARIYAMA, LAIRON, LOUDRED, WHISMUR, MEDITITE, MEDICHAM, MAWILE and SABLEYE.

Ten species, and the largest single block the dex is still missing. The
generic collector never gets here because it orders maps by species count and
Victory Road sits behind a waterfall climb that plain routing cannot express.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
import league_run  # noqa: E402
from collect import Collector  # noqa: E402

log = logging.getLogger("vrhunt")

FLOORS = ("VictoryRoad_1F", "VictoryRoad_B1F", "VictoryRoad_B2F")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=180.0)
    ap.add_argument("--per-map", type=float, default=900.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    # Publish to the SAME feed the widget reads: this sweep ran with
    # feed_name=None once and the picture on the desktop stayed on the
    # last frame of a process that had already exited, which reads as a
    # frozen game and is indistinguishable from one.
    c = Collector(d, per_map=a.per_map, feed_name="default")
    log.info("START %s", d.map_name())

    if not d.map_name().startswith("VictoryRoad"):
        if not league_run.to_city(d):
            log.info("could not reach Ever Grande")
            return 1
        if not league_run.climb(d):
            log.info("could not climb the falls")
            return 1
        league_run.heal_on_plateau(d)
        if not d.take_warp(*league_run.VICTORY_ROAD_DOOR):
            log.info("could not enter Victory Road")
            return 1
    log.info("INSIDE %s %s", d.map_name(), d.pos())

    c.run(budget_s=a.minutes * 60.0, max_maps=12, only=FLOORS)
    log.info("DONE at %s", d.map_name())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
