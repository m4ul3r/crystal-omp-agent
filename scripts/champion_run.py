#!/usr/bin/env python
"""One process: into the League, through all four, then the Champion.

WHY THIS EXISTS. Splitting the job between `elite_four.py` (a subprocess
that walks the rooms) and an in-process Champion loop threw the run away
every single time, and the reason is in the ROM: leaving the League clears
VAR_ELITE_4_STATE and every FLAG_DEFEATED_ELITE_4_*. Measured immediately
after a gauntlet that had beaten all four -- NINJA L58->60, EMBER L58->60,
so the battles genuinely happened:

    at EverGrandeCity (18, 7)
    FLAG_DEFEATED_ELITE_4_SYDNEY     False
    FLAG_DEFEATED_ELITE_4_PHOEBE     False
    FLAG_DEFEATED_ELITE_4_GLACIA     False
    FLAG_DEFEATED_ELITE_4_DRAKE      False
    VAR_ELITE_4_STATE 0

Four won fights, discarded one corridor from the Champion, because the
subprocess walked out of the building before the Champion loop reopened the
save. `elite_four.py`'s own comment warns about exactly this and it still
happened, because the boundary was a process boundary.

So this does the whole thing without ever stepping outside, and the EXP.
SHARE goes on the target FIRST so the levels land where the dex needs them.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from evolve_chain import (  # noqa: E402
    give_share_to_target, steven_fights, walk_league_chain,
)

log = logging.getLogger("champion_run")


def dex_count(dex, state) -> int:
    import re

    m = re.search(r"dex (\d+)/", dex.summary(state))
    return int(m.group(1)) if m else -1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--target", default=None,
                    help="species to hand the EXP. SHARE to")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--protect-bench", action="store_true",
                    help="never switch, so a benched EXP. SHARE holder stays "
                         "out of the ring and survives to collect")
    ap.add_argument("--minutes", type=float, default=120.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    if a.protect_bench:
        # THE CHAMPION IS THE BEST EXP IN THE GAME AND IT IS REPEATABLE.
        # Steven's mons are L77-79 against the Elite Four's L46-57, and
        # `steven_fights` documents 14,161 exp a run with half of it going to
        # the benched share holder -- roughly three times what a full Elite
        # Four lap was paying. But `tactics.recommend` ranks a resist-switch
        # above damage and will front the holder itself, and a fainted mon
        # earns nothing, so the veto is what makes this usable.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from elite_four import protect_bench_policy, champion_policy

        d.battle_policy = protect_bench_policy(d, champion_policy(d))
        log.info("protect-bench: switching vetoed for the share holder")
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    before = dex_count(dex, d.state)
    log.info("start %s %s | dex %d", d.map_name(), d.pos(), before)
    log.info("party %s", [(m.nickname, m.level) for m in d.state.party()
                          if not m.is_egg])

    if a.target:
        nick = give_share_to_target(d, a.target)
        if nick is None:
            log.info("could not put the share on %s -- continuing anyway, "
                     "the fighters still level", a.target)
        d.save(a.state)

    # INTO THE BUILDING, and then never out of it until the Champion is done.
    if not d.map_name().startswith("EverGrandeCity_"):
        try:
            import league_loop

            league_loop.into_hall(d)
            log.info("entered %s %s", d.map_name(), d.pos())
        except Exception as exc:  # noqa: BLE001
            log.info("into_hall: %s", str(exc)[:110])

    deadline = time.time() + a.minutes * 60.0
    fought = 0
    while time.time() < deadline and fought < a.rounds:
        if not walk_league_chain(d):
            log.info("could not reach the Champion from %s", d.map_name())
            break
        n = steven_fights(d, rounds=1)
        if not n:
            break
        fought += n
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        now = dex_count(dex, d.state)
        log.info("after champion run %d: dex %d | party %s", fought, now,
                 [(m.nickname, d.names.species(m.species), m.level)
                  for m in d.state.party() if not m.is_egg])
        d.save(a.state)
        if now > before:
            log.info("*** DEX %d -> %d ***", before, now)
            before = now

    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("RESULT %d champion fights | %s", fought,
             dex.summary(d.state).split(";")[0])
    log.info("party %s", [(m.nickname, d.names.species(m.species), m.level)
                          for m in d.state.party() if not m.is_egg])
    d.save(a.state)
    return 0 if fought else 1


if __name__ == "__main__":
    raise SystemExit(main())
