#!/usr/bin/env python
"""ODDISH -> GLOOM -> BELLOSSOM: two dex entries off one held SUN STONE.

The boxed ODDISH are L25-27 and Gloom's threshold is L21, so they are
already PAST it -- they need ONE in-battle level-up, because
`TryEvolvePokemon` runs off `gLeveledUpInBattle` (battle_main.c:5091-5113).
Then the SUN STONE turns Gloom into BELLOSSOM
(pret/src/data/pokemon/evolution.h). Sapphire sells no stones, so the one in
the bag is the only shot.

Getting out of the Day Care first, deliberately: `Storage.pc_cells()`
matches a tile at (10,1) in `Route117_PokemonDayCare` that is NOT a PC --
the building has none -- and `deposit` then spent 144 walk chunks trying to
reach a cell below it. The room's real exits are (2,8) and (3,8)
(Route117_PokemonDayCare/map.json:29-42).
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402

log = logging.getLogger("sunstone")

BOX_SIZE = 30
DAYCARE = "Route117_PokemonDayCare"


def to_center(d) -> bool:
    """Stand in a real Pokemon Center, asserted by MAP NAME.

    `Storage.pc_cells()` is not a safe test: it matches a tile at (10,1) in
    `Route117_PokemonDayCare`, which has no PC, and `deposit` then spent 144
    walk chunks failing to reach the cell below it. Only a map whose name
    ends in `PokemonCenter_1F` actually has one.

    Leaving the Day Care uses `travel`, not `take_warp` -- the latter hung
    for 17 minutes there (though the true cause of that hang was an
    unanswered nickname prompt eating input, now fixed in hatch.py).
    """
    if d.map_name() == DAYCARE:
        try:
            d.travel("Route117", on_battle="fight", budget_s=120)
            log.info("left the Day Care -> %s %s", d.map_name(), d.pos())
        except Exception as exc:  # noqa: BLE001
            log.info("leaving the Day Care: %s", str(exc)[:90])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        for town in ("MauvilleCity", "VerdanturfTown", "FortreeCity"):
            if d.fly_to(town):
                log.info("flew to %s", d.map_name())
                break
    except Exception as exc:  # noqa: BLE001
        log.info("fly: %s", str(exc)[:90])
    for _ in range(3):
        if d.map_name().endswith("PokemonCenter_1F"):
            return True
        try:
            d.heal_at_nearest_center()
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except (TravelError, Exception) as exc:  # noqa: BLE001
            log.info("centre: %s", str(exc)[:90])
            break
    return d.map_name().endswith("PokemonCenter_1F")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s %s | %s", d.map_name(), d.pos(),
             dex.summary(d.state).split(";")[0])

    have = next((m for m in d.state.party()
                 if d.names.species(m.species).upper() in ("ODDISH", "GLOOM")),
                None)
    if have is None:
        if not to_center(d):
            log.info("could not reach a Centre (at %s)", d.map_name())
            return 1
        if not d.map_name().endswith("PokemonCenter_1F"):
            log.info("not in a Centre (at %s) -- refusing to drive the PC",
                     d.map_name())
            return 1
        st = Storage(d)
        log.info("at %s, PC %s", d.map_name(), st.pc_cells())
        # Free a slot: deposit the highest-level already-registered mon that
        # is not the sweeper. Never WYNAUT (just hatched, keep it simple).
        if len(d.state.party()) >= 6:
            cands = [(i, m) for i, m in enumerate(d.state.party())
                     if d.names.species(m.species).upper()
                     not in ("PELIPPER", "WYNAUT")]
            i, victim = min(cands, key=lambda p: p[1].level or 0)
            log.info("depositing %s L%s", d.names.species(victim.species),
                     victim.level)
            if not st.deposit(i):
                log.info("deposit refused: %s",
                         getattr(st, "last_reason", "?"))
                return 1
        # Highest-level boxed ODDISH: closest to a level-up, and already
        # past Gloom's L21.
        best = None
        for slot, mon in dex.boxed():
            try:
                if d.names.species(mon.species).upper() != "ODDISH":
                    continue
                lv = dex.boxed_level(mon) or 0
            except Exception:  # noqa: BLE001
                continue
            if best is None or lv > best[1]:
                best = (slot, lv)
        if best is None:
            log.info("no boxed ODDISH")
            return 1
        slot, lv = best
        log.info("withdrawing ODDISH L%d from box %d slot %d", lv,
                 slot // BOX_SIZE, slot % BOX_SIZE)
        if not st.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
            log.info("withdraw refused: %s", getattr(st, "last_reason", "?"))
            return 1
        st.close()

    d.save(a.state)
    log.info("party %s", [(d.names.species(m.species), m.level)
                          for m in d.state.party()])
    log.info("banked -- the sweeper's next battle should level it into GLOOM, "
             "then run stone_evolve.py --species GLOOM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
