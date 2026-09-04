"""tallow: Burned Tower -- rival 3 at (11,9) (fork first), drop to B1F, beasts scene, out.

    .venv/bin/python scripts/tallow_burned.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, matchup_policy)

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]

heal_at(d, "ECRUTEAK_POKECENTER_1F")
set_lead(d, "EMBER")
d.default_policy = matchup_policy(d, {"HAUNTER": "EMBER", "CROCONAW": "CRUMB", "MAGNEMITE": "CRUST", "ZUBAT": "FLOUR"})
save_clean(d, "tallow-pre-rival3.state")
assert travel(d, "ECRUTEAK_CITY") and travel(d, "BURNED_TOWER_1F"), d.map_name()
settle_dialog(d)
# the rival scene sits on (11,9); walk onto it deliberately
d.goto(11, 9)
for _ in range(6):
    d.settle(); settle_dialog(d)
    if d.battle():
        d.fight()
        break
settle_dialog(d); d.drain_scene(); d.settle()
log.info("rival3 beaten=%s party %s", d._event_flag("EVENT_RIVAL_BURNED_TOWER"), party())
log.info("last battle:\n%s", d.last_battle.summary())
save_clean(d)
# the floor hole at (10,9) drops to B1F (warp 3 -> (10,9) on B1F); beasts scene on (10,6)
if d.map_name() == "BURNED_TOWER_1F":
    d.take_warp(10, 9)
    log.info("after hole: %s %s (%s)", d.map_name(), d.pos()[2:], d.last_warp_reason)
if d.map_name() == "BURNED_TOWER_B1F":
    d.goto(10, 6)
    settle_dialog(d); d.drain_scene(); d.settle()
    log.info("beasts released=%s", d._event_flag("EVENT_RELEASED_THE_BEASTS"))
    save_clean(d)
    assert travel(d, "BURNED_TOWER_1F") and travel(d, "ECRUTEAK_CITY"), d.map_name()
heal_at(d, "ECRUTEAK_POKECENTER_1F")
save_clean(d, "tallow-burned.state")
log.info("party %s", party())
