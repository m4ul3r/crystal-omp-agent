"""Escape the Route 24 west strip: surf north up the x4-6 channel into
Route 25, cross to the bridge column, descend to Cerulean. Saves at every
milestone.
"""
import sys
import time
sys.path.insert(0, "scripts")
sys.path.insert(0, ".")
from trek import Driver

d = Driver("saves/omp_speed_run.state")
e = d.emu
d.enable_surf()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def tap(mv, tries=5):
    for i in range(tries):
        p = d.pos()[2:]
        d.press(f"{mv}:30 .:150")
        if d.pos()[2:] != p:
            return True
        if d.battle():
            d.fight()
            return True
        d.press(".:140")
    return False


def mount(dir="R"):
    """Face `dir` at water, A, wait, A (YES)."""
    for i in range(4):
        if e.read_u8("wPlayerState") == 4:
            return True
        d.press(f"{dir}:30 .:150")
        d.press("A:22 .:260")
        txt = "".join(e.screen_text()).upper()
        if "CALM" in txt:
            d.press("A:15 .:220")
            d.press(".:250")
    return e.read_u8("wPlayerState") == 4


log("start", d.map_name(), d.pos())
# 1. mount surf heading east into the channel
if e.read_u8("wPlayerState") != 4:
    if not mount("R"):
        raise RuntimeError("could not mount surf")
log("mounted", d.pos())

# 2. ride north up the channel to the top
for i in range(16):
    if d.map_name() != "ROUTE_24":
        break
    if d.pos()[3] <= 1:
        break
    if not tap("U"):
        log("north blocked at", d.pos())
        break
log("channel top", d.map_name(), d.pos())
d.save()

# 3. transition into Route 25 (keep pressing U)
for i in range(6):
    if "ROUTE_25" in d.map_name():
        break
    tap("U")
log("r25", d.map_name(), d.pos())
d.save()

# 4. walk east/south to the bridge column (8-9, row 16) on R25
for i in range(200):
    if d.map_name() != "ROUTE_25":
        break
    p = d.pos()[2:]
    if p == (8, 16) or p == (9, 16) or p == (8, 15) or p == (9, 15):
        break
    mv = "D" if p[1] < 15 else ("L" if p[0] > 9 else ("R" if p[0] < 8 else "D"))
    if not tap(mv):
        for alt in ("U", "D", "L", "R"):
            if alt != mv and tap(alt):
                break
log("bridge approach", d.map_name(), d.pos())
d.save()

# 5. step south onto the ladder edge -> Route 24 bridge, then south to Cerulean
for i in range(40):
    if d.map_name() != "ROUTE_25":
        break
    tap("D")
log("r24 bridge", d.map_name(), d.pos())
d.save()
for i in range(40):
    if d.map_name() != "ROUTE_24":
        break
    if d.pos()[3] >= 17:
        break
    tap("D")
for i in range(6):
    if "CERULEAN" in d.map_name():
        break
    tap("D")
log("end", d.map_name(), d.pos())
if "CERULEAN" in d.map_name():
    d.save()
    log("[SAVED CERULEAN]")
else:
    d.save()
    log("[saved]", d.pos())
