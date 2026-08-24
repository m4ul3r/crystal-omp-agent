"""Handle the Route 30 Joey roadblock battle, then continue north."""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")
import trek
from crystalagent.state import game_state

d = trek.Driver("saves/vega.state")


def drain(rounds=250):
    q = 0
    for _ in range(rounds):
        d.press(".:40")
        if d.keyboard_open():
            d.press("A:6 .:60")
            q = 0
            continue
        if d.textbox() or d.menu_open():
            q = 0
            d.press("A:6 .:60")
        else:
            q += 1
    return q


# leave the Pokécenter / cross out of town on foot
if "POKECENTER" in d.map_name():
    d.goto(3, 6)
    d.press("D:60 .:80")
    d.settle()
    drain(60)
if d.map_name() == "CHERRYGROVE_CITY":
    d.goto(16, 0)
    d.press("U:60 .:80")
    d.settle()
    drain(60)

# get to the blocker and talk it through into a fight
d.goto(5, 24)
d.press("A:4 .:60")
r = drain(100)
log.info("pre-battle drain: %s", r)
for i in range(20):
    if d.battle():
        break
    d.press("A:6 .:80")
if d.battle():
    log.info("fighting Joey's pair")
    d.fight()
drain(200)
d.settle()
gs = game_state(d.emu, d.names)
log.info("after: %s %s hp=%s", d.map_name(), d.pos()[2:],
         [(m["hp"], m["max_hp"]) for m in gs["party"]])
d.save()
log.info("saved")
