#!/usr/bin/env python
"""Probe: party, Route 111 grass geography, catch rates."""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

d = Driver("saves/d111.state")
t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
for mon in d.state.party():
    print("MON", mon)
for sid in (27, 332, 344, 318):
    try:
        sd = d.names.species_data(sid)
        print("SPECIES", sid, getattr(sd, "name", "?"),
              "catch_rate", sd.catch_rate)
    except Exception as exc:
        print("SPECIES", sid, "ERR", str(exc)[:70])
grass = d.nav.find_tiles("Route111", "grass")
print("Route111 grass cells:", len(grass))
ys = sorted({y for _x, y in grass})
print("y values:", ys)
print("blocked cells:", sorted(d.nav.blocked.get("Route111") or ()))
print("exits:", [(e.get("kind"), e.get("dest"), e.get("lands_at"))
                 for e in d.nav.exits("Route111")])
