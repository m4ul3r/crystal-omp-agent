#!/usr/bin/env python
"""What is actually missing, split by HOW it is obtained.

The raw "101 to go" hides the shape of the problem: most of it is
evolutions of Pokemon already catchable, which need levels or stones rather
than routing. This prints the split so effort goes where the entries are.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402


def classify(e) -> str:
    if getattr(e, "encounters", ()):
        return "wild"
    notes = " ".join(getattr(e, "notes", ()) or ()).lower()
    if "evolve" in notes:
        if "stone" in notes:
            return "evolve-stone"
        return "evolve-level"
    if "fossil" in notes or "revive" in notes:
        return "fossil"
    if "gift" in notes or "receive" in notes or "egg" in notes:
        return "gift"
    if "trade" in notes:
        return "trade"
    return "other"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    d = Driver(a.state)
    d.advance_scene(20_000)
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    st = d.state

    print(t.summary(st))
    missing = t.missing(st)
    owned = set(t.owned_species(st) or ())

    buckets: dict[str, list] = {}
    for e in missing:
        buckets.setdefault(classify(e), []).append(e)

    out = {}
    for kind in sorted(buckets, key=lambda k: -len(buckets[k])):
        rows = buckets[kind]
        print(f"\n=== {kind}: {len(rows)} ===")
        entries = []
        for e in rows:
            notes = "; ".join(getattr(e, "notes", ()) or ())
            where = ""
            if getattr(e, "encounters", ()):
                seen = []
                for enc in e.encounters[:4]:
                    m = getattr(enc, "map_name", None) or getattr(
                        enc, "area", None) or str(enc)
                    if m not in seen:
                        seen.append(str(m))
                where = ", ".join(seen)
            # For an evolution, say whether we already hold the PREVIOUS stage.
            have_pre = None
            for pre in (getattr(e, "evolves_from", None) or []):
                have_pre = pre in owned
            entries.append({"name": e.name, "dex": e.dex,
                            "notes": notes, "where": where})
            print(f"  {e.name:<12} {where or notes[:74]}")
        out[kind] = entries

    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
