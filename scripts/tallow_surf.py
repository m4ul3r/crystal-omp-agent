"""tallow: Kimono girls -> HM03 SURF; Route 38/39 -> Olivine; GOOD ROD.

    .venv/bin/python scripts/tallow_surf.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, tactics_policy)

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
set_lead(d, "EMBER", "CRUMB")          # kimono girls: eeveelutions; EMBER 25 needs the exp most
d.default_policy = trainee_policy(d, "EMBER", "CRUMB", margin=4, hp_floor=0.35)

heal_at(d, "ECRUTEAK_POKECENTER_1F")
assert travel(d, "ECRUTEAK_CITY") and travel(d, "DANCE_THEATER"), d.map_name()
for tx, ty in [(0, 2), (2, 1), (6, 2), (9, 1), (11, 2)]:
    d.talk_to(tx, ty); settle_dialog(d)
    p = d.observe()["party"]
    log.info("kimono (%d,%d) -> %s", tx, ty, party())
    if p[0]["hp"] < p[0]["max_hp"] * 0.4 or any(m["hp"] <= 0 for m in p):
        heal_at(d, "ECRUTEAK_POKECENTER_1F")
        assert travel(d, "ECRUTEAK_CITY") and travel(d, "DANCE_THEATER")
d.talk_to(7, 10); settle_dialog(d)
log.info("bag %s", d.observe()["bag"])
assert "HM03" in d.observe()["bag"], d.observe()["bag"]
heal_at(d, "ECRUTEAK_POKECENTER_1F")
save_clean(d, "tallow-hm03.state")

# west: Route 38 -> 39 -> Olivine, Good Rod house (13,15) guru at (2,3)
for m in ["ECRUTEAK_CITY", "ROUTE_38_ECRUTEAK_GATE", "ROUTE_38", "ROUTE_39", "OLIVINE_CITY"]:
    assert travel(d, m), (m, d.map_name())
    p = d.observe()["party"]
    log.info("[leg] %s %s %s", m, d.pos()[2:], party())
    if p[0]["hp"] < p[0]["max_hp"] * 0.4 or any(x["hp"] <= 0 for x in p):
        heal_at(d, "ECRUTEAK_POKECENTER_1F" if m in ("ROUTE_38", "ROUTE_38_ECRUTEAK_GATE") else "OLIVINE_POKECENTER_1F")
heal_at(d, "OLIVINE_POKECENTER_1F")
assert travel(d, "OLIVINE_CITY") and travel(d, "OLIVINE_GOOD_ROD_HOUSE"), d.map_name()
d.talk_to(2, 3); settle_dialog(d)
log.info("bag %s", d.observe()["bag"])
save_clean(d, "tallow-olivine.state")
log.info("party %s money %s", party(), d.observe()["money"])
