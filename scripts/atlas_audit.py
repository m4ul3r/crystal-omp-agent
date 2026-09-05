#!/usr/bin/env python
"""Find every place the movement model says you cannot go -- before the run does.

Tonight's walls were all found the same expensive way: the loop walked at
something for half an hour, and a person eventually noticed. Every one of them
was visible statically, because they share one shape -- *a cell the game needs
you to reach that the model says you cannot*:

* Lavaridge's gym: Flannery unreachable from the door (a trainer on a spring).
* Petalburg's gym: Norman unreachable from the lobby (A-press doors).
* Route 114: Meteor Falls' door unreachable (elevation 0 modelled as a bridge).
* Route 119: the Weather Institute unreachable (elevation missing from the
  BFS state, so a bridge cell closed the road it carried).

So: enumerate what each map WANTS you to reach -- its warps, its coord_event
triggers, its object_events -- and ask whether any legitimate entry point can
reach it. Report what cannot. A precomputed atlas is not for speed (the whole
region decodes in about a second); it is a fence that makes a severed map
loud instead of silent.

This is deliberately a REPORT, not an assertion: plenty of unreachable cells
are legitimate (a gate you have not opened, an island needing SURF you do not
have, a one-way ledge pocket). The value is the diff -- run it before and after
a movement change and read what moved.

    scripts/atlas_audit.py saves/live-run.state
    scripts/atlas_audit.py saves/live-run.state --map Route119 --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import paths  # noqa: E402


def targets_of(map_name):
    """What this map wants the player to reach, from its own map.json.

    Warps are doors and stairs; coord_events are the cutscene triggers the
    story chain steps onto; object_events are the people it talks to. Nothing
    here is a guess -- it is the map's own event table.
    """
    path = paths.MAPS / map_name / "map.json"
    if not path.exists():
        return {}
    try:
        j = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for w in j.get("warp_events") or ():
        out[(int(w["x"]), int(w["y"]))] = f"warp->{w.get('dest_map', '?')}"
    for c in j.get("coord_events") or ():
        if c.get("script"):
            out[(int(c["x"]), int(c["y"]))] = f"trigger {c['script']}"
    for o in j.get("object_events") or ():
        if o.get("script") and o.get("script") != "0x0":
            out.setdefault((int(o["x"]), int(o["y"])),
                           f"object {o.get('graphics_id', '?')}")
    return out


def entries_of(nav, map_name):
    """Every cell the player can legitimately ARRIVE on.

    A map is not entered from an arbitrary tile: you land on a warp's
    destination cell or you cross a seam. Auditing reachability from anywhere
    else invents journeys the game never offers.
    """
    cells = set()
    try:
        info = nav.info(map_name)
    except Exception:  # noqa: BLE001
        return cells
    for w in info.warps:                      # arriving through a door
        cells.add((w.x, w.y))
    for direction in "UDLR":                  # arriving across a seam
        try:
            for cell in nav.edge_cells(map_name, direction):
                cells.add(cell)
        except Exception:  # noqa: BLE001
            continue
    return cells


def _can_engage(nav, map_name, cell, reach) -> bool:
    """Can the player interact with `cell` from somewhere in `reach`?

    Three ways, and the last one is why every Pokemon Centre nurse in the
    region showed up "unreachable" on the first run: you talk to her ACROSS
    the counter. `talk_to` already handles that; the audit has to model the
    same reach or it drowns its own signal in fifty nurses.
    """
    x, y = cell
    if cell in reach:                                   # stand on it
        return True
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        if (x + dx, y + dy) in reach:                   # stand beside it
            return True
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        between = nav.cell(map_name, x + dx, y + dy)
        beyond = (x + 2 * dx, y + 2 * dy)
        if between is not None and not between.passable and beyond in reach:
            return True                                 # talk over a counter
    return False


def audit_map(nav, map_name, verbose=False):
    """(unreachable, total) for one map, plus the rows worth printing."""
    want = targets_of(map_name)
    if not want:
        return 0, 0, []
    entries = entries_of(nav, map_name)
    if not entries:
        return 0, len(want), []

    # One fill per entry, unioned: "can ANY legitimate arrival reach it".
    reach = set()
    for cell in entries:
        try:
            reach |= nav.reachable(map_name, cell)
        except Exception:  # noqa: BLE001
            continue

    rows = []
    for cell, what in sorted(want.items()):
        if _can_engage(nav, map_name, cell, reach):
            continue
        rows.append((cell, what))
    if verbose:
        rows = rows or []
    return len(rows), len(want), rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("state", help="a savestate; its flags decide live gates")
    ap.add_argument("--map", help="audit one map instead of all of them")
    ap.add_argument("--surf", action="store_true",
                    help="assume SURF (default: read it off the save)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    from pokeagent.trek import Driver

    d = Driver(args.state)
    nav = d.nav
    nav.surfing = True if args.surf else d.can_surf()
    nav._reach_cache.clear()
    print(f"atlas audit of {args.state}: surfing={nav.surfing}, "
          f"{len(nav.index)} maps")

    names = [args.map] if args.map else sorted(nav.index)
    worst = []
    total_bad = total_want = 0
    for name in names:
        bad, want, rows = audit_map(nav, name, args.verbose)
        total_bad += bad
        total_want += want
        if bad:
            worst.append((bad, name, rows))
    worst.sort(reverse=True)

    for bad, name, rows in worst:
        print(f"\n{name}: {bad} unreachable")
        for cell, what in rows[:12 if not args.verbose else 999]:
            print(f"    {cell} {what}")
    print(f"\n{total_bad} unreachable of {total_want} event cells across "
          f"{len(names)} map(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
