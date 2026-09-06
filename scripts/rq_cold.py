#!/usr/bin/env python
"""Throwaway cold read: bag/bike/dex state for the Rayquaza run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

state = sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state"
d = Driver(state, live=False)
if d.at_title():
    print("title screen; resuming ->", d.resume_from_title())
print("map", d.map_name(), d.pos(), "elev", d.elevation())
print("money", d.state.money())
for pocket, items in d.state.bag().items():
    print("BAG", pocket, dict(items))
print("party:")
for i, m in enumerate(d.state.party()):
    print(" ", i, m)
t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
caught, seen = t.dex_flags(d.state)
print("dex caught", len(caught), "seen", len(seen))
print("RAYQUAZA caught?", "RAYQUAZA" in {str(c) for c in caught})
print("flag FLAG_HIDE_RAYQUAZA", d.state.flag("FLAG_HIDE_RAYQUAZA"))
print("flag FLAG_SYS_GAME_CLEAR", d.state.flag("FLAG_SYS_GAME_CLEAR"))
