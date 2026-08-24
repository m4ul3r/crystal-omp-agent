"""Cross the R29 catch-tutorial seal (x~53) then BFS-chunk west."""
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


if not d.map_name().startswith("ROUTE_29"):
    d.goto(0, 8)
    d.press("L:60 .:80")
    d.settle()

# 1) blind-walk west across the tutorial coord cell with draining
while d.map_name().startswith("ROUTE_29") and d.pos()[2] > 45:
    r = d.step_dir("L")
    if d.battle():
        d.fight()
    drain(60)
    if r == "blocked":
        drain(150)
log.info("crossed seal? at %s %s", d.map_name(), d.pos()[2:])

# 2) now BFS should see the west side; chunk west
while d.map_name().startswith("ROUTE_29"):
    g, n, x, y = d.pos()
    if x <= 1:
        d.press("L:60 .:80")
        d.settle()
        continue
    target_x = max(1, x - 8)
    ok = False
    for ty in range(y - 2, y + 3):
        if ok:
            break
        try:
            d.goto(target_x, ty)
            ok = True
        except RuntimeError:
            if d.battle():
                d.fight()
            drain(120)
    if not ok:
        log.info("chunk to %d failed; manual step", target_x)
        d.step_dir("L")
        if d.battle():
            d.fight()
        drain(80)

d.settle()
drain(100)
log.info("now: %s %s", d.map_name(), d.pos()[2:])
gs = game_state(d.emu, d.names)
log.info("%s", [(m["species"], m["level"], m["hp"], m["max_hp"]) for m in gs["party"]])
d.save()
log.info("saved")
