#!/usr/bin/env python
"""Run the proven hunt scripts back to back on ONE save so species accumulate.

THE PROBLEM THIS SOLVES
-----------------------
Twelve hunters each forked the canonical save and each banked one to three
species. Savestates are whole-machine snapshots, so two forks CANNOT be
merged -- thirteen species sat on eight incompatible timelines and only one of
them could ever be promoted. Re-running a hunt on the current canonical was
the only integration path, and doing that one script at a time means promoting
between every run.

Every hunt script takes ``--state`` and mutates it IN PLACE, and every one of
them reads the live dex and skips a species already flagged CAUGHT. So they
compose: point them all at the same file, in sequence, and the species pile up
on a single line. That is what this does.

It is deliberately dumb about failure -- a script that dies takes its own
species with it and the chain moves on, because a KOFFING is not worth losing
a RELICANTH over. Each leg is timed, its dex delta recorded, and the state is
copied to a per-leg checkpoint so a crash in leg 7 cannot cost legs 1-6.

    python scripts/chain.py --state saves/chain.state --legs desert,stone,rod

Order matters only for cost: cheap and certain first, so an interrupted chain
still leaves the most species banked.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

log = logging.getLogger("chain")

VENV = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"

#: name -> (script, extra argv). Ordered cheapest-and-most-certain first: an
#: interrupted chain should have banked the sure things already.
LEGS: dict[str, tuple[str, list[str]]] = {
    # 3 species, pure land pacing, no prerequisites.
    "desert": ("scripts/desert111.py", ["--minutes", "22"]),
    # KOFFING: one small cave, ~6 minutes when it went first time.
    "fiery": ("scripts/fiery_jagged.py", ["--minutes", "20"]),
    # NOSEPASS is rock-smash (respawns every map load); SKITTY is a 2% slot.
    "skitty": ("scripts/skitty_nosepass.py", ["--budget", "1500"]),
    # SPHEAL needs low tide forced through TZ; the script does it itself.
    "shoal": ("scripts/shoal_hunt.py", ["--spheal-budget", "900",
                                        "--snorunt-budget", "900"]),
    # SNORUNT, the one species behind the tide AND a STRENGTH boulder. Its own
    # leg because `shoal_hunt`'s Ice Room half cannot land it: the descent
    # from the Lower Room is blocked by the pushable boulder at (25,3), and
    # `pace_map` cannot pace a room whose floor is forced-movement ice. Worth
    # two entries -- GLALIE is the L42 evolution the grind engine then closes.
    "snorunt": ("scripts/snorunt.py", ["--budget", "1800"]),
    # STARYU by rod, then CORSOLA off Route 128.
    "rod": ("scripts/rod_hunt.py", ["--budget", "1500"]),
    # NINETALES / STARMIE / DELCATTY off stones; buys shards as needed.
    "stone": ("scripts/stone_evos.py", ["--mon-budget", "900",
                                        "--stone-budget", "900"]),
    # BELDUM is a gift ball in Steven's house -- certain, and worth 3 entries
    # once the grind reaches METANG/METAGROSS.
    "beldum": ("scripts/fossil_beldum.py", ["--legs", "beldum"]),
    # RELICANTH underwater; needs a DIVE knower withdrawn from a box.
    "underwater": ("scripts/underwater.py", ["--budget", "1500"]),
    # LATIAS is the ROAMER, and it is not a pacing hunt: the leg switches the
    # TV on in Littleroot to run `special InitRoamer` if the struct is blank,
    # then leads a Shadow Tag WOBBUFFET so `AI_Roaming` cannot choose to flee.
    # Idempotent and cheap when there is nothing to do (one dex read).
    "latias": ("scripts/latias.py", ["--minutes", "40"]),
    # MAGNETON is a flat 1% slot: ~1800 steps -- but it is ALSO a MAGNEMITE
    # +3 levels, which the grind engine does for free. Kept for completeness,
    # ordered last, and not worth running while the grind is alive.
    "magneton": ("scripts/magneton.py", ["--minutes", "25"]),
    # SPOINK / ABSOL / CORSOLA. SPOINK also unlocks GRUMPIG for the grind.
    "wildtrio": ("scripts/wild_trio.py", []),
    # AZURILL / IGGLYBUFF / PICHU. ~34 min, certain: every hatch is exactly
    # 11*256 = 2816 real steps (measured 2804 three times, the gap being the
    # steps left when the hatch scene is detected). AZURILL needs a parent
    # holding SEA INCENSE or daycare.c:602-622 silently rewrites it to MARILL.
    "babies": ("scripts/breed2.py", []),
    # SHEDINJA (free party slot) and CROBAT (friendship).
    "crobat": ("scripts/crobat_shedinja2.py", []),
    # RAICHU off a THUNDERSTONE, DELCATTY off a LUNATONE's held Moon Stone.
    "stones2": ("scripts/stones2.py", []),
    # REGIROCK / REGICE / REGISTEEL -- the Sealed Chamber opened once
    # RELICANTH and WAILORD were both caught.
    "regis": ("scripts/regis.py", []),
    # RAYQUAZA at the Sky Pillar apex.
    "rayquaza": ("scripts/rayquaza.py", []),
}

#: Cheapest-and-most-certain first, so an interrupted chain has still banked
#: the sure things. The gift and shard legs are seconds-to-minutes; the wild
#: hunts are minutes; breeding is ~34 min of walking but never fails; the
#: static legendaries go last because they are one entry each and the most
#: likely to need a retry.
#:
#: NOTE "stones2" supersedes "stone" for DELCATTY -- stone_evos' moon-stone
#: hunt spun for 30 minutes on a single battle before the await_action_menu
#: fix, and stones2 resolves an encounter in 2-6s. Both are listed because
#: "stone" also carries NINETALES/STARMIE off shard trades, which never fight.
DEFAULT_ORDER = ["beldum", "desert", "fiery", "wildtrio", "skitty",
                 "shoal", "snorunt", "underwater", "rod", "stone",
                 "stones2", "crobat", "babies", "latias", "regis",
                 "rayquaza", "magneton"]


DEXREAD = r'''
import warnings, sys
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from pokeagent.trek import Driver
from pokeagent import dex as dexmod
d = Driver(sys.argv[1])
t = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
caught, _ = t.dex_flags(d.state)
print("DEX", len(caught), " ".join(str(n) for n in sorted(caught)))
'''


def dex_count(state: str) -> tuple[int, set]:
    """(caught count, natdex set), read in a SUBPROCESS.

    It has to be a subprocess. `Driver(state)` AUTO-ATTACHES a live feed named
    after the save's stem (trek.py:_autofeed) and that claim is held for the
    lifetime of the process. Reading the dex in-process therefore made the
    chain itself the owner of `live/<stem>.owner`, and any leg that builds a
    LiveFeed EXPLICITLY -- the ones taking `--feed` -- hit
    `LiveFeed._claim`'s hard error ("already being written by pid N") and died
    in under a second. Three legs in a row reported rc=1 in 0s for a map full
    of species. Legs relying on autofeed survived because _autofeed swallows
    the claim failure, which is exactly why this took so long to see.
    """
    try:
        p = subprocess.run([str(VENV), "-c", DEXREAD, state],
                           timeout=240, capture_output=True, text=True)
        for line in (p.stdout or "").splitlines():
            if line.startswith("DEX "):
                parts = line.split()
                return int(parts[1]), {int(x) for x in parts[2:]}
        log.info("dex read failed rc=%s: %s", p.returncode,
                 (p.stderr or "").strip()[-200:])
    except subprocess.TimeoutExpired:
        log.info("dex read timed out")
    return -1, set()


NORMALIZE = r'''
import warnings, sys
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from pokeagent.trek import Driver
d = Driver(sys.argv[1])
if d.at_title():
    d.resume_from_title()
d.advance_scene(40000)
for _ in range(6):
    if d.flight.flyable_here():
        break
    try:
        if not d.flight.step_outside():
            break
    except Exception:
        break
if d.flight.flyable_here():
    try:
        d.fly_to("LilycoveCity")
    except Exception:
        pass
try:
    d.heal_at_nearest_center()
except Exception:
    pass
d.save(sys.argv[1])
print("normalized ->", d.map_name(), d.pos())
'''


def normalize(state: str, timeout: float = 300.0) -> str:
    """Put the save somewhere the next hunt can actually start from.

    EVERY LEG INHERITS THE LAST LEG'S POSITION, and that is what made four of
    the first six legs return +0 in seconds. The BELDUM leg ends inside
    Steven's house and the SHOAL leg ends four warps deep in a cave; a hunt
    that opens by flying somewhere is refused outright when it is indoors
    (Overworld_MapTypeAllowsTeleportAndFly), so it exits before hunting
    anything and the chain reads that as "no species here".

    A SUBPROCESS, so a wedged recovery cannot take the chain down -- and so
    its own live-feed claim dies with it, same reason as dex_count.
    """
    try:
        p = subprocess.run([str(VENV), "-c", NORMALIZE, state],
                           timeout=timeout, capture_output=True, text=True,
                           start_new_session=True)
        for line in (p.stdout or "").splitlines():
            if line.startswith("normalized ->"):
                return line
        return f"normalize rc={p.returncode}"
    except subprocess.TimeoutExpired:
        return "normalize timeout"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--legs", default=",".join(DEFAULT_ORDER))
    ap.add_argument("--leg-timeout", type=float, default=2400.0,
                    help="hard seconds per leg; a wedged hunt must not eat "
                         "the whole chain")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s chain %(message)s")

    order = [n.strip() for n in a.legs.split(",") if n.strip()]
    unknown = [n for n in order if n not in LEGS]
    if unknown:
        raise SystemExit(f"unknown legs: {unknown}; known: {sorted(LEGS)}")

    start_n, start_set = dex_count(a.state)
    log.info("START %s at dex %d", a.state, start_n)
    results = []
    for name in order:
        script, extra = LEGS[name]
        if not Path(script).exists():
            log.info("SKIP %s -- %s is missing", name, script)
            continue
        log.info("--- %s", normalize(a.state))
        before_n, before_set = dex_count(a.state)
        t0 = time.time()
        cmd = [str(VENV), script, "--state", a.state, *extra]
        log.info("=== leg %s: %s", name, " ".join(cmd[1:]))
        try:
            # OWN PROCESS GROUP. A leg that times out can leave grandchildren
            # holding the live-feed claim and the emulator, which poisons every
            # later leg; killing the group guarantees it cannot.
            p = subprocess.run(cmd, timeout=a.leg_timeout,
                               capture_output=True, text=True,
                               start_new_session=True)
            rc = p.returncode
            if rc != 0:
                # NEVER SWALLOW THE REASON. The first chain hid three instant
                # failures behind "+0", which read as "no species here".
                tail = (p.stderr or p.stdout or "").strip().splitlines()
                for line in tail[-4:]:
                    log.info("      | %s", line[:160])
        except subprocess.TimeoutExpired:
            rc = "timeout"
        el = time.time() - t0
        after_n, after_set = dex_count(a.state)
        gained = sorted(after_set - before_set)
        # CHECKPOINT EVERY LEG. A chain is only worth running unattended if a
        # crash in leg 7 cannot cost legs 1-6.
        ck = f"{a.state.rsplit('.state', 1)[0]}-{name}.state"
        shutil.copy(a.state, ck)
        meta = Path(a.state + ".meta")
        if meta.exists():
            shutil.copy(meta, ck + ".meta")
        log.info("=== leg %s done rc=%s %.0fs dex %d -> %d (+%d) checkpoint %s",
                 name, rc, el, before_n, after_n, len(gained), ck)
        results.append((name, rc, round(el), before_n, after_n, len(gained)))

    end_n, end_set = dex_count(a.state)
    log.info("CHAIN DONE dex %d -> %d (+%d)", start_n, end_n,
             end_n - start_n)
    for r in results:
        log.info("  %-11s rc=%-8s %5ss  %d -> %d  +%d", *r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
