"""Route 29 -> Cherrygrove, probe-stepping west, SAVING every few steps."""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")
import trek
from crystalagent.state import game_state

d = trek.Driver("saves/vega.state")


def drain(rounds=200):
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


def step(dirs):
    for dr in dirs:
        r = d.step_dir(dr)
        if r in ("moved", "warp"):
            return True
        if d.battle():
            d.fight()
    return False


if not d.map_name().startswith("ROUTE_29"):
    d.goto(0, 8)
    d.press("L:60 .:80")
    d.settle()
steps = 0
last_x = 99
stall = 0
while d.map_name().startswith("ROUTE_29"):
    g, n, x, y = d.pos()
    if x <= 1:
        d.press("L:60 .:80")
        d.settle()
        continue
    dirs = ["L", "L"]
    if y < 5:
        dirs = ["D", "L", "D"]
    elif y > 8:
        dirs = ["U", "L", "U"]
    if not step(dirs):
        step(["D", "U", "R"])   # wiggle around obstacles/NPCs
    drain(50)
    if d.battle():
        d.fight()
    nx = d.pos()[2]
    if nx == last_x:
        stall += 1
        if stall % 6 == 0:
            d.save()             # keep timeline warm even while stalled
        if stall % 10 == 5:
            d.press(".:200")     # let pacing NPCs drift off the cell
        if stall > 60:
            log.info("stalled at %s %s; saving and stopping", d.map_name(), (nx, d.pos()[3]))
            break
    else:
        stall = 0
        last_x = nx
    steps += 1
    if steps % 12 == 0:
        d.save()
        log.info("progress %d,%d", nx, d.pos()[3])

d.settle()
drain(100)
log.info("now: %s %s", d.map_name(), d.pos()[2:])
gs = game_state(d.emu, d.names)
log.info("%s", [(m["species"], m["level"], m["hp"], m["max_hp"]) for m in gs["party"]])
d.save()
log.info("saved")
