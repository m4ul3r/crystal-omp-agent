"""tallow: Cianwood Gym boulder puzzle by hand (RUSTY's recipe), then Chuck.
Re-entering the gym resets the boulders. (4,3) is a wall: the middle boulder
must never be pushed north of row 7.

    .venv/bin/python scripts/tallow_chuck.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead)

d = boot(sys.argv[1])
e = d.emu
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]


def hold(mv, frames=70):
    e.py.button_press({"U": "up", "D": "down", "L": "left", "R": "right"}[mv])
    e.tick(frames)
    e.py.button_release({"U": "up", "D": "down", "L": "left", "R": "right"}[mv])
    e.tick(20)
    d.settle()
    if d.battle():
        d.fight()
    return d.pos()[2:], d.boulder_cells()


def push(mv, expect_boulder):
    for _ in range(3):
        pos, b = hold(mv, 90)
        log.info("  push %s -> pos %s boulders %s", mv, pos, sorted(b))
        if expect_boulder in b:
            return True
    raise SystemExit(f"boulder did not land on {expect_boulder}: {sorted(b)}")


def step(mv, expect):
    for _ in range(3):
        d.step_dir(mv)
        d.settle()
        if d.battle():
            d.fight()
        if d.pos()[2:] == expect:
            return
    raise SystemExit(f"step {mv}: at {d.pos()[2:]}, wanted {expect}")


heal_at(d, "CIANWOOD_POKECENTER_1F")
for _ in range(3):
    if set_lead(d, "FLOUR", "EMBER"):
        break
d.default_policy = trainee_policy(d, "FLOUR", "EMBER", margin=4, hp_floor=0.35)
save_clean(d, "tallow-pre-chuck.state")
assert travel(d, "CIANWOOD_CITY") and travel(d, "CIANWOOD_GYM"), d.map_name()
log.info("entered at %s boulders %s", d.pos()[2:], sorted(d.boulder_cells()))
assert d.goto(5, 9), d.last_goto_reason
log.info("boulders from (5,9): %s", sorted(d.boulder_cells()))
assert d.boulder_cells() >= {(3, 7), (4, 7), (5, 7)}, d.boulder_cells()
step("U", (5, 8))
# activate STRENGTH on the boulder ahead
d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.close_menus()
log.info("strength prompt answered; boulders %s", sorted(d.boulder_cells()))
push("U", (5, 6))                 # (5,7) -> (5,6)
if d.pos()[2:] == (5, 7):
    step("D", (5, 8))
step("L", (4, 8)); step("L", (3, 8))
push("U", (3, 6))                 # (3,7) -> (3,6)
if d.pos()[2:] != (3, 7):
    step("U", (3, 7))
push("R", (5, 7))                 # (4,7) -> (5,7); column 4 open
if d.pos()[2:] != (4, 7):
    step("R", (4, 7))
for y in (6, 5, 4):
    step("U", (4, y))
step("L", (3, 4)); step("U", (3, 3)); step("U", (3, 2)); step("R", (4, 2))
log.info("at %s, Chuck above; party %s", d.pos()[2:], party())
save_clean(d)
d.talk_to(4, 1, facing="U"); settle_dialog(d)
for _ in range(4):
    if d.battle():
        d.fight()
    d.settle(); settle_dialog(d)
obs = d.observe()
log.info("after Chuck: badges=%s bag=%s party=%s", obs["badges"], obs["bag"], party())
log.info("last battle:\n%s", d.last_battle.summary() if d.last_battle else None)
save_clean(d)
