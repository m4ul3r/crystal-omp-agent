"""tallow: Goldenrod -> Squirtbottle -> Route 35/36 (Sudowoodo) -> Route 37 -> Ecruteak.

    .venv/bin/python scripts/tallow_ecruteak.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead)

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
set_lead(d, "CRUMB")
d.default_policy = trainee_policy(d, "CRUMB", "EMBER", margin=3, hp_floor=0.4)


def leg(m):
    assert travel(d, m), (m, d.map_name())
    settle_dialog(d)
    p = d.observe()["party"]
    log.info("[leg] %s %s party %s", m, d.pos()[2:], party())
    return p


# 1) Squirtbottle
def north():
    for m in ["GOLDENROD_CITY", "ROUTE_35", "ROUTE_36"]:
        if m == "ROUTE_36" and d.map_name() == "ROUTE_35" and (17, 6) in d.find_tiles("cut-tree"):
            log.info("cut (17,6): %s %s", d.cut(17, 6), d.last_field_reason)
        p = leg(m)
        if p[0]["hp"] < p[0]["max_hp"] * 0.4 or any(x["hp"] <= 0 for x in p if not x.get("egg")):
            heal_at(d, "GOLDENROD_POKECENTER_1F")
            set_lead(d, "CRUMB")
            leg("GOLDENROD_CITY"); leg("ROUTE_35"); leg("ROUTE_36")


# 1) Squirtbottle needs: meet Floria on Route 36 (33,12), talk to her at the shop, then the teacher
if "SQUIRTBOTTLE" not in d.observe()["bag"]:
    if not d._event_flag("EVENT_MET_FLORIA"):
        north()
        d.talk_to(33, 12); settle_dialog(d)
        log.info("met floria: %s", d._event_flag("EVENT_MET_FLORIA"))
        save_clean(d)
    for m in ["ROUTE_35", "GOLDENROD_CITY", "GOLDENROD_FLOWER_SHOP"]:
        leg(m)
    for _ in range(4):
        cell = d.sprite_cell("SPRITE_LASS")
        log.info("floria at %s", cell)
        r = d.talk_to(*(cell or (5, 6))); settle_dialog(d)
        if d._event_flag("EVENT_TALKED_TO_FLORIA_AT_FLOWER_SHOP"):
            break
    d.talk_to(2, 4); settle_dialog(d)
    log.info("bag %s", d.observe()["bag"])
    assert "SQUIRTBOTTLE" in d.observe()["bag"], d.observe()["bag"]
    save_clean(d)

# 2) north through Route 35 to Route 36
north()
# 3) Sudowoodo at (35,9): talk with the bottle -> YES -> wild L20 SUDOWOODO (KO it; CRUST leads)
if not d._event_flag("EVENT_ROUTE_36_SUDOWOODO"):
    set_lead(d, "CRUST")
    d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=3, hp_floor=0.4)
    r = d.talk_to(35, 9)
    log.info("sudowoodo talk -> %s", r)
    for _ in range(6):
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight(); break
    log.info("sudowoodo gone=%s party %s", d._event_flag("EVENT_ROUTE_36_SUDOWOODO"), party())
    save_clean(d)

# 4) on to Ecruteak
set_lead(d, "CRUMB")
d.default_policy = trainee_policy(d, "CRUMB", "EMBER", margin=3, hp_floor=0.4)
for m in ["ROUTE_37", "ECRUTEAK_CITY"]:
    leg(m)
heal_at(d, "ECRUTEAK_POKECENTER_1F")
save_clean(d, "tallow-ecruteak.state")
log.info("party %s money %s bag %s", party(), d.observe()["money"], d.observe()["bag"])
