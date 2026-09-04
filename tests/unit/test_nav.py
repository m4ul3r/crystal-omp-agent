"""Routing on synthetic collision grids: BFS, ledges, ice, warps, walls."""
import pytest

from crystalagent.nav import (
    COLL_PIT, ICE, MapData, TrekNav, WALKABLE, WARPS,
)

pytestmark = pytest.mark.unit

FLOOR = 0x00
GRASS = 0x14
WALL = 0x07
ICE_C = next(iter(ICE))            # 0x23
LEDGE_R = 0xA0                     # HOPS["R"]
WARP = next(iter(WARPS))           # 0x70
UP_WALL = 0xB2                     # side wall refusing DOWN entry (trek)


def mknav(grids, warps=None, warp_cells=None, conns=None, cls=MapData):
    md = cls.__new__(cls)
    md._grid_cache = {k: [list(r) for r in g] for k, g in grids.items()}
    md._coll_cache = {}
    md._cell_overrides = {}
    md.warps = warps or {}
    md.warp_cells = warp_cells or {}
    md.conns = conns or {}
    md.consts = {}                 # only needed by _conn_landing
    md.surf = False
    if cls.__name__ == "TrekNav":
        md.blocked = {}
    return md


def trek(grids, blocked=None, **kw):
    md = mknav(grids, cls=TrekNav, **kw)
    md.blocked = blocked or {}
    return md

def test_map_name_resolver_accepts_all_public_spellings():
    nav = MapData.__new__(MapData)
    nav.consts = {"NEW_BARK_TOWN": (1, 1, 10, 9)}
    nav.camel = {"NEW_BARK_TOWN": "NewBarkTown"}
    nav.const_by_camel = {"NewBarkTown": "NEW_BARK_TOWN"}
    nav._const_by_normalized = {"newbarktown": "NEW_BARK_TOWN"}
    for name in (
        "NEW_BARK_TOWN", "NewBarkTown", "new bark town", "new_bark_town",
        "NEWBARKTOWN",
    ):
        assert nav.resolve(name) == "NEW_BARK_TOWN"
    assert nav.resolve("NO_SUCH_MAP") is None


def test_straight_corridor_optimal():
    nav = mknav({"M": [[FLOOR] * 5]})
    assert nav.find_path("M", (0, 0), (4, 0)) == ["R"] * 4


def test_warp_blocks_midpath_without_cross():
    nav = mknav(
        {"M": [[FLOOR, WARP, FLOOR]], "M2": [[FLOOR, FLOOR]]},
        warps={"M": {(1, 0): ("M2", 1)}, "M2": {(0, 0): ("M", 1)}},
        warp_cells={"M": [(1, 0)], "M2": [(0, 0)]},
    )
    # cannot route THROUGH the warp without cross=True...
    assert nav.find_path("M", (0, 0), (2, 0)) is None
    # ...but cross=True fires it and lands on the destination's own
    # cell, from which plain walking continues
    assert nav.find_route("M", (0, 0), "M2", (1, 0)) == ["R", "R"]


def test_warp_goal_without_landing_is_not_routable_across():
    # dangling warp id (destination has no such warp cell): no landing
    nav = mknav(
        {"M": [[FLOOR, WARP]], "M2": []},
        warps={"M": {(1, 0): ("M2", 3)}},
        warp_cells={"M2": [(0, 0)]},
    )
    assert nav.find_route("M", (0, 0), "M2", (0, 0)) is None


def test_ledge_hops_forward_only_and_two_cells():
    nav = mknav({"M": [[FLOOR] * 4,
                       [FLOOR, LEDGE_R, WALL, FLOOR]]})
    # standing ON the ledge tile, R jumps over the cliff to x+2
    assert nav.find_path("M", (1, 1), (3, 1)) == ["R"]
    # returning west cannot hop; must go around over the top row
    back = nav.find_path("M", (3, 1), (1, 1))
    assert back == ["U", "L", "L", "D"]


def test_slide_stops_on_first_non_ice():
    nav = mknav({"M": [[ICE_C, ICE_C, FLOOR, FLOOR]]})
    assert nav.slide("M", 0, 0, "R") == (2, 0)


def test_slide_into_wall_stays_on_last_ice():
    nav = mknav({"M": [[ICE_C, ICE_C, WALL]]})
    assert nav.slide("M", 0, 0, "R") == (1, 0)


def test_water_gated_on_surf_flag():
    nav = mknav({"M": [[FLOOR, 0x29, FLOOR]]})   # COLL_WATER set value
    assert nav.find_path("M", (0, 0), (2, 0)) is None
    nav.surf = True
    assert nav.find_path("M", (0, 0), (2, 0)) == ["R", "R"]


def test_unwalkable_goal_reachable_if_adjacent():
    # contract: the goal cell itself is exempt from collision checks --
    # stepping INTO an unwalkable tile as the final step is allowed.
    nav = mknav({"M": [[FLOOR, FLOOR, FLOOR],
                       [FLOOR, WALL, WALL]]})
    assert nav.find_path("M", (0, 0), (2, 1)) == ["R", "R", "D"]
    # but a goal walled off from every approach stays unreachable
    sealed = mknav({"M": [[FLOOR, WALL],
                          [WALL, WALL]]})
    assert sealed.find_path("M", (0, 0), (1, 1)) is None


def test_avoid_reroutes_start_map_cells():
    nav = mknav({"M": [[FLOOR] * 3,
                       [FLOOR, FLOOR, FLOOR]]})
    straight = nav.find_path("M", (0, 0), (2, 0))
    assert straight == ["R", "R"]
    dodged = nav.find_path("M", (0, 0), (2, 0), avoid=[(1, 0)])
    assert dodged == ["D", "R", "R", "U"]


def test_treknav_blocked_cells_stop_even_goals():
    nav = trek({"M": [[FLOOR, FLOOR, FLOOR]]},
               blocked={"M": {(1, 0)}})
    assert nav.find_path("M", (0, 0), (2, 0)) is None
    # base nav would happily plan across the same sealed cell
    base = mknav({"M": [[FLOOR, FLOOR, FLOOR]]})
    assert base.find_path("M", (0, 0), (2, 0)) == ["R", "R"]


def test_treknav_side_wall_directionality():
    # $b2 (COLL_UP_WALL) refuses DOWNWARD entry, and -- per
    # home/map.asm .MovementPermissionsData, index $b2 & 7 = 2 -> LEFT_MASK,
    # which masks FACE_UP -- also refuses moving UP off the tile. So a $b2
    # cell is a one-way dead end from the south: enterable going up,
    # not crossable. Verified live on VICTORY_ROAD (5,58) and
    # ICE_PATH_1F (28,10).
    nav = trek({"M": [[FLOOR],
                      [UP_WALL],
                      [FLOOR]]})
    assert nav.find_path("M", (0, 0), (0, 2)) is None   # down through $b2
    assert nav.find_path("M", (0, 2), (0, 0)) is None   # up off the $b2
    assert nav.find_path("M", (0, 2), (0, 1)) == ["U"]  # onto it, though


def test_set_cell_override_roundtrip():
    nav = mknav({"M": [[FLOOR, WALL]]})
    nav.set_cell("M", 1, 0, FLOOR)
    assert nav.grid("M")[0][1] == FLOOR
    assert (0, 1) not in [(x, y) for _, x, y in []]     # sanity: no crash
    nav.clear_overrides()
    assert nav.grid("M")[0][1] == WALL
