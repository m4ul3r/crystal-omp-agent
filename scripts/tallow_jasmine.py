"""tallow: lighthouse top -> Jasmine -> Cianwood SECRETPOTION -> Amphy -> Olivine Gym.

    .venv/bin/python scripts/tallow_jasmine.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, matchup_policy)

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
bag = lambda: d.observe()["bag"]
set_lead(d, "CRUST", "EMBER")
d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=4, hp_floor=0.35)


def warp_to(x, y, expect):
    for _ in range(3):
        ok = d.take_warp(x, y)
        d.settle(); settle_dialog(d)
        if d.battle():
            d.fight()
        if d.map_name() == expect:
            return
        log.info("  take_warp (%d,%d) -> %s (%s), on %s %s", x, y, ok, d.last_warp_reason,
                 d.map_name(), d.pos()[2:])
    raise SystemExit(f"could not reach {expect} via ({x},{y}); on {d.map_name()} {d.pos()[2:]}")


def lighthouse_top():
    """Stairs, not the elevator/holes: 1F(3,11) 2F(5,3) 3F(13,3) 4F drop (9,2)->(9,3) hole
    to 3F centre, 3F(9,5) 4F(9,7) 5F(9,15) 6F  (RUSTY's route)."""
    assert travel(d, "OLIVINE_CITY") and travel(d, "OLIVINE_LIGHTHOUSE_1F"), d.map_name()
    warp_to(3, 11, "OLIVINE_LIGHTHOUSE_2F")
    warp_to(5, 3, "OLIVINE_LIGHTHOUSE_3F")
    warp_to(13, 3, "OLIVINE_LIGHTHOUSE_4F")
    assert d.goto(9, 2), d.last_goto_reason
    d.step_hold("D"); d.settle(); settle_dialog(d)
    if d.battle():
        d.fight()
    assert d.map_name() == "OLIVINE_LIGHTHOUSE_3F", (d.map_name(), d.pos()[2:])
    warp_to(9, 5, "OLIVINE_LIGHTHOUSE_4F")
    warp_to(9, 7, "OLIVINE_LIGHTHOUSE_5F")
    warp_to(9, 15, "OLIVINE_LIGHTHOUSE_6F")
    log.info("lighthouse top %s party %s", d.pos()[2:], party())


def lighthouse_down():
    """Back to Olivine along the east (16,y) warp column: 6F(16,5) 5F(16,7) 4F(16,9) 3F(16,11) 2F(16,13) 1F."""
    for x, y, expect in [(16, 5, "OLIVINE_LIGHTHOUSE_5F"), (16, 7, "OLIVINE_LIGHTHOUSE_4F"),
                         (16, 9, "OLIVINE_LIGHTHOUSE_3F"), (16, 11, "OLIVINE_LIGHTHOUSE_2F"),
                         (16, 13, "OLIVINE_LIGHTHOUSE_1F")]:
        warp_to(x, y, expect)
    assert travel(d, "OLIVINE_CITY"), d.map_name()


if "SECRETPOTION" not in bag() and not d._event_flag("EVENT_JASMINE_EXPLAINED_AMPHYS_SICKNESS"):
    for m in ["CIANWOOD_CITY", "ROUTE_41", "ROUTE_40", "OLIVINE_CITY"]:
        assert travel(d, m), (m, d.map_name())
    heal_at(d, "OLIVINE_POKECENTER_1F")
    lighthouse_top()
    d.talk_to(8, 8); settle_dialog(d)
    log.info("explained=%s", d._event_flag("EVENT_JASMINE_EXPLAINED_AMPHYS_SICKNESS"))
    save_clean(d, "tallow-amphy.state")

if "SECRETPOTION" not in bag():
    if d.map_name().startswith("OLIVINE_LIGHTHOUSE"):
        lighthouse_down()
    for m in ["OLIVINE_CITY", "ROUTE_40", "ROUTE_41", "CIANWOOD_CITY", "CIANWOOD_PHARMACY"]:
        assert travel(d, m), (m, d.map_name(), d.pos()[2:])
    d.talk_to(2, 3); settle_dialog(d)
    log.info("bag %s", bag())
    assert "SECRETPOTION" in bag(), bag()
    save_clean(d, "tallow-potion.state")
    for m in ["CIANWOOD_CITY", "ROUTE_41", "ROUTE_40", "OLIVINE_CITY"]:
        assert travel(d, m), (m, d.map_name())
    heal_at(d, "OLIVINE_POKECENTER_1F")
    lighthouse_top()
    d.talk_to(8, 8); settle_dialog(d); d.drain_scene(); d.settle()
    log.info("after potion: bag %s at %s", bag(), d.map_name())
    save_clean(d)

# gym: Jasmine at (5,3), no trainers
if d.map_name().startswith("OLIVINE_LIGHTHOUSE"):
    lighthouse_down()
heal_at(d, "OLIVINE_POKECENTER_1F")
d.default_policy = matchup_policy(d, {"MAGNEMITE": "CRUST", "STEELIX": "BRINE"})
set_lead(d, "CRUST", "BRINE")
save_clean(d, "tallow-pre-jasmine.state")
assert travel(d, "OLIVINE_CITY") and travel(d, "OLIVINE_GYM"), d.map_name()
d.talk_to(5, 3); settle_dialog(d)
for _ in range(4):
    if d.battle():
        d.fight()
    d.settle(); settle_dialog(d)
obs = d.observe()
log.info("after Jasmine: badges=%s bag=%s party=%s", obs["badges"], obs["bag"], party())
log.info("last battle:\n%s", d.last_battle.summary() if d.last_battle else None)
save_clean(d)
