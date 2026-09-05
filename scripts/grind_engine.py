#!/usr/bin/env python
"""Self-directing evolution grinder: the 38 level-evolution dex entries.

Gen 3 fires `TryEvolvePokemon` only on a LEVEL-UP, and exp reaches a mon only
if it PARTICIPATED (or holds the EXP. SHARE, which is welded to NINJASK
because the per-mon menu highlights rows with a box instead of the `>` glyph
`select_label` needs). So the way to bank these entries is to put the
pre-evolutions in the party and let them fight.

Each cycle this re-derives its own targets from live state rather than
following a fixed list, because the cheapest target changes as mons evolve:

  * every party/box mon is checked against `gEvolutionTable` (read out of the
    ROM, so it is authoritative) for a by-level evolution whose target species
    is NOT yet owned;
  * boxed levels come from `boxed_level()` -- the box format has no level
    field at all, only EXP;
  * cheapest-first, so the 20 mons already past their threshold (one level-up
    each) go before anything needing a grind.

Several entries the gap report files under "wild" are cheaper here: ALTARIA,
MEDICHAM, BANETTE, DODRIO, GOLDUCK, SEAKING and SANDSLASH all evolve from
mons already in the boxes, which beats a Sky Pillar / Victory Road / Safari
trip for each.

Party composition is built with `deposit`/`withdraw` only. The party menu's
SWITCH is never used -- depositing everything above a mon makes it slot 0 by
construction, and that path is verified.
"""
import argparse, logging, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver, TravelError
from pokeagent.dex import DexTarget
from pokeagent.storage import Storage
from collect import Collector

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("grind")

NET = "SEA BIRD"      # L100 sweeper: stops a faint becoming a blackout
NINJA = "NINJA"


def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def sp(d, m):
    return d.names.species(m.species).upper()


def fresh_dex(d):
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def owned_count(d, dex=None):
    return len((dex or fresh_dex(d)).owned_species(d.state))


def targets(d, dex, budget):
    """(need, species, level, where, becomes) cheapest-first, unowned targets."""
    own = set(dex.owned_species(d.state))
    et = dex.evolutions
    mons = [(("party", i), m) for i, m in enumerate(party(d))]
    mons += [(("box", s // 30, s % 30), m) for s, m in dex.boxed()]
    out, seen = [], set()
    for where, m in mons:
        lv = m.level if m.level else dex.boxed_level(m)
        for e in et._forward.get(m.species, ()):
            if not e.by_level or e.to_species in own:
                continue
            need = max(0, e.param - lv)
            if need > budget or e.to_species in seen:
                continue
            seen.add(e.to_species)
            out.append((need, sp(d, m), lv, where, d.names.species(e.to_species)))
    out.sort(key=lambda r: (r[0], -r[2]))
    return out


def to_center(d):
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("  heal: %s", str(e)[:70])
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
        log.info("  fly/heal: %s", str(e)[:70])
    return d.map_name().endswith("PokemonCenter_1F")


def build_party(d, st, dex, want, keep_net=True):
    """Get `want` (list of (species, where)) into the party, plus the net."""
    keep = {s for s, _w in want} | ({NET, NINJA} if keep_net else set())
    # 1. Evict anything that is not wanted, lowest value first.
    for _ in range(8):
        p = party(d)
        drop = next((i for i, m in enumerate(p)
                     if sp(d, m) not in keep
                     and (m.nickname or "").upper() not in (NET, NINJA)), None)
        if drop is None or len(p) <= 1:
            break
        if not st.deposit(drop):
            log.info("  evict refused: %s", getattr(st, "last_reason", "?"))
            break
    # 2. Bring in the wanted ones that are still boxed.
    for species, where in want:
        if any(sp(d, m) == species for m in party(d)):
            continue
        if where[0] != "box":
            continue
        if len(party(d)) >= 6:
            p = party(d)
            drop = next((i for i, m in enumerate(p)
                         if sp(d, m) not in keep), None)
            if drop is None or not st.deposit(drop):
                break
        if not st.withdraw(where[1], where[2]):
            log.info("  withdraw %s refused: %s", species,
                     getattr(st, "last_reason", "?"))
    return party(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--route", default="Route117")
    ap.add_argument("--minutes", type=float, default=600.0)
    ap.add_argument("--per-cycle", type=float, default=25.0)
    ap.add_argument("--budget", type=int, default=8,
                    help="max levels of grind a target may need")
    ap.add_argument("--slots", type=int, default=4)
    a = ap.parse_args()
    stop = time.time() + a.minutes * 60

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = fresh_dex(d)
    start_n = owned_count(d, dex)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])

    def always_attack(frame):
        best, score = 0, -1.0
        for i, mv in enumerate(frame.get("moves") or []):
            if not mv or not mv.get("pp"):
                continue
            s = (mv.get("power") or 0) * (mv.get("effect_mult") or 1.0)
            if s > score:
                best, score = i, s
        return ("attack", best)

    cycle = 0
    while time.time() < stop:
        cycle += 1
        dex = fresh_dex(d)
        before = owned_count(d, dex)
        rows = targets(d, dex, a.budget)
        if not rows:
            log.info("no targets within +%d levels -- widening", a.budget)
            a.budget += 6
            rows = targets(d, dex, a.budget)
            if not rows:
                log.info("nothing left to evolve by level")
                break
        want = [(r[1], r[3]) for r in rows[:a.slots]]
        log.info("=== cycle %d | dex %d | targets %s ===", cycle, before,
                 [(r[1], "L%s" % r[2], "->%s" % r[4], "+%d" % r[0])
                  for r in rows[:a.slots]])

        if not to_center(d):
            log.info("no Centre from %s -- stopping", d.map_name())
            break
        st = Storage(d)
        build_party(d, st, dex, want)
        try:
            d.heal_at_nearest_center()
        except Exception:  # noqa: BLE001
            pass
        d.save(a.state)
        log.info("  party %s", [(sp(d, m), m.level) for m in party(d)])

        sw = Collector(d, feed_name="grind")
        sw.base_policy = lambda _p=always_attack: _p
        try:
            d.travel(a.route)
        except TravelError as e:
            log.info("  travel: %s", str(e)[:80])
        deadline = time.time() + a.per_cycle * 60
        while time.time() < min(deadline, stop):
            alive = [m for m in party(d) if (m.hp or 0) > 0]
            if len(alive) <= 1:
                break                     # everything but the net is down
            try:
                sw.pace_map(min(deadline, stop), terrain="grass")
            except Exception as e:  # noqa: BLE001
                log.info("  pace_map: %s", str(e)[:90])
                break
            if owned_count(d) > before:
                break
        d.save(a.state)
        now = owned_count(d)
        if now > before:
            log.info("  *** DEX %d -> %d *** %s", before, now,
                     [(sp(d, m), m.level) for m in party(d)])
        else:
            log.info("  no new species this cycle; party %s",
                     [(sp(d, m), m.level, m.hp) for m in party(d)])

    dex = fresh_dex(d)
    log.info("FINAL %s | %s | gained %d", d.map_name(),
             dex.summary(d.state).split(";")[0], owned_count(d, dex) - start_n)
    d.save(a.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
