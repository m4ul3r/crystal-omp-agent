"""tallow: Ice Path after the boulders: drop through a pit -> B2F Mahogany side (slide to the
(9,11) ladder) -> B3F -> B2F Blackthorn side (slide to (3,15)) -> B1F south -> 1F -> Blackthorn.

    .venv/bin/python scripts/tallow_icepath_down.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, save_clean, travel, heal_at, slide_to

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]


def where(tag):
    log.info("[%s] %s %s", tag, d.map_name(), d.pos()[2:])


def step_into(x, y, mv):
    """Walk next to (x,y) and step onto it (pits/ladders)."""
    dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[mv]
    assert d.goto(x - dx, y - dy), d.last_goto_reason
    d.step_hold(mv); d.settle(); settle_dialog(d)
    if d.battle():
        d.fight()


if d.map_name() == "ICE_PATH_B1F":
    step_into(12, 13, "L")            # nearest pit from (14,13)
    where("fell")
assert d.map_name() == "ICE_PATH_B2F_MAHOGANY_SIDE", d.map_name()
save_clean(d, "tallow-icepath-b2f.state")
objs = {(o["x"], o["y"]) for o in d.map_objects() if not o.get("masked")}
log.info("objects here (walls for slides): %s", sorted(objs))
# ladder (9,11): reach a cell next to it, then step in
if not slide_to(d, (9, 10), avoid=objs):
    assert slide_to(d, (8, 11), avoid=objs) or slide_to(d, (10, 11), avoid=objs), "cannot reach the (9,11) ladder"
pos = d.pos()[2:]
mv = {(9, 10): "D", (8, 11): "R", (10, 11): "L"}[pos]
d.take_warp(9, 11) if False else (d.step_hold(mv), d.settle())
where("B3F?")
assert d.map_name() == "ICE_PATH_B3F", d.map_name()
assert travel(d, "ICE_PATH_B2F_BLACKTHORN_SIDE"), (d.map_name(), d.pos()[2:])
where("B2F blackthorn side")
save_clean(d, "tallow-icepath-b2fb.state")
objs = {(o["x"], o["y"]) for o in d.map_objects() if not o.get("masked")}
if not slide_to(d, (3, 14), avoid=objs):
    assert slide_to(d, (2, 15), avoid=objs) or slide_to(d, (4, 15), avoid=objs), "cannot reach the (3,15) ladder"
pos = d.pos()[2:]
mv = {(3, 14): "D", (2, 15): "R", (4, 15): "L"}[pos]
d.step_hold(mv); d.settle()
where("B1F south?")
assert d.map_name() == "ICE_PATH_B1F", d.map_name()
for m in ["ICE_PATH_1F", "BLACKTHORN_CITY"]:
    assert travel(d, m), (m, d.map_name(), d.pos()[2:])
    where(m)
heal_at(d, "BLACKTHORN_POKECENTER_1F")
save_clean(d, "tallow-blackthorn.state"); save_clean(d)
log.info("party %s money %s", party(), d.observe()["money"])
