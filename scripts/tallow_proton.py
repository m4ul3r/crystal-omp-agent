"""tallow: finish Slowpoke Well -- the grunt at (5,2) (west wing, reached via row 6).

    .venv/bin/python scripts/tallow_proton.py saves/tallow.state
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

assert travel(d, "AZALEA_TOWN") and travel(d, "SLOWPOKE_WELL_B1F"), d.map_name()
for cell in [(11, 6), (6, 6)]:
    ok = d.goto(*cell)
    log.info("goto %s -> %s %s pos=%s", cell, ok, d.last_goto_reason, d.pos()[2:])
    settle_dialog(d)
d.talk_to(5, 2)
settle_dialog(d)
d.drain_scene()
d.settle()
log.info("cleared=%s at %s %s party %s", d._event_flag("EVENT_CLEARED_SLOWPOKE_WELL"),
         d.map_name(), d.pos()[2:], party())
save_clean(d)
if d.map_name() != "KURTS_HOUSE":
    assert travel(d, "AZALEA_TOWN") and travel(d, "KURTS_HOUSE"), d.map_name()
for o in d.map_objects():
    if o.get("sprite") == "SPRITE_KURT" and not o.get("masked"):
        d.talk_to(o["x"], o["y"])
        settle_dialog(d)
log.info("bag after Kurt: %s", d.observe()["bag"])
assert travel(d, "AZALEA_TOWN")
heal_at(d, "AZALEA_POKECENTER_1F")
save_clean(d, "tallow-well-done.state")
log.info("party %s money %s", party(), d.observe()["money"])
