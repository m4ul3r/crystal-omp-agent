#!/usr/bin/env python
"""Drive the opening story: truck -> house -> clock -> rival -> starter.

Produces the checkpoints the rest of the harness is tested against. Almost
nothing can be exercised without a party: no battle, no party menu, no
catching, no damage math.

Written as a state machine over ``d.state.tasks()`` and the engine's own
variables, never as a fixed button script. Task names come from the symbol
table, so each step waits for the engine to actually be in the state it
thinks it is -- which is what survives the variable-length fades the intro is
full of. A fixed press count desynchronises on the first slow frame.

Three story facts that are read from the decomp rather than guessed:

* ``VAR_LITTLEROOT_STATE`` gates the north exit. It only advances when you
  talk to the rival in their bedroom (LittlerootTown_MaysHouse_2F/
  scripts.inc:41 ``setvar VAR_LITTLEROOT_STATE, 1``). Walking north first
  just gets you pushed back.
* The rival is MOVED by an on-transition script (``setobjectxyperm 1, 7, 2``),
  so the coordinate in map.json is already stale when you walk in. Talk to
  the live position from ``gObjectEvents``.
* ``sStarterMons = {TREECKO, TORCHIC, MUDKIP}`` (src/starter_choose.c:50) and
  the picker starts on index 1. NEVER assume a ball position -- the
  predecessor project lost a whole leg to exactly that assumption.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.menus import Menus  # noqa: E402
from pokeagent.naming import NamingScreen  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("to_starter")

HOUSE = {"male": "LittlerootTown_BrendansHouse", "female": "LittlerootTown_MaysHouse"}
RIVAL_HOUSE = {"male": "LittlerootTown_MaysHouse", "female": "LittlerootTown_BrendansHouse"}
CLOCK = (5, 1)
#: src/starter_choose.c:50
STARTERS = {"TREECKO": 0, "TORCHIC": 1, "MUDKIP": 2}
#: OBJ_EVENT_GFX_BIRCHS_BAG, as it appears in gObjectEvents.graphicsId
BAG_GFX = 97


def set_the_clock(d, menus, max_frames=60000, stall_rounds=6):
    """Adjust screen -> confirm box -> done.

    Two traps, both hit by an earlier attempt:

    * The confirm is a real YES/NO menu, answered by reading
      ``gMenu.cursorPos``. Mashing A oscillates forever between
      Task_SetClock2 and Task_SetClock4 because the press lands on NO.
    * ``Task_FieldMessageBox`` disappearing does NOT mean the script stopped
      waiting for A -- the script's own wait outlives the box task. So the
      trigger to press A is a stalled signature, not the box.
    """
    last, same, spent = None, 0, 0
    while spent < max_frames:
        tasks = d.state.tasks()
        if "Task_SetClock4" in tasks:
            menus.resolve_choice("YES")
            spent += 40
            last, same = None, 0
            continue
        if not d.scene_active() and not any(t.startswith("Task_SetClock") for t in tasks):
            return True
        sig = (tuple(tasks), d.state.message())
        same = same + 1 if sig == last else 0
        last = sig
        if same >= stall_rounds:
            d.emu.run_sequence("A:4 .:20")
            same = 0
        else:
            d.emu.tick(20)
        spent += 24
    return False


def warp_to(d, predicate, what, required=True) -> bool:
    """Take the first warp matching `predicate`.

    `required` keeps the CLI's old contract -- it produces checkpoints and a
    silent miss there is worthless -- while the play loop passes False, because
    a loop must survive an intro it cannot finish rather than die inside it.
    """
    exits = [e for e in d.nav.exits(d.map_name()) if e["kind"] == "warp" and predicate(e)]
    if not exits:
        if required:
            raise SystemExit(f"no warp to {what} from {d.map_name()}")
        log.warning("no warp to %s from %s", what, d.map_name())
        return False
    if not d.take_warp(exits[0]["x"], exits[0]["y"]):
        if required:
            raise SystemExit(f"could not reach {what}: {d.last_warp_reason}")
        log.warning("could not reach %s: %s", what, d.last_warp_reason)
        return False
    d.advance_scene(40000)
    return True


def choose_starter(d, menus, want, max_rounds=80):
    """Open Birch's bag and take a named starter, cursor-verified."""
    bag = next((n for n in d.live_npcs() if n["graphics_id"] == BAG_GFX), None)
    if bag is None:
        raise SystemExit("Birch's bag is not on this map")
    if not d.talk_to(bag["x"], bag["y"]):
        raise SystemExit(f"could not reach the bag: {d.last_talk_reason}")

    for _ in range(max_rounds):
        if d.state.task_data("Task_StarterChoose2") is not None:
            break
        d.emu.run_sequence("A:4 .:20")
    else:
        raise SystemExit("the starter picker never opened")

    target = STARTERS[want]
    for _ in range(6):
        sel = d.state.task_data("Task_StarterChoose2")
        if sel is None or sel[0] == target:
            break
        d.emu.run_sequence(("RIGHT" if sel[0] < target else "LEFT") + ":4 .:14")
    got = d.state.task_data("Task_StarterChoose2")
    if got is not None and got[0] != target:
        raise SystemExit(f"starter cursor stuck at {got[0]}, wanted {target} ({want})")

    d.emu.run_sequence("A:6 .:40")
    for _ in range(max_rounds):
        if d.state.party_count():
            return True
        if menus.bounds() == (0, 1) and d.scene_active():
            menus.resolve_choice("YES")
        else:
            d.emu.run_sequence("A:4 .:20")
    raise SystemExit("confirmed the starter but the party is still empty")


def _leave_truck(d):
    """Run the trigger column before the truck's dynamic exit."""
    if d.map_name() != "InsideOfTruck":
        return
    for _ in range(4):
        if d.map_name() != "InsideOfTruck":
            break
        # x=3 runs setdynamicwarp; the three exit tiles are at x=4.
        d.step_dir("R")
        d.advance_scene(40000)
        if d.map_name() != "InsideOfTruck":
            break
        _x, y = d.pos()
        if not d.take_warp(4, y if 1 <= y <= 3 else 2):
            log.debug("truck exit refused: %s", d.last_warp_reason)
    d.advance_scene(60000)


def drive_intro(d, menus=None, starter=None, nickname="EMBER",
                saves=None, out=None) -> bool:
    """Play the opening from wherever the game currently is up to a party.

    Extracted from `main` so the PLAY LOOP can call it. A fresh game has no
    Pokemon, and every one of the loop's own steps assumes at least one -- so a
    brand-new run sat in its bedroom repeating "no grass here; heading for
    Route101" while the game politely pushed it back down the stairs. The
    intro is not optional and it is not a special mode; it is just the part of
    the story that happens before the party exists.

    Returns True once the party is non-empty. Failures are reported and
    returned, never raised: the loop must survive an intro it cannot finish.
    """
    from pokeagent.menus import Menus as _Menus

    menus = menus or _Menus(d.emu, d.state)
    starter = starter or d.spec.starter.removeprefix("SPECIES_")
    if starter not in STARTERS:
        log.warning("starter %r is not one of %s", starter, sorted(STARTERS))
        return False

    def fail(why):
        log.warning("intro stopped: %s", why)
        return False

    d.advance_scene(60000)
    gender = d.state.gender()
    house = f"{HOUSE[gender]}_1F"
    _leave_truck(d)
    if d.map_name() != house and not d.state.party():
        # Not in the house and no party: try to get there before giving up.
        try:
            d.travel(house)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not travel to %s: %s", house, exc)
    if d.state.var("VAR_LITTLEROOT_INTRO_STATE") < 6:
        if d.map_name() != house:
            return fail(f"expected {house}, am in {d.map_name()}")
        if not warp_to(d, lambda e: e["dest"].endswith("_2F"), "the bedroom",
                       required=False):
            return fail("could not get upstairs")
        if not d.talk_to(*CLOCK):
            return fail(f"could not reach the clock: {d.last_talk_reason}")
        if not set_the_clock(d, menus):
            return fail("the clock never finished being set")
        d.advance_scene(20000)
        if not warp_to(d, lambda e: e["dest"].endswith("_1F"), "downstairs",
                       required=False):
            return fail("could not get back downstairs")
    if d.map_name() != "LittlerootTown":
        warp_to(d, lambda e: e["dest"] == "LittlerootTown", "outside",
                required=False)

    if d.state.var("VAR_LITTLEROOT_STATE") < 1:
        rival_1f = f"{RIVAL_HOUSE[gender]}_1F"
        try:
            d.travel(rival_1f)
        except Exception as exc:  # noqa: BLE001
            log.debug("travel to the rival's house: %s", exc)
        if not warp_to(d, lambda e: e["dest"].endswith("_2F"),
                       "the rival's bedroom", required=False):
            return fail("could not reach the rival's bedroom")
        rival = next((n for n in d.live_npcs() if not n["player"]), None)
        if rival is None:
            return fail("the rival is not in their bedroom")
        if not d.talk_to(rival["x"], rival["y"]):
            return fail(f"could not talk to the rival: {d.last_talk_reason}")
        d.advance_scene(40000)
        warp_to(d, lambda e: e["dest"].endswith("_1F"), "downstairs",
                required=False)
        warp_to(d, lambda e: e["dest"] == "LittlerootTown", "outside",
                required=False)

    for _ in range(4):
        if d.map_name() == "Route101":
            break
        try:
            d.travel("Route101")
        except Exception as exc:  # noqa: BLE001 - the push-back scene is expected
            log.debug("route101 attempt blocked: %s", exc)
            d.advance_scene(60000)
    if d.map_name() != "Route101":
        return fail(f"could not reach Route101, stuck on {d.map_name()}")
    d.advance_scene(60000)

    if not d.state.party():
        choose_starter(d, menus, starter)
    if not d.state.party():
        return fail("the starter was never taken")
    mon = d.state.party()[0]
    log.info("starter %s L%d", d.names.species(mon.species), mon.level)

    # The Poochyena fight follows immediately.
    for _ in range(200):
        if d.state.battle_ready() or not d.in_battle():
            break
        d.emu.run_sequence("A:4 .:20")
    while not d.advance_scene():
        if d.naming_open():
            NamingScreen(d.emu, d.state).type(nickname)
        else:
            break
    return bool(d.state.party())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/littleroot.state")
    ap.add_argument("--out", default="saves/starter.state")
    # The GameSpec carries the run's starter, so the choice lives in one
    # place rather than in every script's default. Sapphire's is TORCHIC.
    ap.add_argument("--starter", default=None, choices=sorted(STARTERS),
                    help="overrides the GameSpec's starter for this run")
    ap.add_argument("--nickname", default="EMBER")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s"
    )

    d = Driver(a.state)
    menus = Menus(d.emu, d.state)
    saves = Path(a.out).parent
    starter = a.starter or d.spec.starter.removeprefix("SPECIES_")
    if starter not in STARTERS:
        raise SystemExit(f"{d.spec.name}'s starter {starter!r} is not one of {sorted(STARTERS)}")
    log.info("starter       %s (from %s)", starter,
             "--starter" if a.starter else f"GameSpec[{d.spec.id}]")

    log.info("start        %s", d.status())
    d.advance_scene(60000)
    _leave_truck(d)
    gender = d.state.gender()
    house = f"{HOUSE[gender]}_1F"
    if d.map_name() != house:
        raise SystemExit(f"expected to be inside {house}, am in {d.map_name()}")
    log.info("house        %s", d.status())

    warp_to(d, lambda e: e["dest"].endswith("_2F"), "the bedroom")
    if not d.talk_to(*CLOCK):
        raise SystemExit(f"could not reach the clock: {d.last_talk_reason}")
    if not set_the_clock(d, menus):
        raise SystemExit("the clock never finished being set")
    d.advance_scene(20000)
    log.info("clock set    play time %s", d.state.play_time())

    warp_to(d, lambda e: e["dest"].endswith("_1F"), "downstairs")
    warp_to(d, lambda e: e["dest"] == "LittlerootTown", "outside")
    log.info("outside      %s", d.status())

    # The north exit is gated on VAR_LITTLEROOT_STATE; the rival advances it.
    rival_1f = f"{RIVAL_HOUSE[gender]}_1F"
    warp_to(d, lambda e: e["dest"] == rival_1f, "the rival's house")
    warp_to(d, lambda e: e["dest"].endswith("_2F"), "the rival's bedroom")
    rival = next((n for n in d.live_npcs() if not n["player"]), None)
    if rival is None:
        raise SystemExit("the rival is not in their bedroom")
    if not d.talk_to(rival["x"], rival["y"]):
        raise SystemExit(f"could not talk to the rival: {d.last_talk_reason}")
    d.advance_scene(40000)
    if d.state.var("VAR_LITTLEROOT_STATE") < 1:
        raise SystemExit("the rival scene did not advance VAR_LITTLEROOT_STATE")
    log.info("rival met    VAR_LITTLEROOT_STATE=%d", d.state.var("VAR_LITTLEROOT_STATE"))

    warp_to(d, lambda e: e["dest"].endswith("_1F"), "downstairs")
    warp_to(d, lambda e: e["dest"] == "LittlerootTown", "outside")

    # Walking north the first time fires the "someone is shouting" trigger,
    # which pushes you back and bumps the state. Then the seam opens.
    for _ in range(3):
        try:
            d.travel("Route101")
            break
        except Exception as exc:  # noqa: BLE001 - the scene is the expected cause
            log.debug("route101 attempt blocked: %s", exc)
            d.advance_scene(60000)
    if d.map_name() != "Route101":
        raise SystemExit(f"could not reach Route101, stuck on {d.map_name()}")
    d.advance_scene(60000)
    log.info("route 101    %s", d.status())
    d.save(saves / "route101.state")

    choose_starter(d, menus, starter)
    mon = d.state.party()[0]
    log.info(
        "starter      %s L%d %d/%d  moves=%s",
        d.names.species(mon.species), mon.level, mon.hp, mon.max_hp,
        [d.names.move(m) for m in mon.moves if m],
    )
    d.save(Path(a.out))

    # The Poochyena fight follows immediately: a real wild battle, and the
    # checkpoint the battle layer needs. Wait for battle_ready, not merely
    # in_battle -- gBattleMons is empty during the transition animation.
    for _ in range(200):
        if d.state.battle_ready():
            break
        d.emu.run_sequence("A:4 .:20")
    if d.state.battle_ready():
        b = d.state.battle()
        log.info(
            "battle       kinds=%s battlers=%d enemy=%s L%d",
            b.kinds, b.battler_count,
            d.names.species(b.mons[1]["species"]), b.mons[1]["level"],
        )
        d.save(saves / "first-battle.state")
    else:
        log.warning("no readable battle after taking the starter")

    # Play the fight out and finish Birch's lab sequence. advance_scene stops
    # at the nickname prompt rather than typing A's into it, so the name is a
    # deliberate decision.
    while not d.advance_scene():
        if d.naming_open():
            NamingScreen(d.emu, d.state).type(a.nickname)
            log.info("nickname     %s", a.nickname)
        else:
            break
    log.info("lab          %s", d.status())
    d.save(saves / "lab.state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
