#!/usr/bin/env python
"""Throwaway: what does the dex132 fork hold before the Rayquaza run?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

d = Driver("saves/rq.state")
if d.at_title():
    print("title screen; resuming")
    print("resume:", d.resume_from_title())
print("map", d.map_name(), d.pos(), "elev", d.elevation())
print("money", d.state.money())
bag = d.state.bag()
for pocket, items in bag.items():
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
