"""tallow: Mahogany Rocket hideout, stage by stage (argv[2] = stage to start at).

    .venv/bin/python scripts/tallow_hideout.py saves/tallow.state [stage]
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
d.trip_scenes = True                 # camera ambushes fire once each; walk them
if d.map_name().startswith("TEAM_ROCKET_BASE"):
    d.sync_grid()                    # opened doors are changeblocks re-applied by map callbacks


def status(tag):
    log.info("[%s] %s %s party %s bag %s", tag, d.map_name(), d.pos()[2:], party(), d.observe()["bag"])


def go(x, y, label=""):
    ok = d.goto(x, y, label)
    settle_dialog(d)
    if d.battle():
        d.fight()
    log.info("  goto (%d,%d) -> %s %s at %s", x, y, ok, d.last_goto_reason or "", d.pos()[2:])
    return ok


def talk(x, y, **kw):
    r = d.talk_to(x, y, **kw)
    settle_dialog(d)
    for _ in range(3):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    return r


def warp(x, y, expect):
    for _ in range(3):
        d.take_warp(x, y); d.settle(); settle_dialog(d)
        if d.battle():
            d.fight()
        if d.map_name() == expect:
            return True
    raise SystemExit(f"warp ({x},{y}) -> {expect} failed; at {d.map_name()} {d.pos()[2:]}")


def rest():
    """Heal at Mahogany and walk back down to the current floor's entry."""
    here = d.map_name()
    heal_at(d, "MAHOGANY_POKECENTER_1F")
    assert travel(d, "MAHOGANY_TOWN") and travel(d, "MAHOGANY_MART_1F"), d.map_name()
    warp(7, 3, "TEAM_ROCKET_BASE_B1F")
    if here != "TEAM_ROCKET_BASE_B1F":
        warp(3, 14, "TEAM_ROCKET_BASE_B2F")
    if here == "TEAM_ROCKET_BASE_B3F":
        go(27, 13); warp(27, 14, "TEAM_ROCKET_BASE_B3F")


def maybe_rest():
    p = d.observe()["party"]
    if p[0]["hp"] < p[0]["max_hp"] * 0.4 or sum(1 for m in p if m["hp"] <= 0) >= 2:
        rest()


set_lead(d, "EMBER", "FLOUR")
d.default_policy = tactics_policy(d)

if stage <= 0:
    heal_at(d, "MAHOGANY_POKECENTER_1F")
    assert travel(d, "MAHOGANY_TOWN") and travel(d, "MAHOGANY_MART_1F"), d.map_name()
    talk(4, 3)                                   # pharmacist / Lance scene opens the stairs
    status("mart")
    warp(7, 3, "TEAM_ROCKET_BASE_B1F")
    status("B1F")
    save_clean(d, "tallow-hideout-b1f.state")

if stage <= 1:
    # B1F: grunt (2,4) guards nothing we need; stairs at (3,14). Cameras on the way ambush.
    go(3, 13, "B1F stairs")
    maybe_rest()
    warp(3, 14, "TEAM_ROCKET_BASE_B2F")
    status("B2F")
    # Lance heals scene at (5,14)/(4,13); then down to B3F by the west stairs (3,2)
    go(5, 14); go(4, 13)
    status("after lance heal")
    save_clean(d, "tallow-hideout-b2f.state")

if stage <= 2:
    if d.map_name() != "TEAM_ROCKET_BASE_B3F":
        go(27, 13, "B2F east stairs")
        warp(27, 14, "TEAM_ROCKET_BASE_B3F")
    status("B3F")
    # passwords: grunt (5,14) and rocket girl (21,7); scientists (23,11) (11,15)
    for tx, ty in [(5, 14), (11, 15), (23, 11), (21, 7)]:
        talk(tx, ty); status(f"B3F trainer {(tx, ty)}")
        maybe_rest()
    log.info("passwords: %s %s", flag("EVENT_LEARNED_SLOWPOKETAIL"), flag("EVENT_LEARNED_RATICATE_TAIL"))
    save_clean(d, "tallow-hideout-b3f.state")

if stage <= 3:
    # passwords are given on a SECOND talk (endifjustbattled)
    for tx, ty in [(21, 7), (5, 14)]:
        if not (flag("EVENT_LEARNED_SLOWPOKETAIL") and flag("EVENT_LEARNED_RATICATE_TAIL")):
            talk(tx, ty)
    log.info("passwords: %s %s", flag("EVENT_LEARNED_SLOWPOKETAIL"), flag("EVENT_LEARNED_RATICATE_TAIL"))
    assert flag("EVENT_LEARNED_SLOWPOKETAIL") and flag("EVENT_LEARNED_RATICATE_TAIL")
    maybe_rest()
    # to the boss door: B3F (27,2) -> B2F north -> B2F (3,2) -> B3F west region -> door (10,9) from (10,10)
    if not go(10, 10):
        go(27, 3); warp(27, 2, "TEAM_ROCKET_BASE_B2F")
        go(3, 3); warp(3, 2, "TEAM_ROCKET_BASE_B3F")
        status("B3F west")
        go(10, 10)
    d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()      # bg_event door: read it
    log.info("door opened=%s drift=%s", flag("EVENT_OPENED_DOOR_TO_GIOVANNIS_OFFICE"), d.grid_drift())
    d.sync_grid()
    go(10, 9); go(10, 8)
    status("boss room")
    for _ in range(4):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    log.info("petrel beaten=%s", flag("EVENT_BEAT_ROCKET_EXECUTIVEM_4"))
    status("after petrel")
    talk(7, 2)
    log.info("murkrow password: %s", flag("EVENT_LEARNED_HAIL_GIOVANNI"))
    save_clean(d, "tallow-hideout-petrel.state")

if stage <= 4:
    # B2F transmitter door (14,12)/(15,12) is a bg_event read from the SOUTH region (row 13):
    # B3F west -> B3F (27,2)... no: B2F north (3,1) -> B3F via (3,2) -> east region? not connected.
    # Route: B2F north -> (27,2) -> B3F east -> (27,14) -> B2F south -> (14,13) face U, A.
    maybe_rest()
    if d.map_name() == "TEAM_ROCKET_BASE_B3F" and not go(27, 3):
        go(3, 3); warp(3, 2, "TEAM_ROCKET_BASE_B2F")
    if d.map_name() == "TEAM_ROCKET_BASE_B2F" and not go(14, 13):
        go(27, 3); warp(27, 2, "TEAM_ROCKET_BASE_B3F")
    if d.map_name() == "TEAM_ROCKET_BASE_B3F":
        go(27, 13); warp(27, 14, "TEAM_ROCKET_BASE_B2F")
        go(14, 13)
    status("B2F south, at the transmitter door")
    d.press("U:8 .:10 A:6 .:40"); settle_dialog(d); d.settle()
    log.info("transmitter door=%s", flag("EVENT_OPENED_DOOR_TO_ROCKET_HIDEOUT_TRANSMITTER"))
    d.sync_grid()
    go(14, 12); go(14, 11)
    for _ in range(4):
        if d.battle():
            d.fight()
        d.settle(); settle_dialog(d)
    status("after ariana")
    log.info("ariana=%s", flag("EVENT_BEAT_ROCKET_EXECUTIVEF_1"))
    save_clean(d, "tallow-hideout-ariana.state")
    maybe_rest()
    for ex, ey in [(7, 5), (7, 7), (7, 9)]:
        talk(ex, ey); status(f"electrode {(ex, ey)}")
    log.info("electrodes cleared=%s", flag("EVENT_CLEARED_ROCKET_HIDEOUT"))
    settle_dialog(d); d.drain_scene(); d.settle()
    status("done")
    save_clean(d, "tallow-hideout-done.state"); save_clean(d)
