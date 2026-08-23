#!/usr/bin/env python3
"""Encounter-table reader for the pokecrystal disassembly.

Parses data/wild/johto_grass.asm and johto_water.asm into per-map tables.
Grass blocks carry three rate slots and rows grouped by `; morn|day|nite`
comments; water blocks carry a single rate and one time-independent list.

Standalone on purpose (no crystalagent imports): safe to edit/run while
other agents hold trek.py open.

Usage:
  wilds.py ROUTE_30 [MAP...]     print those maps' full tables
  wilds.py --grep WOOPER         every map whose table contains WOOPER
  wilds.py                       one-line summary of every mapped table
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

# scripts/ -> crystal-agent/ -> pokecrystal repo root
WILD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "wild"

OPEN_RE = re.compile(r"^def_(grass|water)_wildmons\s+([A-Za-z0-9_]+)\s*$")
CLOSE_RE = re.compile(r"^end_(grass|water)_wildmons\s*$")
ROW_RE = re.compile(r"^db\s+(\d+)\s*,\s*([A-Za-z0-9_]+)\s*$")
PERIOD_RE = re.compile(r"^;\s*(morn|day|nite)\b")
RATE_RE = re.compile(r"^db\b")


def parse_file(path):
    """Parse one .asm into {map_const: block}.

    block = {"kind", "file": stem, "rates": [str], "lists": {key: rows}}.
    Grass lists are keyed morn/day/nite; water has its single unlabeled
    list under "any". Rows are (level, species) tuples in table order."""
    blocks = {}
    cur = None
    key = None
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = OPEN_RE.match(line)
        if m:
            kind, const = m.groups()
            if const in blocks:
                sys.exit(f"{path.name}:{lineno}: duplicate block {const}")
            cur = {"kind": kind, "file": path.stem, "rates": [],
                   "lists": {"any": []} if kind == "water"
                   else {"morn": [], "day": [], "nite": []}}
            blocks[const] = cur
            key = "any" if kind == "water" else None
            continue
        if CLOSE_RE.match(line):
            cur = None
            key = None
            continue
        if cur is None:
            continue
        m = PERIOD_RE.match(line)
        if m:
            key = m.group(1)
            continue
        m = ROW_RE.match(line)
        if m:
            lvl, sp = int(m.group(1)), m.group(2)
            cur["lists"].setdefault(key or "any", []).append((lvl, sp))
            continue
        if RATE_RE.match(line):
            cur["rates"].append(line)
    return blocks


def load_tables():
    """{map_const: [block, ...]} -- a map can have grass AND water tables."""
    tables = {}
    for stem in ("johto_grass", "johto_water"):
        path = WILD_DIR / f"{stem}.asm"
        if path.exists():
            for const, blk in parse_file(path).items():
                tables.setdefault(const, []).append(blk)
    return tables


def fmt_levels(rows, species=None):
    """Level numbers of `rows` (optionally only `species` rows), '3,4,5'."""
    return ",".join(str(l) for l, sp in rows
                    if species is None or sp == species)


def print_table(const, blk):
    print(f"{const} ({blk['kind']}, {blk['file']}.asm)")
    for rate in blk["rates"]:
        print(f"  rates: {rate}")
    if not any(blk["lists"].values()):
        print("  (none)")
        return
    for key, rows in sorted(blk["lists"].items()):
        if not rows:
            continue
        seen = []
        for _, sp in rows:
            if sp not in seen:
                seen.append(sp)
        detail = ", ".join(f"L{l} {sp}" for l, sp in rows)
        print(f"  {key}: {detail}")
        print(f"       species: {' '.join(seen)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("maps", nargs="*", help="map CONST name(s)")
    ap.add_argument("--grep", metavar="SPECIES",
                    help="list every map whose table contains SPECIES")
    args = ap.parse_args(argv)

    tables = load_tables()
    if not tables:
        sys.exit(f"no wild tables parsed from {WILD_DIR}")

    if args.grep:
        want = args.grep.upper()
        hits = []
        for const, blks in sorted(tables.items()):
            for blk in blks:
                spots = [(key, fmt_levels(rows, want))
                         for key, rows in sorted(blk["lists"].items())
                         if any(sp == want for _, sp in rows)]
                if spots:
                    hits.append((const, blk["kind"],
                                 "; ".join(f"{k} L{l}" for k, l in spots)))
        if not hits:
            print(f"(no map lists {want})")
            return
        for const, kind, where in hits:
            print(f"{const} [{kind}] {want}: {where}")
        return

    if not args.maps:
        for const in sorted(tables):
            for blk in tables[const]:
                parts = []
                for key, rows in sorted(blk["lists"].items()):
                    if not rows:
                        continue
                    seen = []
                    for _, sp in rows:
                        if sp not in seen:
                            seen.append(sp)
                    parts.append(f"{key}: {' '.join(seen)}")
                body = " | ".join(parts) if parts else "(none)"
                print(f"{const} [{blk['kind']}] {body}")
        return

    bad = [m for m in args.maps if m not in tables]
    if bad:
        close = sorted({c for m in bad
                        for c in difflib.get_close_matches(
                            m, tables.keys(), n=4, cutoff=0.55)})
        sys.exit(f"unknown map const(s): {', '.join(bad)}\n"
                 f"closest matches: {', '.join(close)}")
    for m in args.maps:
        for i, blk in enumerate(tables[m]):
            if len(args.maps) > 1 or i:
                print()
            print_table(m, blk)


if __name__ == "__main__":
    main()
