"""tallow: Route 27 (east of Tohjo) -> Route 26 heal house -> Victory Road -> Indigo Plateau PC.

    .venv/bin/python scripts/tallow_league_road.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, travel, live_walk, heal_at

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]


def leg(m):
    ok = travel(d, m)
    log.info("[leg %s] %s %s %s", m, ok, d.map_name(), d.pos()[2:])
    assert ok, (m, d.map_name(), d.pos()[2:])
    d.save(force=True)


if d.map_name() == "ROUTE_27":
    leg("ROUTE_26")
if d.map_name() == "ROUTE_26" and d.pos()[3] > 60:
    leg("ROUTE_26_HEAL_HOUSE")
    d.talk_to(2, 3); settle_dialog(d); d.settle()
    log.info("healed %s", party()); d.save(force=True)
    leg("ROUTE_26")
if d.map_name() == "ROUTE_26":
    leg("VICTORY_ROAD_GATE"); leg("VICTORY_ROAD")
if d.map_name() == "VICTORY_ROAD":
    # static grid lies here: live-grid walk to each ladder in turn
    for tgt, warp in [((1, 50), (1, 49)), ((13, 30), (13, 31)), ((13, 6), (13, 5))]:
        if d.pos()[3] < tgt[1] - 5 and warp != (13, 5):
            continue
        for _ in range(8):
            if live_walk(d, tgt):
                break
        r = d.take_warp(*warp); d.settle(); settle_dialog(d)
        log.info("ladder %s -> %s %s %s", warp, r, d.map_name(), d.pos()[2:])
        d.save(force=True)
    log.info("party %s", party())
if d.map_name() == "ROUTE_23":
    leg("INDIGO_PLATEAU_POKECENTER_1F")
    heal_at(d, "INDIGO_PLATEAU_POKECENTER_1F")
    d.save("tallow-indigo.state", force=True); d.save(force=True)
    log.info("party %s moves %s", party(), [(m["nick"], m["moves"]) for m in d.observe()["party"]])
