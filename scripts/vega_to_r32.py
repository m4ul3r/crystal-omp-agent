"""Violet PC -> Route 32 (south exit), handling the egg-hatch keyboard."""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")
import trek

d = trek.Driver("saves/vega.state")
NICK = sys.argv[1] if len(sys.argv) > 1 else "SPIKE"


def drain(n=250):
    q = 0
    for _ in range(n):
        d.press(".:40")
        if d.keyboard_open():
            log.info("naming keyboard -> DECLINING")
            for _ in range(8):
                if not d.keyboard_open(): break
                d.press("B:6 .:50")
                d.press("A:6 .:80")     # confirm "Cancel?"
                if not d.keyboard_open(): break
            q = 0
            continue
        if d.textbox() or d.menu_open():
            q = 0
            d.press("A:6 .:60")
        else:
            q += 1
    return q


# out of the PC
d.goto(3, 6)
d.press("D:80 .:100")
d.settle()
drain(80)
log.info("city: %s %s", d.map_name(), d.pos()[2:])

# row-21 corridor west to x=14
for tgt in [(18, 21), (14, 21)]:
    for i in range(4):
        d.goto(*tgt)
        if d.pos()[2:] == tgt:
            break
        drain(60)
log.info("mid: %s", d.pos()[2:])
drain(60)

# descend the x=13/14 pocket to the south edge, then into R32
stall = 0
while d.map_name().startswith("VIOLET_CITY"):
    moved = False
    for dr in ("D", "L", "R"):
        if d.step_dir(dr) == "moved":
            moved = True
            break
    if not moved:
        stall += 1
        drain(120)
        if stall > 5:
            log.info("stuck at %s", d.pos()[2:])
            break
    else:
        stall = 0
    if d.battle():
        d.fight()
        drain(120)

drain(100)
log.info("now: %s %s", d.map_name(), d.pos()[2:])
d.save()
log.info("saved")
