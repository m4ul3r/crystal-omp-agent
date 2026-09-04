"""tallow: Olivine -> Ecruteak -> Route 42 -> Mahogany -> Route 43 -> Lake of Rage: red Gyarados (KO), Lance.

    .venv/bin/python scripts/tallow_lake.py saves/tallow.state
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
heal_at(d, "OLIVINE_POKECENTER_1F")
set_lead(d, "BRINE", "EMBER")          # BRINE 28 and SUGAR 28 need the Pryce floor (29)
d.default_policy = trainee_policy(d, "BRINE", "EMBER", margin=4, hp_floor=0.35)
money0 = d.observe()["money"]

CENTERS = {"ROUTE_39": "OLIVINE_POKECENTER_1F", "ROUTE_38": "ECRUTEAK_POKECENTER_1F",
           "ECRUTEAK_CITY": "ECRUTEAK_POKECENTER_1F", "ROUTE_42": "ECRUTEAK_POKECENTER_1F",
           "MAHOGANY_TOWN": "MAHOGANY_POKECENTER_1F", "ROUTE_43": "MAHOGANY_POKECENTER_1F",
           "LAKE_OF_RAGE": "MAHOGANY_POKECENTER_1F"}
for m in ["OLIVINE_CITY", "ROUTE_39", "ROUTE_38", "ECRUTEAK_CITY", "ROUTE_42", "MAHOGANY_TOWN",
          "ROUTE_43", "LAKE_OF_RAGE"]:
    assert travel(d, m), (m, d.map_name(), d.pos()[2:])
    p = d.observe()["party"]
    log.info("[leg] %s %s %s money %s", m, d.pos()[2:], party(), d.observe()["money"])
    if p[0]["hp"] < p[0]["max_hp"] * 0.4 or any(x["hp"] <= 0 for x in p):
        heal_at(d, CENTERS[m])
        assert travel(d, m), (m, d.map_name())
    if m == "MAHOGANY_TOWN":
        heal_at(d, "MAHOGANY_POKECENTER_1F")
        save_clean(d, "tallow-mahogany.state")
save_clean(d, "tallow-lake.state")

# red Gyarados at (18,22) on the water: surf next to it and talk (KO policy: not in the plan)
if not d._event_flag("EVENT_FOUGHT_RED_GYARADOS") if False else True:
    r = d.talk_to(18, 22)
    log.info("gyarados talk -> %s", r)
    for _ in range(6):
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight(); break
    settle_dialog(d)
    log.info("party %s bag %s", party(), d.observe()["bag"])
# Lance at (21,28) -> hideout briefing
d.talk_to(21, 28); settle_dialog(d); d.drain_scene(); d.settle()
log.info("lance done; at %s %s money delta %s", d.map_name(), d.pos()[2:], d.observe()["money"] - money0)
save_clean(d, "tallow-lance.state"); save_clean(d)
