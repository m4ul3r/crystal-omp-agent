"""tallow: Route 32 -> Union Cave (catch CRUST = Geodude on 1F) -> Route 33 -> Azalea.

    .venv/bin/python scripts/tallow_azalea.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, travel, save_clean, heal_at, catch_species

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"]) for m in d.observe()["party"]]

for m in ["VIOLET_CITY", "ROUTE_32", "UNION_CAVE_1F"]:
    assert travel(d, m), (m, d.map_name())
    log.info("[leg] %s %s %s money %s", m, d.pos()[2:], party(), d.observe()["money"])
    save_clean(d)

# Union Cave 1F: cave floor counts as encounter terrain; pace near the entrance
got = catch_species(d, {"GEODUDE": "CRUST"}, "UNION_CAVE_1F", "VIOLET_POKECENTER_1F",
                    heal_via=["VIOLET_CITY", "ROUTE_32"], rounds=30)
log.info("caught=%s party=%s", got, party())
save_clean(d, "tallow-cave.state")

for m in ["UNION_CAVE_B1F", "UNION_CAVE_1F", "ROUTE_33", "AZALEA_TOWN", "AZALEA_POKECENTER_1F"]:
    if m.endswith("POKECENTER_1F"):
        heal_at(d, m)
    else:
        assert travel(d, m), (m, d.map_name())
    log.info("[leg] %s %s %s money %s", m, d.pos()[2:], party(), d.observe()["money"])
    save_clean(d)
save_clean(d, "tallow-azalea.state")
