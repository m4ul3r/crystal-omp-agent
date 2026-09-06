#!/usr/bin/env python
"""Cold-read a save and answer, from `dex_flags`, whether a species is CAUGHT.

Not from a mid-run log line: `DexTarget.dex_flags` returns (caught, seen) as
NATDEX ids, so the check has to go species name -> species id -> natdex ->
membership. (`DexTarget.missing()` yields entry OBJECTS while
`Collector.missing()` yields species IDS; comparing across the two is
silently always-False.)

    scripts/stone_verify.py saves/stone-out.state NINETALES STARMIE DELCATTY
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402


def main() -> int:
    path, want = sys.argv[1], [s.upper() for s in sys.argv[2:]]
    d = Driver(path)
    d.advance_scene(20_000)
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    caught, seen = t.dex_flags(d.state)
    print("state", path, "map", d.map_name(), d.pos())
    print("summary", t.summary(d.state))
    print("caught", len(caught), "seen", len(seen))
    by_name = {}
    for sid in range(1, 412):
        try:
            by_name[d.names.species(sid).upper()] = sid
        except Exception:  # noqa: BLE001
            continue
    for name in want:
        sid = by_name.get(name)
        entry = t.by_species.get(sid) if sid else None
        nat = getattr(entry, "natdex", None)
        print(f"{name}: species={sid} natdex={nat} "
              f"CAUGHT={bool(nat and nat in caught)} "
              f"SEEN={bool(nat and nat in seen)}")
    print("party", [(d.names.species(m.species), m.level, m.nickname)
                    for m in d.state.party() if not m.is_egg])
    print("bag items", (d.state.bag().get("items") or {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
