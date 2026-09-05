#!/usr/bin/env python
"""Evolve the party's past-threshold mons by LETTING THEM FIGHT.

Gen 3 runs `TryEvolvePokemon` only on a level-up, and four mons already in
the party sit past their thresholds: MARILL L25 (Azumarill@18), NATU L25
(Xatu@25), CHINCHOU L29 (Lanturn@27), SURSKIT L24 (Masquerain@22). Each
needs ONE level and nothing else.

Every earlier attempt paid them from the bench with the EXP. SHARE, which is
now lost inside a box on a mon that was deposited still wearing it. That
apparatus was never needed: a L22-29 mon that PARTICIPATES against a
low-level wild earns the level itself and survives comfortably.

Reuses the sweeper's own map walker (`collect.Sweeper.pace_map`) rather than
inventing a verb -- `d.pace` does not exist here, and assuming it did cost a
run. The only change is the battle policy: switch the target in, then swing.
"""
import argparse, logging, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver, TravelError
from pokeagent.dex import DexTarget
from collect import Collector

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("lvl")

WANT = ("MARILL", "SURSKIT", "NATU", "CHINCHOU")


def find(d, species):
    for i, m in enumerate(d.state.party()):
        if not m.is_egg and d.names.species(m.species).upper() == species:
            return i, m
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--route", default="Route117")
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--per-target", type=float, default=25.0)
    a = ap.parse_args()
    stop = time.time() + a.minutes * 60

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])
    log.info("party %s", [(d.names.species(m.species), m.level)
                          for m in d.state.party() if not m.is_egg])

    sw = Collector(d, feed_name="line3")

    # REVIVE FIRST. Every target came back from the Elite Four gauntlet at 0
    # HP, so `('switch', i)` was refused all run with "MARILL (slot 1) has
    # fainted" -- 13 minutes of wilds killed by the sweeper while the mon that
    # needed the experience sat unconscious on the bench. A fainted mon earns
    # nothing and cannot be sent in; the Centre is the whole fix.
    try:
        d.heal_at_nearest_center()
        log.info("healed at %s", d.map_name())
    except Exception as e:  # noqa: BLE001
        log.info("heal: %s", str(e)[:90])
    log.info("hp %s", [(d.names.species(m.species), m.hp, m.level)
                       for m in d.state.party() if not m.is_egg])

    try:
        d.travel(a.route)
    except TravelError as e:
        log.info("travel: %s", str(e)[:90])
    log.info("at %s", d.map_name())

    for species in WANT:
        if time.time() > stop:
            break
        idx, mon = find(d, species)
        if idx is None:
            log.info("%s is not in the party", species)
            continue
        lvl0 = mon.level
        log.info("=== %s L%s -> needs one level ===", species, lvl0)

        inner = sw.base_policy()

        def policy(frame, _sp=species, _inner=inner):
            i, m = find(d, _sp)
            me = frame.get("me") or {}
            if i is not None and (me.get("nickname") or "").upper() != \
                    (m.nickname or "").upper():
                if frame.get("can_switch"):
                    return ("switch", i)
            return ("attack", 0)

        # `fight()` calls `self.base_policy()`, so shadow that -- there is no
        # policy_override attribute (checked; inventing one is how this file
        # already lost a run to `d.pace`).
        sw.base_policy = lambda _p=policy: _p
        deadline = time.time() + a.per_target * 60
        while time.time() < min(deadline, stop):
            # RE-HEAL WHENEVER THE TARGET IS DOWN. Healing once at the start
            # was not enough: Route 117 fields ROSELIA and ODDISH, the target
            # gets poisoned on the turn it is switched in, and then faints
            # from the WALKING between encounters. After that every switch is
            # refused with "MARILL (slot 1) has fainted" -- which was true and
            # which I first read as a false report, because `hp` had printed
            # 75/75 minutes earlier. A benched mon at 0 HP earns nothing.
            _i, cur = find(d, species)
            if cur is not None and (cur.hp or 0) == 0:
                log.info("  %s is down -- back to the Centre", species)
                try:
                    d.heal_at_nearest_center()
                except Exception as e:  # noqa: BLE001
                    log.info("  heal: %s", str(e)[:80])
                    break
                try:
                    d.travel(a.route)
                except TravelError as e:
                    log.info("  travel back: %s", str(e)[:80])
                    break
            try:
                sw.pace_map(min(deadline, stop), terrain="grass")
            except Exception as e:
                log.info("  pace_map: %s", str(e)[:90])
                break
            _i, now = find(d, species)
            if now is None:
                log.info("  %s left the party -- likely EVOLVED", species)
                break
            if (now.level or 0) > (lvl0 or 0):
                log.info("  %s L%s -> L%s", species, lvl0, now.level)
                break
        d.save(a.state)
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        log.info("  dex now %s", dex.summary(d.state).split(";")[0])

    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("FINAL %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])
    log.info("party %s", [(d.names.species(m.species), m.level)
                          for m in d.state.party() if not m.is_egg])
    d.save(a.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
