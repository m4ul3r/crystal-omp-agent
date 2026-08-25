"""Region-aware trek.route()/travel(): the planner must never take a warp
sitting on a walled-off part of the current map. The live claude-wren bug:
standing in Sprout Tower 2F's east arrival area, travel planned the (10,14)
stairs directly -- unreachable behind permanent walls; the real route detours
over the 1F outer walkway."""
from pathlib import Path

import pytest

from trek import Driver, TrekNav

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
POKECRYSTAL = ROOT.parent
needs_disasm = pytest.mark.skipif(
    not (POKECRYSTAL / "constants" / "map_constants.asm").exists(),
    reason="pokecrystal disassembly not present")


@pytest.fixture(scope="module")
def nav():
    return TrekNav(POKECRYSTAL)


def route_driver(nav, map_name, cell):
    """Plan-only Driver: real TrekNav + real data/mapgraph.json, fake pos."""
    d = Driver.__new__(Driver)
    d.nav = nav
    d.map_name = lambda: map_name
    d.pos = lambda: (0, 0) + tuple(cell)
    d._resolve_map = lambda name: name
    d._refresh_nav_blocks = lambda: None
    return d


def warp_legs(steps):
    return [(s["from"], tuple(s["cell"]), s["to"])
            for s in steps if s["kind"] == "warp"]


# -- route() region gating ----------------------------------------------------

@needs_disasm
def test_sprout_2f_east_arrival_detours_over_walkway(nav):
    # standing at (6,2) -- the 2F east arrival area -- the (10,14) stairs
    # are walled off; the plan must round-trip over the 1F walkway
    d = route_driver(nav, "SPROUT_TOWER_2F", (6, 2))
    steps = d.route("SPROUT_TOWER_3F")
    assert warp_legs(steps) == [
        ("SPROUT_TOWER_2F", (17, 3), "SPROUT_TOWER_1F"),
        ("SPROUT_TOWER_1F", (2, 6), "SPROUT_TOWER_2F"),
        ("SPROUT_TOWER_2F", (10, 14), "SPROUT_TOWER_3F"),
    ]
    # and never a direct first hop onto either stair cell
    first = warp_legs(steps)[0]
    assert first[1] not in ((10, 14), (11, 14))


@needs_disasm
def test_sprout_2f_west_side_takes_direct_stairs(nav):
    # from the west corridor (region of the stairs) the direct leg is right
    d = route_driver(nav, "SPROUT_TOWER_2F", (5, 14))
    assert warp_legs(d.route("SPROUT_TOWER_3F")) == [
        ("SPROUT_TOWER_2F", (10, 14), "SPROUT_TOWER_3F"),
    ]


@needs_disasm
def test_simple_route_violet_city_to_gym_unchanged(nav):
    # regression: single-region maps plan exactly as before -- one walk
    # approach plus one warp leg, straight through the gym door
    d = route_driver(nav, "VIOLET_CITY", (23, 6))
    steps = d.route("VIOLET_GYM")
    assert warp_legs(steps) == [("VIOLET_CITY", (18, 17), "VIOLET_GYM")]
    assert steps[0]["kind"] == "walk" and steps[0]["map"] == "VIOLET_CITY"


@needs_disasm
def test_multi_map_connection_route_still_resolves(nav):
    # regression: connection edges (multi-region from/to lists) still chain
    d = route_driver(nav, "NEW_BARK_TOWN", (6, 8))
    steps = d.route("VIOLET_CITY")
    assert [s["to"] for s in steps if s["kind"] != "walk"] == [
        "ROUTE_29", "CHERRYGROVE_CITY", "ROUTE_30", "ROUTE_31",
        "ROUTE_31_VIOLET_GATE", "VIOLET_CITY"]


# -- travel() seam replan ------------------------------------------------------

def warp_step(frm, to, cell, dest, ax, ay, d="R"):
    return {"kind": "warp", "from": frm, "to": to, "dir": d,
            "cell": list(cell), "warp_id": 1, "dest": list(dest),
            "notes": None, "approaches": [{"x": ax, "y": ay, "dir": d}]}


def test_travel_replans_remainder_when_glide_crosses_region_seam():
    """A tolerated (drift <= 3) landing on the far side of a region seam
    must splice in a fresh route() from the live cell -- the stale
    remainder walks the wrong side of a wall."""
    d = Driver.__new__(Driver)
    world = {"map": "M1", "cell": (2, 0)}
    d.map_name = lambda: world["map"]
    d.pos = lambda: (0, 0) + world["cell"]
    d._resolve_map = lambda name: name
    d._refresh_nav_blocks = lambda: None
    d.settle = lambda **kw: None
    d.textbox = lambda: False
    # M2 has two regions: modeled landing (5,5) is region 0, the actual
    # glide puts us at (5,7) in region 1
    regions = {("M2", (5, 5)): (0,), ("M2", (5, 7)): (1,),
               ("M2", (1, 7)): (1,), ("M3", (0, 0)): (0,)}
    d._regions = lambda m, x, y: regions.get((m, (x, y)), (-1,))
    plan_a = [{"kind": "walk", "map": "M1", "x": 2, "y": 0, "why": "w"},
              warp_step("M1", "M2", (3, 0), (5, 5), 2, 0),
              {"kind": "walk", "map": "M2", "x": 8, "y": 5, "why": "w"},
              warp_step("M2", "M3", (9, 5), (0, 0), 8, 5)]
    plan_b = [{"kind": "walk", "map": "M2", "x": 1, "y": 7, "why": "w"},
              warp_step("M2", "M3", (0, 7), (0, 0), 1, 7, d="L")]
    routes = []

    def route(dest, max_cost=None):
        routes.append(world["cell"])
        return plan_a if len(routes) == 1 else plan_b

    d.route = route
    gotos = []

    def goto(x, y, label=""):
        gotos.append((x, y))
        world["cell"] = (x, y)
        return True

    d.goto = goto

    def _step(mv):
        if world["map"] == "M1":
            world["map"], world["cell"] = "M2", (5, 7)   # glided off-region
        else:
            world["map"], world["cell"] = "M3", (0, 0)
        return "warp"

    d._step = _step
    out = d.travel("M3")
    assert routes == [(2, 0), (5, 7)]        # replanned from the live cell
    assert gotos == [(2, 0), (1, 7)]         # stale (8,5) leg never walked
    assert world["map"] == "M3"
    assert out == plan_a[:2] + plan_b
