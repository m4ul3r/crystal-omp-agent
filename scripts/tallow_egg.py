"""tallow: Mr. Pokemon's egg + Oak's Pokedex, back to Elm (rival fight on the way).

    .venv/bin/python scripts/tallow_egg.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, travel, save_clean, heal_at

d = boot(sys.argv[1])
heal_at(d, "CHERRYGROVE_POKECENTER_1F")
d.save()
assert travel(d, "MR_POKEMONS_HOUSE"), d.map_name()
settle_dialog(d)       # entry: Mr. Pokemon hands the egg, Oak gives the Pokedex
d.drain_scene()
d.settle()
bag = d.observe()["bag"]
log.info("bag after visit: %s", bag)
save_clean(d, "tallow-egg.state")

# back to Elm: Route 30 -> Cherrygrove (rival ambush) -> Route 29 -> New Bark
for m in ["ROUTE_30", "CHERRYGROVE_CITY", "CHERRYGROVE_POKECENTER_1F",
          "CHERRYGROVE_CITY", "ROUTE_29", "NEW_BARK_TOWN", "ELMS_LAB"]:
    if m == "CHERRYGROVE_POKECENTER_1F":
        heal_at(d, m)
        continue
    if not travel(d, m):
        raise SystemExit(f"stuck at {d.map_name()} {d.pos()[2:]} en route to {m}")
    lead = d.lead()
    log.info("[leg] %s %s lead L%d %d/%d", d.map_name(), d.pos()[2:],
             lead["level"], lead["hp"], lead["max_hp"])
    save_clean(d)
d.talk_to(5, 2)
settle_dialog(d)
d.drain_scene()
d.settle()
log.info("bag at Elm: %s", d.observe()["bag"])
save_clean(d, "tallow-elm-egg.state")
