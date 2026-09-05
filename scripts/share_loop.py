#!/usr/bin/env python
"""Bank the by-level evolution dex entries, one Elite Four lap at a time.

The mechanism, proven: hand the EXP. SHARE to a pre-evolution, BENCH it
behind the level-100, and let the lead sweep. The ROM pays the holder
`expYield * level / 7 / 2` per knockout whether or not it was sent out
(`battle_script_commands.c:3381-3441`) -- about half of every Elite Four mon.
MARILL L25 evolved into AZUMARILL inside the FIRST room; dex 88 -> 89.

Three failure modes are designed out, each measured first:

* the holder must not lead -- a L25 in front of Sidney's L46 MIGHTYENA is
  knocked out on turn one, and a fainted mon is skipped by both the
  participant count and the exp loop, so the lap pays nothing;
* the harness's own `tactics.recommend` ranks a resist-switch above damage and
  will front the holder itself (a benched MARILL resists SHARPEDO's Water),
  hence `--protect-bench`, which vetoes switching;
* an item stays with a deposited mon and leaves the bag, so the share has to
  be unequipped with `Teacher.take_from_mon` before it can move on.

Targets are re-derived from `gEvolutionTable` every lap, cheapest first, so
the loop follows the live cheapest entry instead of a list that goes stale as
mons evolve.
"""
import argparse, logging, subprocess, sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver
from pokeagent.dex import DexTarget

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("loop")

HERE = __file__.rsplit("/", 1)[0]


def owned_and_targets(state, budget):
    """(dex_count, [(need, species, level, becomes)]) read from a fresh boot."""
    d = Driver(state)
    d.advance_scene(20_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    own = set(dex.owned_species(d.state))
    et = dex.evolutions
    mons = [m for m in d.state.party() if not m.is_egg]
    lv = {id(m): (m.level or 0) for m in mons}
    for _s, mo in dex.boxed():
        mons.append(mo)
        lv[id(mo)] = dex.boxed_level(mo)
    rows, seen = [], set()
    for m in mons:
        for e in et._forward.get(m.species, ()):
            if not e.by_level or e.to_species in own or e.to_species in seen:
                continue
            need = max(0, e.param - lv[id(m)])
            if need > budget:
                continue
            seen.add(e.to_species)
            rows.append((need, d.names.species(m.species).upper(),
                         lv[id(m)], d.names.species(e.to_species)))
    # RANK BY THE LEVEL IT HAS TO REACH, NOT BY LEVELS NEEDED.
    # Experience scales with the cube of level, so "+2 levels" on a L30
    # HORSEA costs far more than "+5 levels" on a L5 CASCOON. Sorting by
    # levels-needed put the expensive one first and spent laps on L36-L40
    # mons that cannot make a level in one gauntlet, while CASCOON L5 -> 10
    # and SILCOON L5 -> 10 sat in the queue behind them.
    rows.sort(key=lambda r: (r[2] + r[0], r[0]))
    n = len(own)
    del d
    return n, rows


def run(args, minutes):
    try:
        return subprocess.run(args, cwd=HERE.rsplit("/scripts", 1)[0],
                              timeout=minutes * 60).returncode
    except subprocess.TimeoutExpired:
        log.info("  timed out: %s", " ".join(args[-4:]))
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=600.0)
    ap.add_argument("--per-lap", type=float, default=16.0)
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--passes", type=int, default=3,
                    help="gauntlet passes per staging; the rooms re-arm on "
                         "leaving, so this amortises the staging cost")
    a = ap.parse_args()
    stop = time.time() + a.minutes * 60
    py = sys.executable

    lap = 0
    failed = {}
    while time.time() < stop:
        lap += 1
        n, rows = owned_and_targets(a.state, a.budget)
        if not rows:
            log.info("no by-level targets within +%d -- widening", a.budget)
            a.budget += 8
            n, rows = owned_and_targets(a.state, a.budget)
            if not rows:
                log.info("nothing left to evolve by level")
                break
        # SKIP WHAT HAS ALREADY FAILED TWICE. The first version of this loop
        # re-picked LOUDRED every lap and burned four laps on it, because the
        # share was landing on the level-100 and nothing could ever change.
        pick = next((r for r in rows if failed.get(r[1], 0) < 2), None)
        if pick is None:
            log.info("every target in range has failed twice -- widening")
            a.budget += 8
            continue
        need, species, level, becomes = pick
        log.info("=== lap %d | dex %d | %s L%s -> %s (+%d) | queue %s ===",
                 lap, n, species, level, becomes, need,
                 [r[1] for r in rows[1:5]])

        rc = run([py, "scripts/share_grind.py", "--state", a.state,
                  "--target", species], minutes=13)
        if rc != 0:
            failed[species] = failed.get(species, 0) + 1
            log.info("  staging failed (rc=%d)", rc)
            # AN ELITE FOUR ROOM SEALS UNTIL ITS MEMBER IS BEATEN. A gauntlet
            # interrupted mid-fight leaves the save inside PhoebesRoom with
            # BOTH doors shut -- the entry door closes behind you at runtime
            # and the exit only opens on victory -- so no amount of warp
            # walking or grid syncing gets out, and staging can only report
            # "no Centre reachable". The way out is to fight, which
            # elite_four.py already does from wherever it stands.
            log.info("  running a gauntlet to free the save")
            run([py, "scripts/elite_four.py", "--state", a.state,
                 "--protect-bench", "--minutes", str(a.per_lap),
                 "--feed", "default"], minutes=a.per_lap + 6)
            continue
        # SEVERAL PASSES PER STAGING. Staging costs ~4 minutes (fly, PC, take
        # and give the share, fly back), so one gauntlet per staging spends
        # most of the lap on overhead. The four Elite Four rooms RE-ARM when
        # the building is left -- their flags are cleared on the way out -- so
        # the walk can simply be repeated, and each pass is ~20 knockouts of
        # L46-57 with half the experience going to the benched holder.
        #
        # The Champion is not used: `walk_league_chain` reports "door (6,2) is
        # shut -- engaging the trainer" for each member and then leaves the
        # building rather than reaching Corridor4, so `steven_fights` returned
        # `RESULT 0 champion fights` every lap while the four members supplied
        # all of the experience anyway (SHROOMISH L5 -> L16 in one pass).
        for pass_no in range(a.passes):
            run([py, "scripts/elite_four.py", "--state", a.state,
                 "--protect-bench", "--minutes", str(a.per_lap),
                 "--feed", "default"], minutes=a.per_lap + 6)
            if owned_and_targets(a.state, a.budget)[0] > n:
                log.info("  banked on pass %d -- next target", pass_no + 1)
                break

        after, rows_after = owned_and_targets(a.state, a.budget)
        if after > n:
            failed.pop(species, None)
            log.info("  *** DEX %d -> %d (%s) ***", n, after, becomes)
        else:
            # A STRIKE ONLY COUNTS IF NO LEVELS WERE GAINED. One lap is worth
            # several levels to a low-level holder but not always enough to
            # cross a threshold -- RALTS went L4 -> L12 (needs 20) and
            # SANDSHREW L14 -> L17 (needs 22), and a flat two-strike rule
            # benched both while they were plainly working. Progress earns
            # another lap; only a mon that gained nothing is abandoned.
            now_level = next((r[2] for r in rows_after if r[1] == species), None)
            if now_level is not None and now_level > level:
                log.info("  %s L%s -> L%s, no evolution yet -- another lap",
                         species, level, now_level)
            else:
                failed[species] = failed.get(species, 0) + 1
                log.info("  no gain at all (%s stayed at L%s, %d strike(s))",
                         species, level, failed[species])

    n, _ = owned_and_targets(a.state, a.budget)
    log.info("FINAL dex %d", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
