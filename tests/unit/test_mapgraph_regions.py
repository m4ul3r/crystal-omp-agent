"""Region-aware mapgraph: multi-region maps (Sprout Tower floors) must
route via warps between regions, never through walls (session claude-wren:
`travel` targeted the 2F->3F stairs at (10,14) which are walled off from
the 2F east arrival area; the real route detours over the 1F walkway)."""
import json
from pathlib import Path

import pytest

from crystalagent.nav import MapData

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = ROOT / "data" / "mapgraph.json"
POKECRYSTAL = ROOT.parent
needs_disasm = pytest.mark.skipif(
    not (POKECRYSTAL / "constants" / "map_constants.asm").exists(),
    reason="pokecrystal disassembly not present")

FLOOR, WALL, WARP_COLL = 0x00, 0x07, 0x70


def mknav(grids, warps=None):
    md = MapData.__new__(MapData)
    md._grid_cache = {k: [list(r) for r in g] for k, g in grids.items()}
    md._coll_cache = {}
    md._cell_overrides = {}
    md.warps = warps or {}
    md.warp_cells = {}
    md.conns = {}
    md.consts = {}
    md.surf = False
    return md


# ---------- region_map / regions_at on synthetic grids ----------

def test_walls_split_regions():
    nav = mknav({"M": [[FLOOR, WALL, FLOOR]]})
    ids, n = nav.region_map("M")
    assert n == 2
    assert ids[0][0] != ids[0][2] and ids[0][1] == -1


def test_warp_event_cell_splits_but_bare_warp_collision_does_not():
    # a live warp tile cannot be walked THROUGH; a warp-collision tile
    # without an event (gate carpet) is plain floor
    grids = {"M": [[FLOOR, WARP_COLL, FLOOR]],
             "N": [[FLOOR, WARP_COLL, FLOOR]]}
    nav = mknav(grids, warps={"M": {(1, 0): ("X", 1)}})
    assert nav.region_map("M")[1] == 2
    assert nav.region_map("N")[1] == 1


def test_regions_at_warp_tile_yields_adjacent_components():
    nav = mknav({"M": [[FLOOR, WARP_COLL, FLOOR]]},
                warps={"M": {(1, 0): ("X", 1)}})
    ids, _ = nav.region_map("M")
    assert nav.regions_at("M", 1, 0) == (ids[0][0], ids[0][2])
    assert nav.regions_at("M", 0, 0) == (ids[0][0],)


def test_plan_route_respects_regions():
    # A = two rooms split by a wall; the only way from room 0 to room 1
    # is a round trip through B (the Sprout Tower shape, minimized)
    grids = {"A": [[FLOOR, WALL, FLOOR]], "B": [[FLOOR, FLOOR, FLOOR]]}
    nav = mknav(grids)
    edges = [
        {"from_map": "A", "to_map": "B", "kind": "warp", "cells": [0, 0],
         "dest_cell": [0, 0], "routable": True,
         "from_regions": [0], "to_regions": [0]},
        {"from_map": "B", "to_map": "A", "kind": "warp", "cells": [2, 0],
         "dest_cell": [2, 0], "routable": True,
         "from_regions": [0], "to_regions": [1]},
    ]
    legs = nav.plan_route(edges, "A", (0, 0), "A", (2, 0))
    assert [(e["from_map"], tuple(e["cells"])) for e in legs] == \
        [("A", (0, 0)), ("B", (2, 0))]
    # already in the goal region: no legs
    assert nav.plan_route(edges, "A", (2, 0), "A", (2, 0)) == []


# ---------- the shipped mapgraph.json ----------

@pytest.fixture(scope="module")
def graph():
    return json.loads(GRAPH_PATH.read_text())


@pytest.fixture(scope="module")
def nav():
    return MapData(POKECRYSTAL)


def _legs(legs):
    return [(e["from_map"], tuple(e["cells"]), e["to_map"]) for e in legs]


@needs_disasm
def test_sprout_tower_ascent_uses_walkway_chain(graph, nav):
    # 1F entrance -> 3F: up the east stairs, across via 1F's outer
    # walkway, down the west corridor to the real 3F stairs
    legs = nav.plan_route(graph["edges"],
                          "SPROUT_TOWER_1F", (10, 14),
                          "SPROUT_TOWER_3F", (10, 13))
    assert _legs(legs) == [
        ("SPROUT_TOWER_1F", (6, 4), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (17, 3), "SPROUT_TOWER_1F"),
        ("SPROUT_TOWER_1F", (2, 6), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (10, 14), "SPROUT_TOWER_3F"),
    ]


@needs_disasm
def test_sprout_tower_descent_reverses_the_chain(graph, nav):
    legs = nav.plan_route(graph["edges"],
                          "SPROUT_TOWER_3F", (10, 13),
                          "SPROUT_TOWER_1F", (9, 14))
    assert _legs(legs) == [
        ("SPROUT_TOWER_3F", (10, 14), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (2, 6), "SPROUT_TOWER_1F"),
        ("SPROUT_TOWER_1F", (17, 3), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (6, 4), "SPROUT_TOWER_1F"),
    ]


@needs_disasm
def test_sprout_2f_east_arrival_never_plans_direct_stairs(graph, nav):
    # the live claude-wren failure: from the 2F east arrival area the
    # (10,14) stairs are unreachable; the plan must detour over 1F
    legs = nav.plan_route(graph["edges"],
                          "SPROUT_TOWER_2F", (6, 2),
                          "SPROUT_TOWER_3F", (10, 13))
    assert _legs(legs) == [
        ("SPROUT_TOWER_2F", (17, 3), "SPROUT_TOWER_1F"),
        ("SPROUT_TOWER_1F", (2, 6), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (10, 14), "SPROUT_TOWER_3F"),
    ]


@needs_disasm
def test_sprout_edges_carry_disjoint_regions(graph):
    e = {(x["from_map"], tuple(x["cells"])): x
         for x in graph["edges"] if x["kind"] == "warp"}
    arrive_east = e[("SPROUT_TOWER_1F", (6, 4))]["to_regions"]
    stairs_up = e[("SPROUT_TOWER_2F", (10, 14))]["from_regions"]
    walkway_exit = e[("SPROUT_TOWER_2F", (17, 3))]["from_regions"]
    # east arrival area cannot reach the 3F stairs, but can reach (17,3)
    assert not set(arrive_east) & set(stairs_up)
    assert set(arrive_east) == set(walkway_exit)


@needs_disasm
@pytest.mark.parametrize("src,start,dst", [
    ("NEW_BARK_TOWN", (10, 9), "ELMS_LAB"),
    ("CHERRYGROVE_CITY", (20, 9), "ROUTE_30"),
    ("VIOLET_CITY", (20, 18), "VIOLET_GYM"),
])
def test_previously_working_routes_still_resolve(graph, nav, src, start, dst):
    legs = nav.plan_route(graph["edges"], src, start, dst)
    assert legs, f"no plan {src} -> {dst}"
    assert legs[-1]["to_map"] == dst
