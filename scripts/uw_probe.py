#!/usr/bin/env python
"""Throwaway probe: what the uw fork knows about DIVE, RELICANTH, WAILORD."""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")

d = Driver(sys.argv[1] if len(sys.argv) > 1 else "saves/uw.state")
t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
print("map", d.map_name(), d.pos(), "title?", d.at_title())
print("summary:", t.summary(d.state))
print("field moves:", d.field_moves())
print("can_dive", d.can_dive(), "badge7", d.state.flag("FLAG_BADGE07_GET"))
print("party:")
for i, m in enumerate(d.state.party()):
    print("  ", i, d.names.species(m.species), m.nickname,
          getattr(m, "level", None))

caught, seen = t.dex_flags(d.state)
for want in ("RELICANTH", "WAILORD", "WAILMER", "LOMBRE", "LUDICOLO",
             "CLAMPERL", "CHINCHOU", "LUVDISC", "CORPHISH"):
    sid = d.consts.species.get("SPECIES_" + want)
    nat = t.evolutions.natdex(sid) if sid else None
    print(f"  {want}: id={sid} nat={nat} caught={nat in caught} "
          f"seen={nat in seen}")

print("boxed divers / relevant:")
hm08 = d.consts.items.get("ITEM_HM08")
for slot, mon in t.boxed():
    nm = d.names.species(mon.species).upper()
    if nm in ("LOMBRE", "LUDICOLO", "WAILMER", "WAILORD", "TENTACRUEL",
              "SHARPEDO", "WAILMER"):
        print("  slot", slot, "box", slot // 30, "sl", slot % 30, nm,
              "L", t.boxed_level(mon), "nick", mon.nickname,
              "moves", [d.names.move(mv) for mv in getattr(mon, "moves", [])])

print("wild tables:")
for mp in ("Underwater1", "Underwater2", "Underwater3", "Underwater4",
           "Route124", "Route126", "Route129", "Route128"):
    try:
        rows = t.wild.for_map(mp)
    except Exception as exc:  # noqa: BLE001
        print("  ", mp, "ERR", exc)
        continue
    if not rows:
        continue
    agg = {}
    for r in rows:
        agg.setdefault(r.kind, set()).add(d.names.species(r.species))
    print("  ", mp, {k: sorted(v) for k, v in agg.items()})
