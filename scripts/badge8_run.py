#!/usr/bin/env python
"""Badge 8: stock up, re-cross the ice, and beat Wallace.

The last attempt reached MILOTIC -- his fifth and final mon -- with SHOCK WAVE
(TM34, Electric, never misses) doing double damage to a team that is Water top
to bottom. What ran out was healing: the bag held two HYPER POTIONs and the
SUPER POTIONs restore 50 against a Milotic that hits for more than that.

There is 45,884 sitting unspent, so the fix is a shopping trip.

Order matters: the gym's ice resets when the floor reloads, so shop FIRST and
cross afterwards.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.partyorder import PartyOrder  # noqa: E402
from collect import Collector  # noqa: E402
from ice_run import read_floor, cover, floor_path, run_path  # noqa: E402

log = logging.getLogger("badge8")

CITY = "SootopolisCity"
GYM = "SootopolisCity_Gym_1F"
MART_DOOR = (17, 29)
GYM_DOOR, GYM_APPROACH = (31, 32), (31, 33)
WALLACE_SPOT = (8, 3)
#: Healing, best first. Whatever the shelf actually calls it.
WANTED = ("FULL RESTORE", "HYPER POTION", "SUPER POTION")
#: How many X ATTACKs to stack before swinging. Wallace's MILOTIC has Recover
#: and he carries potions of his own, so a fair fight is a draw -- two runs
#: ended "stalled", four straight turns with neither HP bar moving. Raw
#: attack multiplication is what breaks that: +4 roughly doubles the hit, and
#: TAKE DOWN at 90 base off a doubled Attack clears Recover comfortably.
XATTACK_STACK = 4


def breaker(frame, used=[0]):
    """Stack X ATTACK, then hit hard, healing when it gets dangerous.

    Deliberately NOT the strongest-typed attacker: SHOCK WAVE is 2x but comes
    off a L42 Pelipper's mediocre special attack and lands under Recover.
    MIGHTYENA is neutral to both Water and the Ice moves that killed the
    Water/Grass tank, and TAKE DOWN is 90 base physical -- the thing X ATTACK
    actually multiplies.
    """
    me = frame.get("me") or {}
    hp, mx = me.get("hp") or 0, me.get("max_hp") or 1
    items = (frame.get("bag") or {}).get("items") or {}
    if hp * 2 < mx:
        for name in WANTED:
            if items.get(name):
                return ("item", name)
    stages = me.get("stat_stages") or {}
    atk = stages.get("attack", stages.get("atk", 0)) if isinstance(stages, dict) else 0
    if atk < XATTACK_STACK and items.get("X ATTACK") and hp * 5 > mx * 3:
        used[0] += 1
        return ("item", "X ATTACK")
    return Driver.damage_first(frame)


def sustain(frame):
    """Damage first; drink EARLY and with the biggest thing in the bag.

    `bag` is nested by pocket -- reading it flat is why an earlier heal branch
    never once fired. Healing at 40% was also too late: Milotic removes more
    than a SUPER POTION restores.
    """
    me = frame.get("me") or {}
    hp, mx = me.get("hp") or 0, me.get("max_hp") or 1
    if hp * 5 < mx * 3:
        items = (frame.get("bag") or {}).get("items") or {}
        for name in WANTED:
            if items.get(name):
                return ("item", name)
    return Driver.damage_first(frame)


def leave_gym(d) -> bool:
    if d.map_name() == "SootopolisCity_Gym_B1F":
        d.reach_cell(11, 22, map_name=d.map_name(), on_battle="fight")
        d.take_warp(11, 22)
    if d.map_name() != GYM:
        return d.map_name() == CITY
    for door in ((8, 25), (9, 25)):
        try:
            d.reach_cell(*door, map_name=GYM, on_battle="fight")
        except Exception:  # noqa: BLE001
            pass
        if d.pos() == door and d.take_warp(*door):
            break
    return d.map_name() == CITY


def shop(d) -> bool:
    """Buy as much top-tier healing as the wallet allows."""
    col = Collector(d, feed_name=None)
    if not d.take_warp(*MART_DOOR):
        try:
            d.goto(MART_DOOR[0], MART_DOOR[1] + 1, on_battle="fight")
        except Exception:  # noqa: BLE001
            pass
        if not d.take_warp(*MART_DOOR):
            log.info("could not get into the Mart")
            return False
    clerk = col.clerk_cell(d.map_name())
    if clerk is None:
        log.info("no clerk on %s", d.map_name())
        return False
    try:
        d.talk_to(*clerk)
    except Exception:  # noqa: BLE001
        pass
    shelf = {}
    try:
        shelf = {r["name"].upper(): r["price"] for r in col.mart.items()}
    except Exception as exc:  # noqa: BLE001
        log.info("shelf unreadable: %s", str(exc)[:70])
    log.info("shelf: %s", sorted(shelf))
    bought = False
    if "X ATTACK" in shelf and d.state.money() > 6000:
        qty = min(12, (d.state.money() - 5000) // shelf["X ATTACK"])
        if qty > 0:
            log.info("buying %dx X ATTACK at %d", qty, shelf["X ATTACK"])
            if not col.mart.buy("X ATTACK", qty):
                log.info("  X ATTACK: %s", col.mart.last_reason)
    for want in WANTED:
        if want not in shelf:
            continue
        price = shelf[want]
        qty = min(30, max(0, (d.state.money() - 400) // price))
        if qty <= 0:
            continue
        log.info("buying %dx %s at %d (money %d)", qty, want, price,
                 d.state.money())
        if col.mart.buy(want, qty):
            bought = True
            break
        log.info("  %s: %s", want, col.mart.last_reason)
    for _ in range(12):
        if not d.scene_active() and not col.mart.is_open():
            break
        d.emu.run_sequence("B:4 .:20")
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    return bought


def cross_ice(d) -> bool:
    """Walk the three ice sections to Wallace's doorstep."""
    for _ in range(4):
        if d.map_name() == GYM and d.pos() == WALLACE_SPOT:
            return True
        if d.map_name() == "SootopolisCity_Gym_B1F":
            d.reach_cell(11, 22, map_name=d.map_name(), on_battle="fight")
            d.take_warp(11, 22)
        if d.map_name() == CITY:
            try:
                d.goto(*GYM_APPROACH, on_battle="fight")
            except Exception:  # noqa: BLE001
                pass
            d.emu.run_sequence("UP:24 .:40")
            d.advance_scene(60000)
        if d.map_name() != GYM:
            continue
        for _ in range(6):
            if d.pos() == WALLACE_SPOT:
                break
            d.close_menus()
            walls, thin, cracked, stairs = read_floor(d)
            blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                       if not o.get("player")}
            done = floor_path(walls, thin, cracked, blocked, d.pos(),
                              [WALLACE_SPOT])
            if done is not None:
                run_path(d, done, GYM)[0]
                continue
            plan = None
            for tile in sorted(thin):
                body = cover(thin, tile, exits=stairs)
                if body is None:
                    continue
                stands = [(tile[0] + dx, tile[1] + dy)
                          for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                          if (tile[0] + dx, tile[1] + dy) not in thin
                          and (tile[0] + dx, tile[1] + dy) not in walls]
                approach = floor_path(walls, thin, cracked, blocked, d.pos(),
                                      stands)
                if approach is not None:
                    plan = (tile, approach, body)
                    break
            if plan is None:
                break
            tile, approach, body = plan
            if not run_path(d, approach, GYM)[0]:
                break
            first = next((mv for mv, (dx, dy) in
                          (("U", (0, -1)), ("D", (0, 1)), ("L", (-1, 0)),
                           ("R", (1, 0)))
                          if (d.pos()[0] + dx, d.pos()[1] + dy) == tile), None)
            if first is None:
                break
            run_path(d, first + body, GYM)[0]
    return d.map_name() == GYM and d.pos() == WALLACE_SPOT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--skip-shop", action="store_true")
    ap.add_argument("--max-frames", type=int, default=3_000_000,
                    help="a leader who heals makes for a LONG battle")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s money %d badges %d", d.map_name(), d.pos(),
             d.state.money(), len(d.state.badges()))

    if not a.skip_shop:
        if not leave_gym(d):
            log.info("could not leave the gym (in %s)", d.map_name())
            return 1
        d.heal() if d.map_name() != CITY else None
        if not shop(d):
            log.info("shopping did not land; carrying on with what we have")
        log.info("after shopping: money %d", d.state.money())
        # Heal before the climb.
        try:
            d.goto(43, 32, on_battle="fight")
            if d.take_warp(43, 31):
                d.heal()
                for e in d.exits():
                    if e.get("kind") == "warp":
                        d.take_warp(e["x"], e["y"])
                        break
        except Exception:  # noqa: BLE001
            pass
        if a.out:
            d.save(a.out)

    if not cross_ice(d):
        log.info("could not reach %s (at %s %s)", WALLACE_SPOT, d.map_name(),
                 d.pos())
        if a.out:
            d.save(a.out)
        return 1
    log.info("at Wallace's doorstep with %s",
             (d.state.bag().get("items") or {}))

    # MIGHTYENA: neutral to Water AND to the Ice that killed the Grass tank,
    # and TAKE DOWN is the biggest physical hit on the roster for X ATTACK to
    # multiply.
    PartyOrder(d).lead_with("MIGHTYENA")
    before = len(d.state.badges())
    d.close_menus()
    d.emu.run_sequence("U:4 .:20")
    d.emu.run_sequence("A:4 .:60")
    d.advance_scene(90000)
    if d.in_battle():
        # WALLACE HEALS HIS OWN MONS, so this runs long: the previous attempt
        # came back "stalled" -- out of frames, not beaten -- after spending
        # all 31 HYPER POTIONs. His supply is finite and ours is not, so the
        # budget is what decides it.
        r = d.fight(policy=breaker, max_frames=a.max_frames)
        log.info("outcome: %s", (r or {}).get("outcome"))
    for _ in range(6):
        d.advance_scene(150000)
    log.info("BADGES %d -> %d", before, len(d.state.badges()))
    if a.out:
        d.save(a.out)
    return 0 if d.state.flag("FLAG_BADGE08_GET") else 1


if __name__ == "__main__":
    raise SystemExit(main())
