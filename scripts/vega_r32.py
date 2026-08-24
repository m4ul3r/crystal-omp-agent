"""Walk Route 32 south to the Union Cave entrance in goto chunks."""
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


# find cave warp coords from the repo map data
import re
p = Path(d.nav.repo_root) / "maps" / "UnionCave1F.asm" if hasattr(d.nav, "repo_root") else None
import crystalagent.paths as paths
txt = (paths.REPO_ROOT / "maps" / "Route32.asm").read_text()
warps = re.findall(r"warp_event\s+(\d+),\s*(\d+),\s*UNION_CAVE_1F", txt)
log.info("cave warps on R32: %s", warps)
tx, ty = int(warps[0][0]), int(warps[0][1])

# leave the Pokécenter / cross out of town on foot
if "POKECENTER" in d.map_name():
    d.goto(3, 6)
    d.press("D:80 .:100")
    d.settle()
    drain(60)
for i in range(6):
    if d.map_name().startswith("ROUTE_32"):
        break
    d.goto(15, 2)
    if not d.map_name().startswith("ROUTE_32"):
        d.press(".:60")

x, y = d.pos()[2:]
while d.map_name() == "ROUTE_32":
    x, y = d.pos()[2:]
    if abs(y - ty) < 4 and abs(x - tx) < 3:
        break
    ny = min(ty + 2, y + 8)
    done = False
    for cand in [(x, ny), (tx, ny), (x - 3, ny), (x + 3, ny), (x, y + 4)]:
        try:
            d.goto(*cand)
            done = True
            break
        except RuntimeError:
            if d.battle():
                d.fight()
                drain(150)
    if not done:
        r = d.step_dir("D") if y < ty else d.step_dir("L")
        drain(80)
    if d.battle():
        d.fight()
        drain(120)
    log.info("progress %s %s", d.pos()[2:], f"target {tx},{ty}")

d.settle()
drain(100)
log.info("now: %s %s want %s,%s", d.map_name(), d.pos()[2:], tx, ty)
d.save()
log.info("saved")
