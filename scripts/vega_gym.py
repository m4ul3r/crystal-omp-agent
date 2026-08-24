"""Violet Gym: fight through keepers, beat Falkner, save milestone."""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")
import trek
from crystalagent.state import game_state

d = trek.Driver("saves/vega.state")


def drain(n=200):
    q = 0
    for _ in range(n):
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


# leave the Pokécenter first
if "POKECENTER" in d.map_name():
    d.goto(3, 6)
    d.press("D:80 .:100")
    d.settle()
    drain(60)

# enter the gym from the city
if not d.map_name().startswith("VIOLET_GYM"):
    for i in range(8):
        if d.pos()[2] == (18, 17):
            break
        d.goto(18, 17)
        if d.pos()[2] != (18, 17):
            d.press(".:60")
    d.press("U:80 .:100")
    d.settle()
log.info("gym: %s %s", d.map_name(), d.pos()[2:])
d.save()

# walk north up the middle; sight-line keepers trigger on the way
for wp in [(5, 12), (5, 9), (5, 6), (5, 4), (5, 2)]:
    if d.battle():
        d.fight()
        drain(150)
    try:
        d.goto(*wp)
    except RuntimeError:
        pass
    if d.battle():
        d.fight()
        log.info("keeper beaten at wp %s", wp)
    drain(100)
    # Falkner stands at (5,1); when adjacent (5,2) face up and talk
    if d.pos()[2] == (5, 2):
        d.step_dir("U")
        d.press("A:4 .:60")
        for i in range(30):
            if d.battle():
                break
            d.press("A:6 .:80")

if d.battle():
    log.info("FALKNER FIGHT")
    d.fight()
drain(300)
d.settle()
gs = game_state(d.emu, d.names)
log.info("after: %s %s badges=%s", d.map_name(), d.pos()[2:],
         gs["player"]["johto_badges"])
log.info("%s", [(m["species"], m["level"], m["hp"], m["max_hp"]) for m in gs["party"]])
d.save()
if gs["player"]["johto_badges"]:
    d.save("vega-zephyr-badge.state")
    log.info("ZEPHYR BADGE saved")
