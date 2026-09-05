#!/usr/bin/env python
"""Level the team EVENLY, by putting whoever is furthest behind in front.

This exists to undo a deliberate mistake. `scripts/sea_grind.py` switched one
named mon in on turn one of every battle to funnel all XP into it, because the
badge-8 wall needed a single super-effective attacker. It worked -- PELIPPER
reached L100 -- and it traded away the run's team-balance objective: the rest
of the party sat at 46-53 while one mon took everything.

The mechanism was never the problem, only its target. Here the switch picks
the LOWEST-level healthy party member instead, so experience flows to whoever
needs it, and a mon at the party's ceiling is simply never chosen.

Trainers are the XP source, not wilds: a route trainer is worth many times a
ZIGZAGOON, and beating them pays prize money at the same time (see
`trainer_farm.py` for why that matters -- wild battles pay nothing at all).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
import trainer_farm  # noqa: E402

log = logging.getLogger("balance")


def spread(d) -> list[tuple[str, int]]:
    return [(m.nickname, m.level) for m in d.state.party()]


def laggard(d, ceiling: int | None = None):
    """(index, mon) of the lowest-level healthy member worth training."""
    best = None
    for i, m in enumerate(d.state.party()):
        if getattr(m, "is_egg", False) or not m.hp:
            continue
        if ceiling is not None and m.level >= ceiling:
            continue
        if best is None or m.level < best[1].level:
            best = (i, m)
    return best


def make_policy(d, ceiling):
    """Switch the laggard in on turn one, then let tactics fight.

    Fresh per battle: holding the "already switched" flag in a closure across
    battles is what made an earlier version switch once and never again, and
    ten minutes of fighting left the target at exactly the level it started
    (journal port-48).
    """
    done = {"switched": False}

    def policy(frame):
        me = (frame or {}).get("me") or {}
        active = me.get("nickname") or me.get("name")
        pick = laggard(d, ceiling)
        if pick is None:
            return None
        idx, mon = pick
        if active != mon.nickname and not done["switched"]:
            done["switched"] = True
            return ("switch", idx)
        return None            # tactics picks the move

    return policy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--per-map", type=float, default=600.0)
    ap.add_argument("--ceiling", type=int, default=60,
                    help="stop training a mon once it reaches this level; "
                         "keeps the team together instead of making a second "
                         "L100")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    out = a.out or a.state
    deadline = time.time() + a.minutes * 60
    log.info("START %s levels %s", d.map_name(), spread(d))

    # Fight trainers with the laggard in front. `farm_map` plays each battle
    # through the driver, so the policy is installed on the Driver itself.
    original = Driver.damage_first
    for route in trainer_farm.ROUTES:
        if time.time() > deadline:
            break
        pick = laggard(d, a.ceiling)
        if pick is None:
            log.info("every member is at the ceiling (%d)", a.ceiling)
            break
        log.info("training %s (L%d) on %s", pick[1].nickname, pick[1].level,
                 route)
        try:
            if d.map_name() != route and not d.travel(route, on_battle="fight"):
                continue
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=make_policy(d, a.ceiling))
            if d.map_name() != route:
                continue

        # Patch the driver's default policy for this leg so every battle the
        # farm starts sends the laggard out first.
        Driver.damage_first = staticmethod(
            lambda frame, _d=d: make_policy(_d, a.ceiling)(frame) or
            original(frame))
        try:
            trainer_farm.farm_map(d, route, min(a.per_map,
                                                deadline - time.time()))
        finally:
            Driver.damage_first = original
        log.info("  levels now %s", spread(d))
        d.save(out)

    log.info("DONE levels %s", spread(d))
    d.save(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
