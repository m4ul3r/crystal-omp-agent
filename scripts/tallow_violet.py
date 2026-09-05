"""tallow: leave the lab (aide's balls), buy 5 POKE BALL, catch FLOUR (Pidgey/
Hoothoot) on Route 30, reach Violet City and heal.

    .venv/bin/python scripts/tallow_violet.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, travel, save_clean, heal_at

WANT = {"PIDGEY": "FLOUR", "HOOTHOOT": "FLOUR"}

d = boot(sys.argv[1])

# 1) out of the lab: the aide's 5-ball scene sits on the exit cells
assert travel(d, "NEW_BARK_TOWN"), d.map_name()
save_clean(d)
log.info("bag leaving lab: %s", d.observe()["bag"])

# 2) Cherrygrove mart: exactly 5 POKE BALL (persona rule)
for m in ["ROUTE_29", "CHERRYGROVE_CITY", "CHERRYGROVE_MART"]:
    assert travel(d, m), (m, d.map_name())
    save_clean(d)
d.mart_buy(1, 3, "POKE BALL", 5)
obs = d.observe()
log.info("after mart: bag=%s money=%s", obs["bag"], obs["money"])
save_clean(d)

# 3) Route 30: pace the first grass belt until FLOUR is caught
assert travel(d, "CHERRYGROVE_CITY") and travel(d, "ROUTE_30"), d.map_name()
grass = d.find_tiles("grass")
xs = [c[0] for c in grass]; ys = [c[1] for c in grass]
log.info("route 30 grass: %d cells x%d-%d y%d-%d", len(grass), min(xs), max(xs), min(ys), max(ys))
# nearest belt to the south entrance
here = d.pos()[2:]
near = sorted(grass, key=lambda c: abs(c[0]-here[0]) + abs(c[1]-here[1]))[:12]
bx = (min(c[0] for c in near), max(c[0] for c in near),
      min(c[1] for c in near), max(c[1] for c in near))
d.goto(*near[0])
caught = False
for rnd in range(25):
    r = d.pace(40, box=bx)
    if r["stopped"] != "battle":
        log.info("pace stopped: %s", r)
        if r["stopped"] in ("warp", "whiteout"):
            break
        continue
    enemy = d.observe().get("enemy", {})
    sp = enemy.get("name")
    if sp in WANT and "FLOUR" not in [m["nick"] for m in d.observe()["party"] if m.get("nick")]:
        log.info("wild %s L%s -> catch as FLOUR", sp, enemy.get("level"))
        d.catch(nickname=WANT[sp], max_balls=2)
        names = [m.get("nick") for m in d.observe()["party"]]
        if "FLOUR" in names:
            caught = True
            log.info("caught FLOUR; party %s", names)
            break
        d.fight()   # second ball failed: persona says flee; fight() honours 'flee' below
    else:
        d.fight()
    lead = d.lead()
    if lead["hp"] < lead["max_hp"] * 0.4:
        log.info("lead low (%d/%d); healing at Cherrygrove", lead["hp"], lead["max_hp"])
        heal_at(d, "CHERRYGROVE_POKECENTER_1F")
        assert travel(d, "CHERRYGROVE_CITY") and travel(d, "ROUTE_30")
        d.goto(*near[0])
save_clean(d)
log.info("caught=%s party=%s", caught, [(m["nick"], m["level"]) for m in d.observe()["party"]])

# 4) on to Violet City
for m in ["ROUTE_31", "VIOLET_CITY", "VIOLET_POKECENTER_1F"]:
    if m.endswith("POKECENTER_1F"):
        heal_at(d, m)
    else:
        assert travel(d, m), (m, d.map_name())
    save_clean(d)
save_clean(d, "tallow-violet.state")
log.info("party: %s", [(m["nick"], m["level"], m["hp"]) for m in d.observe()["party"]])
