"""tallow: after Zephyr -- Togepi egg from Elm's aide, 3 REPEL, catch WHISK (Mareep) on Route 32.

    .venv/bin/python scripts/tallow_r32.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, travel, save_clean, heal_at, catch_species

d = boot(sys.argv[1])

# 1) leave the gym (Elm's call), aide in the Pokecenter hands the egg
assert travel(d, "VIOLET_CITY"), d.map_name()
save_clean(d)
assert travel(d, "VIOLET_POKECENTER_1F"), d.map_name()
objs = [o for o in d.map_objects() if o.get("sprite") == "SPRITE_SCIENTIST"]
log.info("aide objects: %s", objs)
d.talk_to(4, 3)
settle_dialog(d)
obs = d.observe()
log.info("party after aide: %s", [(m["nick"], m["level"], m.get("egg")) for m in obs["party"]])
d.heal()
save_clean(d)
# 2) REPEL is not stocked before Azalea (data/items/marts.asm: Violet/Cherrygrove
#    carry none) -- Union Cave is crossed without; buy 3 at Azalea for Ilex.
save_clean(d)

# 3) Route 32: WHISK
assert travel(d, "VIOLET_CITY") and travel(d, "ROUTE_32"), d.map_name()
got = catch_species(d, {"MAREEP": "WHISK"}, "ROUTE_32", "VIOLET_POKECENTER_1F",
                    heal_via=["VIOLET_CITY"])
log.info("caught=%s party=%s", got, [(m["nick"], m["level"], m["hp"]) for m in d.observe()["party"]])
heal_at(d, "VIOLET_POKECENTER_1F")
save_clean(d, "tallow-r32.state")
