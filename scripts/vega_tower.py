"""Sprout Tower: fight sages on 2F+3F, beat Elder Li, get FLASH."""
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
            d.press("B:4 .:40")
            q = 0
            continue
        if d.textbox() or d.menu_open():
            q = 0
            d.press("A:6 .:60")
        else:
            q += 1
    return q


def goto_try(x, y, spread=3):
    for ty in range(y - spread, y + spread + 1):
        try:
            d.goto(x, ty)
            return True
        except RuntimeError:
            if d.battle():
                d.fight()
                drain(150)
    return False


def talk(x, y, facing=None):
    """Approach and battle a trainer NPC; returns after any fight."""
    try:
        d.talk_to(x, y, facing=facing)
    except Exception as ex:
        log.info("talk %s failed: %s", (x, y), str(ex)[:90])
    if d.battle():
        d.fight()
        log.info("won sage at %s", (x, y))
    drain(120)


if not d.map_name().startswith("SPROUT"):
    sys.exit("not in tower")

# 2F: Nico (12,3) then Edmond (9,14); stairs to 3F at (10,14)
goto_try(12, 4)
talk(12, 3)
goto_try(9, 13)
talk(9, 14)
d.save()
try:
    goto_try(10, 15)
except Exception:
    pass
for _ in range(3):
    d.press("D:80 .:100")
    d.settle()
    if d.map_name() == "SPROUT_TOWER_3F":
        break
log.info("on: %s %s", d.map_name(), d.pos()[2:])
d.save()

if d.map_name() == "SPROUT_TOWER_3F":
    # Neal (11,11), Troy (8,8), Jin (8,13) around the pillar; elder at (10,2)
    goto_try(11, 12, 2)
    talk(11, 11)
    goto_try(8, 9, 2)
    talk(8, 8)
    goto_try(8, 12, 2)
    talk(8, 13, facing="R")
    d.save()
    # Elder Li (10,2): approach from below
    for _ in range(4):
        try:
            d.goto(10, 3)
            break
        except RuntimeError:
            drain(120)
    d.step_dir("U")
    d.press("A:4 .:60")
    for i in range(30):
        if d.battle():
            break
        d.press("A:6 .:80")
    if d.battle():
        log.info("ELDER LI FIGHT")
        d.fight()
    drain(250)
    d.settle()
    gs = game_state(d.emu, d.names)
    log.info("after elder: %s %s", d.map_name(), d.pos()[2:])
    log.info("%s", [(m["species"], m["level"], m["hp"], m["max_hp"]) for m in gs["party"]])

d.save()
log.info("saved")
