"""tallow: Azalea -- 3 REPEL, Kurt, Slowpoke Well rockets, Kurt again. FLOUR leads
(trainee), EMBER anchors so it stays at the L19 ceiling.

    .venv/bin/python scripts/tallow_well.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead)

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"]) for m in d.observe()["party"]]
d.default_policy = trainee_policy(d, "FLOUR", "EMBER")

# 1) mart: 3 REPEL (persona: before any cave)
assert travel(d, "AZALEA_TOWN") and travel(d, "AZALEA_MART"), d.map_name()
d.mart_buy(1, 3, "REPEL", 3)
log.info("bag %s money %s", d.observe()["bag"], d.observe()["money"])
save_clean(d)

# 2) Kurt sends himself to the well
assert travel(d, "AZALEA_TOWN") and travel(d, "KURTS_HOUSE"), d.map_name()
d.talk_to(3, 2)
settle_dialog(d)
d.drain_scene()
save_clean(d)

# 3) the well: FLOUR leads
assert travel(d, "AZALEA_TOWN"), d.map_name()
set_lead(d, "FLOUR")
d.talk_to(31, 9)          # rocket guarding the well (if still there)
settle_dialog(d)
assert travel(d, "SLOWPOKE_WELL_B1F"), d.map_name()
save_clean(d, "tallow-well.state")
for tx, ty in [(15, 7), (5, 2), (5, 6), (10, 4)]:
    log.info("rocket at (%d,%d); party %s", tx, ty, party())
    d.talk_to(tx, ty)
    settle_dialog(d)
    if any(m["hp"] <= 0 and not m.get("egg") for m in d.observe()["party"]):
        raise SystemExit(f"fainted in the well: {party()}")
    d.heal_party()
log.info("after rockets: %s bag %s", party(), d.observe()["bag"])
d.talk_to(16, 14)          # Kurt, carries you out
settle_dialog(d)
d.drain_scene()
d.settle()
log.info("now at %s %s", d.map_name(), d.pos()[2:])
save_clean(d)
if d.map_name() != "KURTS_HOUSE":
    assert travel(d, "AZALEA_TOWN") and travel(d, "KURTS_HOUSE"), d.map_name()
for o in d.map_objects():
    if o.get("sprite") == "SPRITE_KURT" and not o.get("masked"):
        d.talk_to(o["x"], o["y"])
        settle_dialog(d)
log.info("bag after Kurt: %s", d.observe()["bag"])
heal_at(d, "AZALEA_POKECENTER_1F") if travel(d, "AZALEA_TOWN") else None
save_clean(d, "tallow-well-done.state")
log.info("party %s money %s", party(), d.observe()["money"])
