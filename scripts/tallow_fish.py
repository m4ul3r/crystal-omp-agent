"""tallow: box LADLE, fish the Ecruteak pond with the GOOD ROD until BRINE (Poliwag) is caught.

    .venv/bin/python scripts/tallow_fish.py saves/tallow.state [casts]
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                set_lead, tactics_policy, STEP_OF)

d = boot(sys.argv[1])
casts = int(sys.argv[2]) if len(sys.argv) > 2 else 60
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]

if "LADLE" in {m["nick"] for m in d.observe()["party"]}:
    assert travel(d, "ECRUTEAK_POKECENTER_1F"), d.map_name()
    log.info("deposit LADLE: %s %s", d.deposit("LADLE"), d.last_pc_reason)
    save_clean(d)
set_lead(d, "EMBER", "CRUMB")
d.default_policy = tactics_policy(d)
assert travel(d, "ECRUTEAK_CITY"), d.map_name()

water = set(d.find_tiles("water"))
floor = set(d.find_tiles("floor"))
spots = [((x, y), f) for (x, y) in water for f, (dx, dy) in STEP_OF.items()
         if (x - dx, y - dy) in floor]
here = d.pos()[2:]
(wx, wy), facing = min(spots, key=lambda s: abs(s[0][0]-here[0]) + abs(s[0][1]-here[1]))
stand = (wx - STEP_OF[facing][0], wy - STEP_OF[facing][1])
log.info("fishing spot: stand %s face %s at water %s", stand, facing, (wx, wy))
assert d.goto(*stand), d.last_goto_reason

for cast in range(casts):
    d.step_dir(facing)                       # blocked step = turn to face the water
    ok = d.use_item("GOOD ROD")
    log.info("cast %d: use_item=%s (%s)", cast, ok, d.last_item_reason)
    bite = False
    for _ in range(12):
        d.press(".:20")
        rows = "\n".join(d.emu.screen_text())
        if "bite" in rows.lower():
            bite = True
            d.press("A:4 .:30")
            break
        if "nibble" in rows.lower():
            break
        if d.battle():
            bite = True
            break
    settle_dialog(d)
    for _ in range(4):
        if d.battle():
            d.fight()
            break
        d.press(".:20")
    settle_dialog(d)
    names = {m["nick"] for m in d.observe()["party"]}
    log.info("  bite=%s party=%s", bite, sorted(names))
    if "BRINE" in names:
        log.info("BRINE caught after %d casts", cast + 1)
        break
    d.close_menus()
else:
    raise SystemExit("no BRINE after %d casts" % casts)
heal_at(d, "ECRUTEAK_POKECENTER_1F")
save_clean(d, "tallow-brine.state"); save_clean(d)
log.info("party %s", party())
