"""tallow: from anywhere in west Johto back to Blackthorn via Mahogany + Ice Path
(Route 45 is one-way downhill, so Blackthorn is only reachable through the Ice Path).

    .venv/bin/python scripts/tallow_to_blackthorn.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, slide_to)

d = boot(sys.argv[1])
d.nav.surf = False                      # Route 32's river tempts nav into a surf prompt it cannot answer
set_lead(d, "CRUST", "EMBER"); d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=4)
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]

ORDER = ["ROUTE_46", "ROUTE_29", "CHERRYGROVE_CITY", "ROUTE_30", "ROUTE_31", "VIOLET_CITY", "ROUTE_32", "UNION_CAVE_1F",
         "ROUTE_33", "AZALEA_TOWN", "ILEX_FOREST", "ROUTE_34", "GOLDENROD_CITY", "ROUTE_35", "ROUTE_36", "ROUTE_37",
         "ECRUTEAK_CITY", "ROUTE_42", "MAHOGANY_TOWN"]
CENTERS = {"VIOLET_CITY": "VIOLET_POKECENTER_1F", "AZALEA_TOWN": "AZALEA_POKECENTER_1F",
           "GOLDENROD_CITY": "GOLDENROD_POKECENTER_1F", "ECRUTEAK_CITY": "ECRUTEAK_POKECENTER_1F",
           "MAHOGANY_TOWN": "MAHOGANY_POKECENTER_1F"}
here = d.map_name()
start = ORDER.index(here) + 1 if here in ORDER else 0
for m in ORDER[start:]:
    if m == "ILEX_FOREST" and d.map_name() == "AZALEA_TOWN":
        pass
    d.nav.surf = m not in ("VIOLET_CITY", "ROUTE_32")   # only Route 32's river is a trap
    ok = travel(d, m)
    if not ok and m == "ROUTE_34" and d.map_name() == "ILEX_FOREST":
        d.goto(1, 6); d.step_hold("U"); d.settle(); settle_dialog(d)      # north gate needs the held step
        ok = travel(d, m)
    assert ok, (m, d.map_name(), d.pos()[2:])
    p = d.observe()["party"]
    log.info("[leg] %s %s %s", m, d.pos()[2:], party())
    if m in CENTERS or p[0]["hp"] < p[0]["max_hp"] * 0.4 or any(x["hp"] <= 0 for x in p):
        heal_at(d, CENTERS.get(m, "GOLDENROD_POKECENTER_1F"))
    save_clean(d)

# Ice Path
if d.map_name() == "MAHOGANY_POKECENTER_1F":
    assert travel(d, "MAHOGANY_TOWN")
d.nav.surf = True
assert travel(d, "ROUTE_44") and travel(d, "ICE_PATH_1F"), d.map_name()
if d.pos()[2:] == (15, 2) or d.pos()[3] < 9:
    assert slide_to(d, (16, 8)), "1F rink"
assert d.goto(37, 6), d.last_goto_reason
assert d.take_warp(37, 5) and d.map_name() == "ICE_PATH_B1F", d.map_name()
save_clean(d)
# nearest pit from the (17,2) landing is (11,2): step in from (11,3)... pits are entered from any side
assert d.goto(12, 2), d.last_goto_reason
d.step_hold("L"); d.settle(); settle_dialog(d)
assert d.map_name() == "ICE_PATH_B2F_MAHOGANY_SIDE", (d.map_name(), d.pos()[2:])
objs = {(o["x"], o["y"]) for o in d.map_objects() if not o.get("masked")}
if not slide_to(d, (9, 10), avoid=objs):
    assert slide_to(d, (8, 11), avoid=objs) or slide_to(d, (10, 11), avoid=objs), "ladder"
mv = {(9, 10): "D", (8, 11): "R", (10, 11): "L"}[d.pos()[2:]]
d.step_hold(mv); d.settle()
assert d.map_name() == "ICE_PATH_B3F", d.map_name()
assert travel(d, "ICE_PATH_B2F_BLACKTHORN_SIDE"), d.map_name()
objs = {(o["x"], o["y"]) for o in d.map_objects() if not o.get("masked")}
slide_to(d, (3, 14), avoid=objs)
if d.map_name() == "ICE_PATH_B2F_BLACKTHORN_SIDE":
    d.step_hold("D"); d.settle()
assert d.map_name() == "ICE_PATH_B1F", d.map_name()
for m in ["ICE_PATH_1F", "BLACKTHORN_CITY"]:
    assert travel(d, m), (m, d.map_name(), d.pos()[2:])
heal_at(d, "BLACKTHORN_POKECENTER_1F")
save_clean(d, "tallow-pre-clair.state"); save_clean(d)
log.info("party %s money %s", party(), d.observe()["money"])
