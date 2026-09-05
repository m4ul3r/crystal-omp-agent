"""tallow: Dragon's Den after Clair: B1F live-grid walk, WHIRLPOOL at (10,19), surf to the shrine
(19,29) -> quiz -> Clair gives the RISING badge on the way out.

    .venv/bin/python scripts/tallow_den.py saves/tallow.state
"""
import logging, sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, save_clean, travel, heal_at, STEP_OF, live_walk

d = boot(sys.argv[1])
e = d.emu
party = lambda: [(m["nick"], m["level"], m["hp"]) for m in d.observe()["party"]]
WATER, BUOY, WHIRL, LAND = {0x29, 0x2A, 0x2B, 0x2C}, 0x27, 0x24, {0x00, 0x01}


def where(tag):
    log.info("[%s] %s %s", tag, d.map_name(), d.pos()[2:])


if "HM06" in d.observe()["bag"] and not d.field_moves().get("WHIRLPOOL"):
    log.info("teach WHIRLPOOL -> BRINE: %s %s", d.teach_tm("HM06", "BRINE", forget="WATER GUN"), d.last_tm_reason)
assert d.field_moves().get("WHIRLPOOL"), d.field_moves()

if d.map_name() != "DRAGONS_DEN_B1F":
    if d.map_name().startswith("BLACKTHORN_GYM"):
        if d.map_name() == "BLACKTHORN_GYM_1F" and d.pos()[3] < 9:
            d.sync_grid(); d.take_warp(7, 9); d.goto(1, 6); d.take_warp(1, 7); d.nav.clear_overrides("BLACKTHORN_GYM_1F")
        assert travel(d, "BLACKTHORN_CITY")
    heal_at(d, "BLACKTHORN_POKECENTER_1F")
    save_clean(d, "tallow-pre-den.state")
    for m in ["BLACKTHORN_CITY", "DRAGONS_DEN_1F", "DRAGONS_DEN_B1F"]:
        assert travel(d, m), (m, d.map_name(), d.pos()[2:])
        where(m)
save_clean(d)
g = d.live_grid()
for y in range(len(g)):
    log.info("%2d %s", y, "".join({0x00: '.', 0x01: '.', 0x07: '#', 0x29: '~', 0x27: 'b', 0x24: 'W'}.get(g[y][x], '%x' % (g[y][x] >> 4)) for x in range(len(g[0]))))
# to the whirlpool: stand on (10,18) facing D at (10,19)
for _ in range(4):
    if live_walk(d, (10, 19), lambda c: c in LAND or c in WATER):
        break
assert d.pos()[2:] == (10, 19), d.pos()[2:]
where("above whirlpool")
r = d.use_field_move("WHIRLPOOL", facing="D"); log.info("whirlpool: %s %s", r, d.last_field_reason)
d.settle(); settle_dialog(d)
for _ in range(4):
    if live_walk(d, (19, 30), lambda c: c in LAND or c in WATER or c == WHIRL):
        break
assert d.pos()[2:] == (19, 30), d.pos()[2:]
where("shrine door")
d.step_hold("U"); d.settle(); settle_dialog(d)
assert d.map_name() == "DRAGON_SHRINE", d.map_name()
d.save("tallow-shrine.state", force=True)
# the elder starts the quiz on entry; each question ends in a menu. Answers (RUSTY): 1,1,2,1,2
answers = [0, 0, 1, 0, 1]
for i, a in enumerate(answers):
    for _ in range(60):
        if d.menu_open() and any(g in "".join(d.emu.screen_text()) for g in "▶▷"):
            break
        d.press("A:4 .:20")
    log.info("q%d menu:\n%s", i + 1, "\n".join(r for r in d.emu.screen_text() if r.strip())[-400:])
    for _ in range(a):
        d.press("D:4 .:15")
    d.press("A:6 .:40")
for _ in range(40):
    if d.emu.read_u8("wScriptMode") == 0 and not d.textbox():
        break
    d.press("A:4 .:30")
log.info("after quiz: %s %s bag %s script=%s", d.map_name(), d.pos()[2:], d.observe()["bag"], d.emu.read_u8("wScriptMode"))
save_clean(d)
# out: Clair meets you at B1F (19,30) with the badge
d.take_warp(4, 9) if d.map_name() == "DRAGON_SHRINE" else None
d.settle(); settle_dialog(d); d.drain_scene(); d.settle(); settle_dialog(d)
obs = d.observe()
log.info("badges %s bag %s at %s %s", obs["badges"], obs["bag"], d.map_name(), d.pos()[2:])
save_clean(d)
