#!/usr/bin/env python
"""Catch a KECLEON by revealing an invisible one with the Devon Scope.

A prior sweep "visited all 7 statics and caught none", and the reason is in
the script rather than the routing. `EventScript_Kecleon`
(pret/data/scripts/static_pokemon.inc:80-102) is:

    checkitem ITEM_DEVON_SCOPE, 1     <- we hold it
    goto EventScript_AskUseDevonScope
      msgbox Text_WantToUseDevonScope, MSGBOX_YESNO   <- MUST BE ANSWERED
      goto_if_eq EventScript_BattleKecleon
        ...
        dowildbattle

So it is not an encounter you can walk into: it is an A-press on an
object_event whose sprite is invisible, followed by a YES. Each of the seven
sets its own `FLAG_HIDE_KECLEON_*` and is one-shot, and the Fortree one is
not catchable at all -- it flees with `removeobject` and no `dowildbattle`
(pret/data/maps/FortreeCity/scripts.inc:77-86).

Coordinates straight from the maps' own object_events:
  Route120 (20,11) (27,2) (4,77) (7,51) (19,48)
  Route119 (31,6) (20,13, elevation 4)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402

log = logging.getLogger("kecleon")

SPOTS = [
    ("Route119", 31, 6),
    ("Route119", 20, 13),
    ("Route120", 20, 11),
    ("Route120", 27, 2),
    ("Route120", 19, 48),
    ("Route120", 7, 51),
    ("Route120", 4, 77),
]


def have_kecleon(d) -> bool:
    """Is KECLEON in the party or the boxes? Then there is nothing to do.

    Resolved by NAME through `names.species`, because this ROM's name table
    is the authority and there is no reverse lookup on it.
    """
    try:
        from pokeagent.dex import DexTarget

        t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        owned = t.owned_species(d.state) or ()
        return any(d.names.species(sp).upper() == "KECLEON" for sp in owned)
    except Exception:  # noqa: BLE001
        return False


def reach(d, map_name: str, budget: float = 300.0) -> bool:
    if d.map_name() == map_name:
        return True
    for _ in range(3):
        try:
            if d.travel(map_name, on_battle="fight", budget_s=budget):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("  travel %s: %s", map_name, str(exc)[:110])
            break
        if d.map_name() == map_name:
            return True
    return d.map_name() == map_name


def try_spot(d, map_name: str, x: int, y: int) -> bool:
    """Reveal and fight the Kecleon at (x,y). True if a battle happened."""
    if not reach(d, map_name):
        return False
    log.info("  approaching the invisible Kecleon at %s (%d,%d)",
             map_name, x, y)
    # `talk_to` walks to a walkable neighbour, FACES the cell and presses A --
    # which is exactly the interaction the script needs. The sprite being
    # invisible does not matter: the object_event is still there.
    try:
        d.talk_to(x, y)
    except Exception as exc:  # noqa: BLE001
        log.info("  talk_to refused: %s", str(exc)[:100])
        return False
    # ANSWER THE BOX. `Text_WantToUseDevonScope` is MSGBOX_YESNO and the
    # script goes nowhere until it is answered.
    for _ in range(8):
        if d.in_battle():
            break
        try:
            if d.choice_open():
                d.resolve_choice("YES")
                d.advance_scene(40_000)
                continue
        except Exception as exc:  # noqa: BLE001
            log.debug("  choice: %s", str(exc)[:70])
        d.emu.run_sequence("A:4 .:40")
        d.advance_scene(40_000)
    if not d.in_battle():
        log.info("  no battle at (%d,%d) -- already taken or not revealed",
                 x, y)
        return False
    log.info("  KECLEON revealed; catching")
    # The Catcher owns the ball turn: it weakens without killing and throws
    # at the right moment. Forcing `wanted` because this is a one-shot static
    # -- there is no second Kecleon at this cell once the flag is set.
    from pokeagent.catching import CatchPlan, Catcher

    try:
        policy = Catcher(d).policy(CatchPlan(True, "one-shot static",
                                             "KECLEON", 999.0))
        d.fight(policy=policy)
    except Exception as exc:  # noqa: BLE001 - a lost catch is not a crash
        log.info("  catch policy: %s", str(exc)[:110])
        d.fight()
    d.advance_scene(40_000)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())

    if not any(str(k).upper() == "DEVON SCOPE"
               for k in (d.state.bag().get("key_items") or {})):
        log.info("no DEVON SCOPE -- the script prints "
                 "'something unseeable' and returns")
        return 1
    if have_kecleon(d):
        log.info("KECLEON already owned; nothing to do")
        return 0

    for map_name, x, y in SPOTS:
        if try_spot(d, map_name, x, y):
            out = a.out or a.state
            d.save(out)
            log.info("banked %s", out)
            if have_kecleon(d):
                log.info("*** KECLEON CAUGHT ***")
                return 0
            log.info("battle happened but KECLEON is not owned "
                     "(fled or KO'd) -- trying the next spot")
    log.info("no Kecleon caught; at %s %s", d.map_name(), d.pos())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
