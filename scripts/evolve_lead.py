#!/usr/bin/env python
"""Evolve pre-evolutions by making them the LEAD and letting them fight.

Nine of the 38 level-evolution entries need one level or none: the threshold
already sits below the level the mon is held at, and Gen 3 only fires
`TryEvolvePokemon` on a level-up.

Why this shape, after three shapes that failed:

* The EXP. SHARE bench trick is faster but the share is welded to NINJASK --
  the per-mon menu (`Do what with NINJA?`) highlights rows with a BOX, not the
  `>` glyph `select_label` looks for, so it pressed A on CUT and answered
  "There's nothing to CUT." Item juggling is blocked on reading that menu's
  own cursor.
* Exp only reaches a mon that PARTICIPATES (or holds the share), so a benched
  target with no share earns nothing. It has to be the one in the ring.
* `Collector.base_policy()` is a TRAINING policy: with NINJASK L61 in the
  party it sent NINJASK, which then ate every point. Hence a policy here that
  never switches voluntarily.
* A solo party whited out -- a lone L25 MARILL on Route 117 meets grass wilds
  that resist Water Gun and poison it, and one faint ends the run at a
  Centre. So the sweeper rides along as slot 1: it cannot steal exp it never
  earns (it is never sent out while the target stands), but it stops a faint
  from becoming a blackout.

Party composition is built with `deposit`, never with the party menu's SWITCH:
depositing everything above the target makes the target slot 0 by
construction, and deposit is verified working.
"""
import argparse, logging, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver, TravelError
from pokeagent.dex import DexTarget
from pokeagent.storage import Storage
from collect import Collector

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("lead")

BACKUP = "SEA BIRD"          # the L100 sweeper, kept as the anti-whiteout net


def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def named(d, mon):
    return d.names.species(mon.species).upper()


def dexn(d):
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    return len(t.owned_species(d.state))


def to_center(d):
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("  heal: %s", str(e)[:80])
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
        log.info("  fly/heal: %s", str(e)[:80])
    return d.map_name().endswith("PokemonCenter_1F")


def make_lead(d, st, species):
    """Deposit everything above `species` so it becomes slot 0."""
    for _ in range(8):
        p = party(d)
        if p and named(d, p[0]) == species:
            return True
        victim = next((i for i, m in enumerate(p) if named(d, m) != species), None)
        if victim is None or len(p) <= 1:
            break
        if not st.deposit(victim):
            log.info("  deposit refused: %s", getattr(st, "last_reason", "?"))
            return False
    p = party(d)
    return bool(p) and named(d, p[0]) == species


def ensure_backup(d, st, dex):
    """Put the sweeper back in as slot 1 so a faint is not a blackout."""
    if any((m.nickname or "").upper() == BACKUP for m in party(d)):
        return True
    for slot, mon in dex.boxed():
        if (mon.nickname or "").upper() == BACKUP:
            return st.withdraw(slot // 30, slot % 30)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--route", default="Route117")
    ap.add_argument("--targets", required=True,
                    help="comma-separated species, in order")
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--per-target", type=float, default=30.0)
    a = ap.parse_args()
    stop = time.time() + a.minutes * 60

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])

    # Never switch, never flee: the target must stay in the ring to earn.
    def always_attack(frame):
        moves = frame.get("moves") or []
        best, score = 0, -1.0
        for i, mv in enumerate(moves):
            if not mv or not mv.get("pp"):
                continue
            s = (mv.get("power") or 0) * (mv.get("effect_mult") or 1.0)
            if s > score:
                best, score = i, s
        return ("attack", best)

    for species in [s.strip().upper() for s in a.targets.split(",") if s.strip()]:
        if time.time() > stop:
            break
        before_dex = dexn(d)
        if not to_center(d):
            log.info("no Centre reachable from %s -- stopping", d.map_name())
            break
        st = Storage(d)
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)

        have = next((m for m in party(d) if named(d, m) == species), None)
        if have is None:
            hit = next(((s, mo) for s, mo in dex.boxed()
                        if d.names.species(mo.species).upper() == species), None)
            if hit is None:
                log.info("%s is in neither party nor boxes -- skipping", species)
                continue
            slot, _mo = hit
            while len(party(d)) >= 6:
                v = next((i for i, m in enumerate(party(d))
                          if (m.nickname or "").upper() != BACKUP), None)
                if v is None or not st.deposit(v):
                    break
            if not st.withdraw(slot // 30, slot % 30):
                log.info("could not withdraw %s: %s", species,
                         getattr(st, "last_reason", "?"))
                continue

        if not make_lead(d, st, species):
            log.info("could not make %s the lead -- skipping", species)
            continue
        ensure_backup(d, st, dex)
        try:
            d.heal_at_nearest_center()
        except Exception:  # noqa: BLE001
            pass
        d.save(a.state)
        lead = party(d)[0]
        log.info("=== %s L%s leads, party %s ===", species, lead.level,
                 [(named(d, m), m.level) for m in party(d)])

        sw = Collector(d, feed_name="lead")
        sw.base_policy = lambda _p=always_attack: _p
        try:
            d.travel(a.route)
        except TravelError as e:
            log.info("  travel: %s", str(e)[:80])
        deadline = time.time() + a.per_target * 60
        while time.time() < min(deadline, stop):
            cur = next((m for m in party(d) if named(d, m) == species), None)
            if cur is None:
                log.info("  %s is gone from the party -- EVOLVED", species)
                break
            if (cur.hp or 0) == 0:
                log.info("  %s is down -- Centre", species)
                if not to_center(d):
                    break
                try:
                    d.travel(a.route)
                except TravelError:
                    break
                continue
            try:
                sw.pace_map(min(deadline, stop), terrain="grass")
            except Exception as e:  # noqa: BLE001
                log.info("  pace_map: %s", str(e)[:90])
                break
            now = dexn(d)
            if now > before_dex:
                log.info("  *** DEX %d -> %d ***", before_dex, now)
                break
        d.save(a.state)
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        log.info("  after %s: %s", species, dex.summary(d.state).split(";")[0])

    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("FINAL %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])
    d.save(a.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
