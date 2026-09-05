#!/usr/bin/env python
"""Stage the party for an Elite Four lead-out run, then park at the League.

Slot 0 must be the mon that needs experience and slots 1+ the escorts, because
`elite_four.py --front-lead` leads with slot 0 and switches it straight out:
the opponent's turn-one attack lands on the incoming escort, so the target
banks a full participant share of every KO without ever taking damage.

Composition is done with deposit/withdraw only -- depositing everything above
a mon makes it slot 0 by construction. The party menu's SWITCH is unusable
here: its rows highlight with a box rather than the `>` glyph select_label
reads, which is the same reason the EXP. SHARE is still stuck on NINJASK.
"""
import argparse, logging, sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver, TravelError
from pokeagent.dex import DexTarget
from pokeagent.storage import Storage

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("front")


def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def spn(d, m):
    return d.names.species(m.species).upper()


def to_center(d):
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("heal: %s", str(e)[:80])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    for _ in range(4):
        if d.flight.flyable_here():
            break
        d.flight.step_outside()
    try:
        d.fly_to("MauvilleCity")
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("fly/heal: %s", str(e)[:80])
    return d.map_name().endswith("PokemonCenter_1F")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--target", required=True, help="species for slot 0")
    ap.add_argument("--escorts", default="PELIPPER,BLAZIKEN,AGGRON,MIGHTYENA",
                    help="escort SPECIES, strongest first")
    a = ap.parse_args()
    escorts = [e.strip().upper() for e in a.escorts.split(",") if e.strip()]
    target = a.target.strip().upper()

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])

    if not to_center(d):
        log.info("no Centre reachable (at %s)", d.map_name())
        return 1
    st = Storage(d)

    # bring the target in if it is boxed
    if not any(spn(d, m) == target for m in party(d)):
        hit = next(((s, mo) for s, mo in dex.boxed()
                    if d.names.species(mo.species).upper() == target), None)
        if hit is None:
            log.info("%s is in neither party nor boxes", target)
            return 1
        while len(party(d)) >= 6:
            v = next((i for i, m in enumerate(party(d))
                      if (m.nickname or "").upper() not in escorts), None)
            if v is None or not st.deposit(v):
                break
        if not st.withdraw(hit[0] // 30, hit[0] % 30):
            log.info("withdraw %s refused: %s", target,
                     getattr(st, "last_reason", "?"))
            return 1

    # deposit everything above the target so it lands on slot 0
    for _ in range(8):
        p = party(d)
        if p and spn(d, p[0]) == target:
            break
        drop = next((i for i, m in enumerate(p) if spn(d, m) != target), None)
        if drop is None or len(p) <= 1:
            break
        if not st.deposit(drop):
            log.info("deposit refused: %s", getattr(st, "last_reason", "?"))
            break

    # Restore the escorts behind it, matched by SPECIES. A deposited mon's
    # nickname reads back EMPTY from the box struct, so matching escorts by
    # nickname silently found nothing and left the target to lead alone.
    fresh = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    for species in escorts:
        if len(party(d)) >= 6:
            break
        if any(spn(d, m) == species for m in party(d)):
            continue
        hit = next(((s, mo) for s, mo in fresh.boxed()
                    if d.names.species(mo.species).upper() == species), None)
        if hit is None:
            log.info("escort %s not in any box", species)
            continue
        if not st.withdraw(hit[0] // 30, hit[0] % 30):
            log.info("escort %s refused: %s", species,
                     getattr(st, "last_reason", "?"))

    try:
        d.heal_at_nearest_center()
    except Exception:  # noqa: BLE001
        pass
    p = party(d)
    log.info("party %s", [(m.nickname, spn(d, m), m.level, m.hp) for m in p])
    if not p or spn(d, p[0]) != target:
        log.info("FAILED: slot 0 is %s, not %s",
                 spn(d, p[0]) if p else "empty", target)
        d.save(a.state)
        return 1

    # park inside the League hall so elite_four.py starts where it expects
    # EverGrande is across water -- Fly, never walk. `travel` answered
    # "no walkable route from MauvilleCity to EverGrandeCity".
    try:
        for _ in range(4):
            if d.flight.flyable_here():
                break
            d.flight.step_outside()
        d.fly_to("EverGrandeCity")
    except Exception as e:  # noqa: BLE001
        log.info("fly EverGrande: %s", str(e)[:80])
    try:
        import league_loop
        league_loop.into_hall(d)
    except Exception as e:  # noqa: BLE001
        log.info("into_hall: %s", str(e)[:100])
    log.info("parked at %s %s", d.map_name(), d.pos())
    d.save(a.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
