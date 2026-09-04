"""tallow: HM04 STRENGTH (Olivine cafe) -> surf Route 40/41 -> Cianwood: FLY (Chuck's wife),
Secret Potion (pharmacy), then Chuck (tallow_gym.py).

    .venv/bin/python scripts/tallow_cianwood.py saves/tallow.state
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
set_lead(d, "CRUST", "EMBER")
d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=4, hp_floor=0.35)

if "HM04" not in d.observe()["bag"]:
    assert travel(d, "OLIVINE_CITY") and travel(d, "OLIVINE_CAFE"), d.map_name()
    d.talk_to(4, 3); settle_dialog(d)
    log.info("bag %s", d.observe()["bag"])
    assert "HM04" in d.observe()["bag"], d.observe()["bag"]
if not d.field_moves().get("STRENGTH"):
    log.info("teach STRENGTH -> CRUST: %s %s", d.teach_tm("HM04", "CRUST", forget="SELFDESTRUCT"), d.last_tm_reason)
log.info("field moves %s", d.field_moves())
save_clean(d)

for m in ["OLIVINE_CITY", "ROUTE_40", "ROUTE_41", "CIANWOOD_CITY"]:
    assert travel(d, m), (m, d.map_name())
    p = d.observe()["party"]
    log.info("[leg] %s %s %s", m, d.pos()[2:], party())
heal_at(d, "CIANWOOD_POKECENTER_1F")
save_clean(d, "tallow-cianwood.state")

# HM02 FLY from Chuck's wife (walks L/R around (10,46))
assert travel(d, "CIANWOOD_CITY"), d.map_name()
for _ in range(4):
    cell = d.sprite_cell("SPRITE_POKEFAN_F") or (10, 46)
    d.talk_to(*cell); settle_dialog(d)
    if "HM02" in d.observe()["bag"]:
        break
log.info("bag %s", d.observe()["bag"])
assert "HM02" in d.observe()["bag"], d.observe()["bag"]
log.info("teach FLY -> FLOUR: %s %s", d.teach_tm("HM02", "FLOUR", forget="WHIRLWIND"), d.last_tm_reason)
log.info("field moves %s", d.field_moves())
save_clean(d, "tallow-hm02.state")

# Secret Potion for Jasmine's Ampharos
assert travel(d, "CIANWOOD_PHARMACY"), d.map_name()
d.talk_to(2, 3); settle_dialog(d)
log.info("bag %s", d.observe()["bag"])
assert travel(d, "CIANWOOD_CITY"), d.map_name()
heal_at(d, "CIANWOOD_POKECENTER_1F")
save_clean(d)
log.info("party %s money %s", party(), d.observe()["money"])
