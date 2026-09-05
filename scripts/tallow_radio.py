"""tallow: Goldenrod Radio Tower, staged. argv[2] = stage.
  0: tower 1F..5F, fake director (Petrel) -> BASEMENT KEY
  1: Underground: basement door -> switch room -> warehouse -> Director -> CARD KEY
  2: tower 3F card-key door -> 4F -> 5F Archer -> CLEAR BELL (EVENT_CLEARED_RADIO_TOWER)

    .venv/bin/python scripts/tallow_radio.py saves/tallow.state [stage]
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, tactics_policy)

d = boot(sys.argv[1])
stage = int(sys.argv[2]) if len(sys.argv) > 2 else 0
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
flag = d._event_flag
d.trip_scenes = True
set_lead(d, "CRUST", "EMBER")
d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=4, hp_floor=0.35)


def status(tag):
    log.info("[%s] %s %s party %s bag %s", tag, d.map_name(), d.pos()[2:], party(), d.observe()["bag"])


def go(x, y, label=""):
    ok = d.goto(x, y, label); settle_dialog(d)
    if d.battle():
        d.fight()
    log.info("  goto (%d,%d) -> %s %s at %s", x, y, ok, d.last_goto_reason or "", d.pos()[2:])
    return ok


def talk(x, y, **kw):
    r = d.talk_to(x, y, **kw); settle_dialog(d)
    for _ in range(3):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    return r


def leg(m):
    ok = travel(d, m)
    assert ok, (m, d.map_name(), d.pos()[2:])
    p = d.observe()["party"]
    if p[0]["hp"] < p[0]["max_hp"] * 0.4 or sum(1 for x in p if x["hp"] <= 0) >= 2:
        d.heal_party() or heal_at(d, "GOLDENROD_POKECENTER_1F")
    status(m)


def rest():
    heal_at(d, "GOLDENROD_POKECENTER_1F")


if stage <= 0:
    rest()
    for m in ["GOLDENROD_CITY", "RADIO_TOWER_1F", "RADIO_TOWER_2F", "RADIO_TOWER_3F", "RADIO_TOWER_4F", "RADIO_TOWER_5F"]:
        leg(m)
    save_clean(d, "tallow-radio-5f.state")
    go(1, 3); go(0, 3)                       # FakeDirectorScript coord -> Petrel
    for _ in range(4):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    status("after fake director")
    assert "BASEMENTKEY" in d.observe()["bag"], d.observe()["bag"]
    save_clean(d, "tallow-radio-key.state")

if stage <= 1:
    if not (d.map_name() == "GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES" and d.pos()[3] <= 5):
        if d.map_name() == "GOLDENROD_CITY":
            assert d.take_warp(9, 5), d.last_warp_reason           # north stairs (the (11,29) side is sealed by a grunt)
        if d.map_name() == "GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES" and d.pos()[3] > 20:
            assert d.take_warp(21, 25), d.last_warp_reason
        assert d.map_name() == "GOLDENROD_UNDERGROUND", d.map_name()
        status("underground tunnel")
        if not flag("EVENT_USED_BASEMENT_KEY"):
            go(18, 7); d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
            log.info("basement door: %s", flag("EVENT_USED_BASEMENT_KEY"))
        d.sync_grid()
        go(18, 7)
        for _ in range(3):
            d.step_hold("U"); d.settle(); settle_dialog(d)
            if d.pos()[3] > 20:
                break
        status("past the basement door")
        assert d.pos()[3] > 20, "the (18,6) door warp did not fire"
        go(22, 28); assert d.take_warp(22, 27), d.last_warp_reason
        assert d.map_name() == "GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES", d.map_name()
        d.sync_grid()
        save_clean(d, "tallow-underground.state"); save_clean(d)
        status("switch corridor")
        log.info("%s", d.map_view())

    # position 6 (all three switches ON) opens doors 6/8/9/11: corridor -> (6,6) -> (6,8) -> row 9 -> (18,10) -> warehouse
    for sx, ev in [(16, "EVENT_SWITCH_1"), (10, "EVENT_SWITCH_2"), (2, "EVENT_SWITCH_3")]:
        if not flag(ev):
            go(sx, 2); d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
            log.info("switch %s -> %s (pos %d)", ev, flag(ev), d.emu.read_u8("wUndergroundSwitchPositions"))
    # door 5 (10,10) only opens in position 5 and survives the move to 6: switch 1 off, then on again
    for _ in range(2):
        go(16, 2); d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
        log.info("switch 1 -> %s (pos %d)", flag("EVENT_SWITCH_1"), d.emu.read_u8("wUndergroundSwitchPositions"))
    d.sync_grid()
    save_clean(d)
    log.info("%s", d.map_view())
    go(22, 11)
    assert d.take_warp(22, 10), d.last_warp_reason
    assert d.map_name() == "GOLDENROD_UNDERGROUND_WAREHOUSE", d.map_name()
    save_clean(d, "tallow-warehouse.state"); save_clean(d)
    status("warehouse")
    # grunts (9,8) (8,15) (14,3), Director (12,8) -> CARD KEY
    for tx, ty in [(14, 3), (9, 8), (8, 15)]:
        talk(tx, ty)
    for _ in range(3):
        cell = d.sprite_cell("SPRITE_GENTLEMAN") or (12, 8)
        talk(*cell)
        if "CARDKEY" in d.observe()["bag"]:
            break
    status("director")
    assert "CARDKEY" in d.observe()["bag"], d.observe()["bag"]
    save_clean(d, "tallow-cardkey.state"); save_clean(d)

if stage <= 2:
    # out via the Dept Store basement door (17,2), heal, back up the tower
    if d.map_name() == "GOLDENROD_UNDERGROUND_WAREHOUSE":
        # back the way we came (the dept-store elevator is menu-driven)
        go(2, 11); assert d.take_warp(2, 12), d.last_warp_reason
    if d.map_name() == "GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES" and 9 <= d.pos()[3] <= 13:
        # the Director resets the switches: the emergency switch (20,11) reopens 3/5/6/8/9/11
        go(20, 12); d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
        log.info("emergency switch: pos %d", d.emu.read_u8("wUndergroundSwitchPositions"))
        d.sync_grid(); go(22, 4); assert d.take_warp(23, 3), d.last_warp_reason
        d.sync_grid(); go(21, 30); d.step_hold("D"); d.settle()
        assert d.pos()[3] < 20, d.pos()
        go(3, 3); assert d.take_warp(3, 2), d.last_warp_reason
        assert d.take_warp(20, 29), d.last_warp_reason
        status("back in the city")
    rest()
    for m in ["GOLDENROD_CITY", "RADIO_TOWER_1F", "RADIO_TOWER_2F", "RADIO_TOWER_3F"]:
        leg(m)
    if not flag("EVENT_USED_THE_CARD_KEY_IN_THE_RADIO_TOWER"):
        go(14, 3); d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
        log.info("card key used: %s", flag("EVENT_USED_THE_CARD_KEY_IN_THE_RADIO_TOWER"))
    d.sync_grid()
    go(17, 1); assert d.take_warp(17, 0), d.last_warp_reason      # 3F (17,0) -> 4F (17,0)
    status("4F east")
    save_clean(d, "tallow-radio-4f.state"); save_clean(d)
    log.info("4F tiles: %s exits %s", {c: d.tile_at(*c) for c in [(12,0),(12,1),(11,0),(13,0)]}, d.exits())
    go(12, 1)
    for _ in range(4):
        if d.take_warp(12, 0) or d.map_name() == "RADIO_TOWER_5F":
            break
        log.info("  take_warp (12,0): %s at %s", d.last_warp_reason, d.pos()[2:])
        d.step_hold("U"); d.settle(); settle_dialog(d)
    assert d.map_name() == "RADIO_TOWER_5F", (d.map_name(), d.pos()[2:])
    status("5F east")
    save_clean(d, "tallow-pre-archer.state")
    go(16, 6); go(16, 5)                                          # RocketBoss coord -> Archer
    for _ in range(4):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    d.drain_scene(); d.settle(); settle_dialog(d)
    status("after archer")
    log.info("cleared radio tower: %s", flag("EVENT_CLEARED_RADIO_TOWER"))
    save_clean(d, "tallow-radio-cleared.state"); save_clean(d)
