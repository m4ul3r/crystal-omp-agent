"""The map is a DATA interface, not art to be counted.

`map_view()` renders a grid behind a 5-column row gutter and a two-row x
ruler, so answering "what is at x=15?" from it means counting characters
in a monospace row. A driving model got that wrong three times in one
session: it read Ilex Forest row 22 as walkable and bumped a wall twenty
times, put the Olivine pier warp at x=2 when it is x=3, and could only
find the Vermilion Port Passage city exit by grepping `warp_event` -- i.e.
the disassembly was a better map interface than the harness's renderer.

So `find_tiles`/`exits`/`tile_at`/`tiles_in` answer by coordinate, and
`map_view` carries an annotation block built from those same calls, so the
picture and the data cannot disagree.
"""
import re

import pytest

import trek
from crystalagent import paths
from crystalagent.nav import MapData
from trek import Driver

pytestmark = pytest.mark.unit

REPO = paths.REPO_ROOT

# collision bytes: 0x00 floor, 0x14 grass, 0x29 water, 0x71 door-warp, 0x01 wall
FLOOR, GRASS, WATER, WARP, WALL = 0x00, 0x14, 0x29, 0x71, 0x01


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]

    def read_u8(self, sym):
        return 0

    def tick(self, n=1):
        self.frame += n


class FakeNav:
    """Just the MapData surface the map interface reads."""

    surf = False
    blocked = {}

    def __init__(self, grid, warps=(), conns=()):
        self._grid = grid
        self.camel = {"TEST_MAP": "TestMap"}
        self.warps = {"TEST_MAP": dict(warps)}
        self.warp_cells = {"TEST_MAP": [c for c in dict(warps)]}
        self.conns = {"TEST_MAP": dict(conns)}

    def grid(self, name):
        if name != "TEST_MAP":
            raise KeyError(name)
        return self._grid

    def _enterable(self, name, x, y):
        """Same rule as MapData._enterable, on the fake grid."""
        from crystalagent.nav import WALKABLE, WARPS, HOPS, WATER
        grid = self.grid(name)
        if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
            return False
        c = grid[y][x]
        return (c in WALKABLE or c in WARPS or c in HOPS
                or (self.surf and c in WATER))

    # the component/changeblock surface map_view now reads: borrow the REAL
    # algorithms so the fake cannot drift from MapData's behaviour
    region_map = MapData.region_map
    regions_at = MapData.regions_at

    def conditional(self, name):
        return {}

    def _warp_landing(self, *a, **k):
        return None


def fake_driver(grid, warps=(), conns=(), pos=(2, 2), npcs=()):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.nav = FakeNav(grid, warps, conns)
    d.names = None
    d.map_name = lambda: "TEST_MAP"
    d._map_const = lambda: "TEST_MAP"
    d.pos = lambda: (0, 0, pos[0], pos[1])
    d.npc_cells = lambda: set(npcs)
    return d


GRID = [
    [WALL,  WALL,  WALL,  WALL,  WALL],
    [WALL,  FLOOR, FLOOR, GRASS, WARP],
    [WALL,  FLOOR, FLOOR, GRASS, WALL],
    [WARP,  FLOOR, WATER, WATER, WALL],
    [WALL,  WALL,  WALL,  WALL,  WALL],
]


# -- find_tiles: the call that removes the counting ---------------------

def test_find_tiles_returns_exactly_the_matching_cells_sorted():
    d = fake_driver(GRID)
    assert d.find_tiles("warp") == [(0, 3), (4, 1)]
    assert d.find_tiles("grass") == [(3, 1), (3, 2)]
    assert d.find_tiles("water") == [(2, 3), (3, 3)]
    assert d.find_tiles("floor") == [(1, 1), (1, 2), (1, 3),
                                     (2, 1), (2, 2)]


def test_find_tiles_knows_the_ledge_and_sidewall_families():
    """`_tile_kind` returns 'ledge-down'/'sidewall-ul', which a caller
    asking for "ledges" should not have to enumerate."""
    grid = [[0x00, 0xA0, 0xB4], [0x00, 0x00, 0x00]]
    d = fake_driver(grid, pos=(0, 0))
    assert d.tile_at(1, 0).startswith("ledge-")
    assert d.tile_at(2, 0).startswith("sidewall-")
    assert d.find_tiles("ledge") == [(1, 0)]
    assert d.find_tiles("sidewall") == [(2, 0)]


def test_find_tiles_npc_uses_the_live_sprites():
    d = fake_driver(GRID, npcs=[(2, 2), (1, 1)])
    assert d.find_tiles("npc") == [(1, 1), (2, 2)]


def test_an_unknown_kind_is_empty_not_an_error():
    assert fake_driver(GRID).find_tiles("banana") == []


# -- tile_at / tiles_in ------------------------------------------------

def test_tile_at_and_observe_use_the_same_classifier():
    """They must not be two different classifiers: `observe()['tiles']`
    and `tile_at` both go through `_tile_kind`, so the four neighbours of
    a position agree by construction."""
    d = fake_driver(GRID, pos=(2, 2))
    x, y = 2, 2
    expect = {"u": d.tile_at(x, y - 1), "d": d.tile_at(x, y + 1),
              "l": d.tile_at(x - 1, y), "r": d.tile_at(x + 1, y)}
    from crystalagent.nav import STEP
    for dd, (dx, dy) in STEP.items():
        assert trek._tile_kind(GRID[y + dy][x + dx]) == expect[dd.lower()]
    assert d.tile_at(x, y) == "floor"


def test_tile_at_is_off_map_outside_the_grid():
    d = fake_driver(GRID)
    assert d.tile_at(-1, 0) == "off-map"
    assert d.tile_at(0, 99) == "off-map"


def test_tiles_in_is_keyed_by_absolute_coordinates():
    d = fake_driver(GRID)
    rect = d.tiles_in(1, 1, 3, 2)
    assert set(rect) == {(1, 1), (2, 1), (3, 1), (1, 2), (2, 2), (3, 2)}
    assert rect[(3, 1)] == "grass"
    assert rect[(1, 1)] == "floor"
    # bounds may be given in either order and are clipped to the grid
    assert d.tiles_in(3, 2, 1, 1) == rect
    assert set(d.tiles_in(-5, -5, 0, 0)) == {(0, 0)}


# -- exits: what grep warp_event was for -------------------------------

def test_exits_joins_warps_and_edge_connections():
    d = fake_driver(GRID, warps={(0, 3): ("NEXT_MAP", 1),
                                 (4, 1): ("OTHER_MAP", 2)},
                    conns={"north": ("ABOVE_MAP", 0)})
    assert d.exits() == [
        {"kind": "warp", "x": 0, "y": 3, "to": "NEXT_MAP", "warp_id": 1},
        {"kind": "warp", "x": 4, "y": 1, "to": "OTHER_MAP", "warp_id": 2},
        {"kind": "connection", "dir": "north", "to": "ABOVE_MAP",
         "edge": "y=0"},
    ]


def test_exits_match_the_real_warp_events():
    """maps/VermilionPortPassage.asm:23-24 -- `warp_event 15, 0,
    VERMILION_CITY, 8` and `warp_event 16, 0, VERMILION_CITY, 9`. This is
    the exact pair a session could only recover by grepping the .asm."""
    d = Driver.__new__(Driver)
    d.nav = MapData(REPO)
    d.map_name = lambda: "VERMILION_PORT_PASSAGE"
    d._map_const = lambda: "VERMILION_PORT_PASSAGE"
    city = [(e["x"], e["y"]) for e in d.exits()
            if e["kind"] == "warp" and e["to"] == "VERMILION_CITY"]
    assert city == [(15, 0), (16, 0)]
    port = [(e["x"], e["y"]) for e in d.exits()
            if e["to"] == "VERMILION_PORT"]
    assert port == [(3, 14)]            # maps/VermilionPortPassage.asm:27


def test_olivine_port_pier_warp_is_where_the_asm_says():
    """The session computed the pier warp at x=2; maps/OlivinePort.asm:390
    says `warp_event 7, 23, FAST_SHIP_1F, 1`, and :389 the passage door at
    (11,7). Reading it off the data cannot miscount."""
    d = Driver.__new__(Driver)
    d.nav = MapData(REPO)
    d.map_name = lambda: "OLIVINE_PORT"
    d._map_const = lambda: "OLIVINE_PORT"
    assert [(e["x"], e["y"], e["to"]) for e in d.exits()] == [
        (7, 23, "FAST_SHIP_1F"), (11, 7, "OLIVINE_PORT_PASSAGE")]


# -- map_view's annotation block ---------------------------------------

def test_map_view_annotates_every_warp_it_draws():
    """The art and the data can never disagree: every warp cell inside the
    rendered window is named, with its destination, in the block below."""
    d = fake_driver(GRID, warps={(0, 3): ("NEXT_MAP", 1)}, pos=(1, 1),
                    npcs=[(2, 1)])
    art = d.map_view()
    window_warps = [c for c in d.find_tiles("warp")]
    assert "warps:" in art
    line = next(l for l in art.splitlines() if l.startswith("warps:"))
    for x, y in window_warps:
        assert f"({x},{y})" in line or "outside this view" in line
    assert "(0,3)->NEXT_MAP" in line
    assert "npcs:  (2,1)" in art
    assert "decide from find_tiles()" in art


def test_map_view_counts_warps_outside_the_window():
    """A cropped view must never silently drop a warp: it says how many
    are out of frame."""
    grid = [[FLOOR] * 3, [FLOOR, FLOOR, FLOOR], [WALL, WALL, WALL],
            [WARP, FLOOR, FLOOR]]
    d = fake_driver(grid, warps={(0, 3): ("FAR_MAP", 1)}, pos=(0, 0))
    art = d.map_view()
    line = next(l for l in art.splitlines() if l.startswith("warps:"))
    assert "outside" in line


def test_map_view_names_edge_connections_and_water():
    d = fake_driver(GRID, conns={"south": ("BELOW_MAP", 0)}, pos=(1, 1))
    art = d.map_view()
    assert "edge:  south y=4 -> BELOW_MAP" in art
    assert "grass: rows 1-2, x 3-3 (2 cells)" in art
    # water is not in the walk-reachable window, so it is not claimed
    assert "water:" not in art
    d.nav.surf = True
    assert "water: rows 3-3, x 2-3 (2 cells)" in d.map_view()


def _grid_rows(art):
    """Just the cell rows of a map_view, gutter removed: the header and two
    ruler rows come first, legend/annotations after. The gutter is the
    fixed `f"{y:4d} "` width the renderer writes."""
    return [l[5:] for l in art.splitlines()[3:]
            if re.match(r"^\s{0,3}\d+ ", l)]


def test_map_view_shows_a_wing_it_cannot_reach_and_names_its_entrance():
    """The regression that cost a session: Rocket base B3F's western half
    is walkable, holds the rival/boss triggers, and hangs off two ladders
    only another floor can reach. The render drew it as blank -- identical
    to wall -- so the map read as "nothing there". Walkable cells of an
    unreachable component must draw as `,`, their warps as `o`, and the
    annotation must say how to get in.

    The wing is walled off INSIDE the reachable bounding box, which is the
    case the glyphs answer; a wing outside the crop is answered by the
    `offregion:` line alone."""
    F, W, O = FLOOR, WALL, WARP
    grid = [[F, F, F, F, F, F, F],
            [F, W, W, W, W, W, F],
            [F, W, O, F, F, W, F],
            [F, W, F, F, F, W, F],
            [F, W, F, F, F, W, F],
            [F, W, W, W, W, W, F],
            [F, F, F, F, F, F, F]]
    d = fake_driver(grid, warps={(2, 2): ("OTHER_FLOOR", 1)}, pos=(0, 0))
    art = d.map_view()
    rows = _grid_rows(art)
    assert any("," in r for r in rows), "unreachable floor drew as void"
    assert any("o" in r for r in rows), "its entrance drew as void"
    line = next(l for l in art.splitlines() if l.startswith("offregion:"))
    assert "(2,2)->OTHER_FLOOR" in line
    assert "NOT reachable from here" in line
    assert "8 walkable cells at x 2-4, y 2-4" in line
    # the reachable ring is untouched: still plain floor glyphs
    assert rows[0].startswith("@......")


def test_map_view_keeps_quiet_about_decorative_islands():
    """A one-cell pocket with no way in is noise, not architecture: it
    draws, so the map is never a lie, but it earns no annotation line."""
    F, W = FLOOR, WALL
    grid = [[F, F, F, F, F],
            [F, W, W, W, F],
            [F, W, F, W, F],
            [F, W, W, W, F],
            [F, F, F, F, F]]
    d = fake_driver(grid, pos=(0, 0))
    art = d.map_view()
    assert "offregion:" not in art
    assert any("," in r for r in _grid_rows(art))
