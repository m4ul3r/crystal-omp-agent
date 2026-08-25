#!/usr/bin/env python
"""Build data/mapgraph.json from pokecrystal map sources.

Nodes are map constants (from constants/map_constants.asm); edges are
warps (parsed from maps/<Map>.asm `def_warp_events` tables) and map-edge
connections (parsed from data/maps/attributes.asm `connection` macros).

Parsing follows the real macros, not pattern guesses:
- `map_const NAME, w, h` (constants/map_constants.asm): GROUP_/MAP_/WIDTH/
  HEIGHT constants; coordinates here are TILE coords = 2 * block coords.
- `warp_event x, y, MAP_ID, dest_warp_id` (macros/scripts/maps.asm):
  db y, x, dest_warp_id then GROUP/MAP ids. dest_warp_id starts at 1;
  -1 is a "back-warp" that returns you where you came from (not routable).
- `connection direction, Name, MAP_ID, offset` (data/maps/attributes.asm):
  offset is the target map's origin relative to the current map (x offset
  for north/south... actually y offset for west/east, x offset for
  north/south per macro comment: "x offset for east/west, y offset for
  north/south" is NOT what the code does -- see below).

Landing math (verified against engine/overworld/warp_connection.asm
EnterMapConnection, matching crystalagent/nav.py which was live-tested):
- warp: stepping on cell (x,y) lands you on the DESTINATION map's own warp
  event numbered dest_warp_id (same cell coordinates there).
- connection walking off edge at (x,y): new coord strips the offset,
  nx = x - 2*offset (north/south) or ny = y - 2*offset (west/east); the
  other coordinate clamps to the destination's far edge.

Run: .venv/bin/python scripts/build_mapgraph.py [--repo PATH]
Writes data/mapgraph.json next to this repo (crystal-agent/data/).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crystalagent.nav import MapData  # noqa: E402 (collision grids/regions)

DEFAULT_REPO = Path(__file__).resolve().parent.parent.parent  # pokecrystal/
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "mapgraph.json"


def parse_map_constants(repo):
    """CONST -> {camel_placeholder fields: group, blocks_w, blocks_h}."""
    maps = {}
    group = None
    path = repo / "constants" / "map_constants.asm"
    for line in path.read_text().splitlines():
        line = line.split(";")[0].rstrip()
        if m := re.match(r"\s*newgroup\s+(\w+)", line):
            group = m.group(1)
        elif m := re.match(r"\s*endgroup", line):
            group = None
        elif m := re.match(r"\s*map_const\s+([A-Z0-9_]+)\s*,\s*(\d+)\s*,\s*(\d+)", line):
            name, w, h = m.group(1), int(m.group(2)), int(m.group(3))
            maps[name] = {
                "group": group,
                "blocks_w": w,
                "blocks_h": h,
                "tiles_w": 2 * w,
                "tiles_h": 2 * h,
            }
    return maps


def parse_attributes(repo):
    """Parse data/maps/attributes.asm: CamelCase <-> CONST pairing and
    per-map connection macros. Returns (camel_of, conns)."""
    camel_of = {}   # CONST -> CamelCase
    conns = {}      # CONST -> {direction: (dest_CONST, offset)}
    cur = None
    path = repo / "data" / "maps" / "attributes.asm"
    for line in path.read_text().splitlines():
        if m := re.match(r"\tmap_attributes\s+(\w+)\s*,\s*([A-Z0-9_]+)\s*,", line):
            cur = m.group(2)
            camel_of[cur] = m.group(1)
            conns.setdefault(cur, {})
            continue
        m = re.match(
            r"\tconnection\s+(north|south|west|east)\s*,\s*(\w+)\s*,\s*"
            r"([A-Z0-9_]+)\s*,\s*(.+?)\s*$", line)
        if m and cur:
            direction, _camel, dest, off = m.groups()
            # Legacy 6-arg form: connection d, Name, MAP, (<a>) - (<b>)
            expr = off.replace(" ", "")
            if lm := re.fullmatch(r"\((-?\w+)\)-\((-?\w+)\)", expr):
                offset = int(lm.group(1), 0) - int(lm.group(2), 0)
            else:
                offset = int(expr, 0)
            conns[cur][direction] = (dest, offset)
    return camel_of, conns


def parse_warps_and_objects(repo, camel_of):
    """Per map (by CamelCase file): warp table and object events.

    Returns:
      warp_cells: CONST -> [(x,y) | None] indexed by warp_id-1
                 (None = back-warp id -1)
      warp_edges: CONST -> {(x,y): (dest_CONST, dest_warp_id)}
      objects:    CONST -> [{x, y, sprite}]
    """
    inv = {camel: const for const, camel in camel_of.items()}
    warp_cells, warp_edges, objects = {}, {}, {}
    for camel, const in inv.items():
        path = repo / "maps" / f"{camel}.asm"
        if not path.exists():
            continue
        cells, edges, objs = [], {}, []
        section = None
        for line in path.read_text().splitlines():
            if re.match(r"\tdef_\w+", line):
                section = line.strip()
                continue
            m = re.match(r"\twarp_event\s+(-?\d+)\s*,\s*(-?\d+)\s*,\s*"
                         r"([A-Z0-9_]+)\s*,\s*(-?\d+)", line)
            if m and section == "def_warp_events":
                x, y = int(m.group(1)), int(m.group(2))
                dest, wid = m.group(3), int(m.group(4))
                if wid >= 1:
                    cells.append((x, y))
                    edges[(x, y)] = (dest, wid)
                else:
                    cells.append(None)  # back-warp: id -1
                continue
            m = re.match(r"\tobject_event\s+(-?\d+)\s*,\s*(-?\d+)\s*,\s*"
                         r"([A-Z0-9_]+)\s*,", line)
            if m and section == "def_object_events":
                objs.append({"x": int(m.group(1)), "y": int(m.group(2)),
                             "sprite": m.group(3)})
        if cells:
            warp_cells[const] = cells
        if edges:
            warp_edges[const] = edges
        if objs:
            objects[const] = objs
    return warp_cells, warp_edges, objects


def warp_landing(warp_cells, dest, wid):
    """Destination (x,y) for arriving via dest map's warp number wid."""
    cells = warp_cells.get(dest)
    if not cells or wid > len(cells) or cells[wid - 1] is None:
        return None
    return list(cells[wid - 1])


OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west"}


def conn_band(const_dims, dest_dims, direction, offset):
    """Cells on the FROM map whose far edge walks into the connection:
    inclusive [[x1,y1],[x2,y2]] band, per the overlap implied by
    EnterMapConnection (new coord = old coord - 2*offset)."""
    Wf, Hf = const_dims["tiles_w"], const_dims["tiles_h"]
    Wd, Hd = dest_dims["tiles_w"], dest_dims["tiles_h"]
    if direction in ("north", "south"):
        x1, x2 = max(0, 2 * offset), min(Wf - 1, 2 * offset + Wd - 1)
        y = 0 if direction == "north" else Hf - 1
        return [[x1, y], [x2, y]]
    y1, y2 = max(0, 2 * offset), min(Hf - 1, 2 * offset + Hd - 1)
    x = 0 if direction == "west" else Wf - 1
    return [[x, y1], [x, y2]]


def conn_landing(dest_dims, direction, offset):
    """Where you appear on the destination map (EnterMapConnection:
    stripped coord + far edge)."""
    Wd, Hd = dest_dims["tiles_w"], dest_dims["tiles_h"]
    if direction == "north":
        return None, Hd - 1, 2 * offset  # (x varies, y fixed); handled below
    if direction == "south":
        return None, 0, 2 * offset
    if direction == "west":
        return Wd - 1, None, 2 * offset
    return 0, None, 2 * offset

OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west"}


def build_graph(repo):
    maps = parse_map_constants(repo)
    camel_of, conns = parse_attributes(repo)

    missing = sorted(set(maps) - set(camel_of))
    extra = sorted(set(camel_of) - set(maps))

    warp_cells, warp_edges, objects = parse_warps_and_objects(repo, camel_of)

    # Regions: connected components of each map's static walkable grid.
    # Multi-region maps (Sprout Tower floors) have warps that are NOT
    # mutually reachable on foot; edges carry from_regions/to_regions so
    # planners route on (map, region), not bare maps.
    md = MapData(repo)
    md.surf = True   # optimistic connectivity: water is passable terrain

    def landing_regions(const, x, y):
        """Regions steppable-into after WALKING onto (x,y): None without a
        grid, () when the landing cell is not enterable (walled -- a
        connection transition would bonk in-game). Warp landings teleport
        and never bonk; they use regions_at directly."""
        try:
            if not md._enterable(const, x, y):
                return []
            return list(md.regions_at(const, x, y))
        except (KeyError, FileNotFoundError, IndexError):
            return None

    def regions_at(const, x, y):
        try:
            return list(md.regions_at(const, x, y))
        except (KeyError, FileNotFoundError, IndexError):
            return None    # no decodable collision grid for this map

    def region_count(const):
        try:
            return md.region_map(const)[1]
        except (KeyError, FileNotFoundError, IndexError):
            return None

    nodes = {}
    for const, dims in sorted(maps.items()):
        node = dict(dims)
        node["camel"] = camel_of.get(const)
        node["file"] = f"maps/{camel_of[const]}.asm" if const in camel_of else None
        node["connections"] = sorted(conns.get(const, {}))
        node["npc_count"] = len(objects.get(const, []))
        node["region_count"] = region_count(const)
        nodes[const] = node

    edges = []

    # Warp edges: from_map trigger cell -> dest map's own warp cell.
    for src in sorted(warp_edges):
        for (x, y), (dest, wid) in sorted(warp_edges[src].items()):
            landing = warp_landing(warp_cells, dest, wid)
            if landing is None:
                edges.append({
                    "from_map": src, "to_map": dest, "kind": "warp",
                    "cells": [x, y], "warp_id": wid, "dest_cell": None,
                    "routable": False,
                    "notes": f"warp #{wid} has no matching warp_event on "
                             f"{dest} (or is a back-warp)",
                })
                continue
            edge = {
                "from_map": src, "to_map": dest, "kind": "warp",
                "cells": [x, y], "warp_id": wid, "dest_cell": landing,
                "routable": True,
                "notes": "step onto cell; door/cutscene warps only fire "
                         "with the direction held through the transition",
            }
            frm = regions_at(src, x, y)
            tos = regions_at(dest, landing[0], landing[1])
            if frm is not None:
                edge["from_regions"] = frm
            if tos is not None:
                edge["to_regions"] = tos
            edges.append(edge)

    # Connection edges (one directed edge per `connection` macro line).
    for src in sorted(conns):
        for direction, (dest, offset) in sorted(conns[src].items()):
            if dest not in maps:
                edges.append({
                    "from_map": src, "to_map": dest, "kind": "connection",
                    "direction": direction, "offset": offset,
                    "routable": False,
                    "notes": "destination constant not in map_constants.asm",
                })
                continue
            band = conn_band(maps[src], maps[dest], direction, offset)
            fx, fy, strip = conn_landing(maps[dest], direction, offset)
            # Landing cell: stripped coord runs over the band; emit the band
            # midpoint as representative entry plus the formula.
            c1, c2 = band
            mid = ((c1[0] + c2[0]) // 2, (c1[1] + c2[1]) // 2)
            sx = x_mid = mid[0] - 2 * offset if direction in ("north", "south") else fx
            sy = mid[1] - 2 * offset if direction in ("west", "east") else fy
            entry_cell = [sx, sy]
            frm, tos = set(), set()
            have_grids = (regions_at(src, *band[0]) is not None
                          and regions_at(dest, sx, sy) is not None)
            (x1, y1), (x2, y2) = band
            for bx in range(x1, x2 + 1):
                for by in range(y1, y2 + 1):
                    r = regions_at(src, bx, by) or ()
                    frm.update(r)
                    if not r:
                        continue   # can't stand here; landing irrelevant
                    if direction in ("north", "south"):
                        lx, ly = bx - 2 * offset, fy
                    else:
                        lx, ly = fx, by - 2 * offset
                    tos.update(landing_regions(dest, lx, ly) or ())
            edge = {
                "from_map": src, "to_map": dest, "kind": "connection",
                "direction": direction, "offset": offset,
                "cells": band,
                "entry": {
                    "heading": direction,
                    "landing_formula": (
                        "(x - 2*offset, %d)" % (maps[dest]["tiles_h"] - 1)
                        if direction == "north" else
                        "(x - 2*offset, 0)" if direction == "south" else
                        "(%d, y - 2*offset)" % (maps[dest]["tiles_w"] - 1)
                        if direction == "west" else
                        "(0, y - 2*offset)"),
                    "example_cell": entry_cell,
                    "example_source_cell": list(mid),
                },
                "routable": True,
                "notes": "walk off the map across the border band",
            }
            if have_grids:
                edge["from_regions"] = sorted(frm)
                edge["to_regions"] = sorted(tos)
            edges.append(edge)

    graph = {
        "_meta": {
            "repo": str(repo),
            "coordinate_space": "tile coords (2x block coords), origin top-left",
            "sources": [
                "constants/map_constants.asm",
                "data/maps/attributes.asm",
                "maps/*.asm (def_warp_events, def_object_events)",
                "macros/scripts/maps.asm (macro arg order)",
                "engine/overworld/warp_connection.asm (EnterMapConnection math)",
                "maps/*.blk + data/tilesets/*_collision.asm (region components"
                " via crystalagent.nav.MapData.region_map)",
            ],
            "unmatched_constants": {"only_in_map_constants": missing,
                                    "only_in_attributes": extra},
        },
        "nodes": nodes,
        "edges": edges,
    }
    return graph


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO,
                    help="pokecrystal disassembly root")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    graph = build_graph(args.repo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(graph, indent=1, sort_keys=False) + "\n")

    n_warps = sum(1 for e in graph["edges"] if e["kind"] == "warp")
    n_conns = sum(1 for e in graph["edges"] if e["kind"] == "connection")
    print(f"{args.out}: {len(graph['nodes'])} maps, "
          f"{n_warps} warp edges ({n_warps - sum(e['routable'] for e in graph['edges'] if e['kind'] == 'warp')} unroutable), "
          f"{n_conns} connection edges")
    meta = graph["_meta"]["unmatched_constants"]
    if any(meta.values()):
        print("WARNING unmatched constants:", json.dumps(meta), file=sys.stderr)


if __name__ == "__main__":
    main()
