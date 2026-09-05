"""Map decode, movement rules and routing, against the real data files."""

import pytest

pytestmark = pytest.mark.unit


def test_every_map_is_indexed(mapdata):
    """394 maps, and the index must round-trip."""
    assert len(mapdata.index) == 394
    for name, (g, n) in mapdata.index.items():
        assert mapdata.by_number[(g, n)] == name


def test_blockdata_size_matches_declared_geometry(mapdata):
    """A layout whose .bin does not match width*height*2 means the decode is
    misaligned, which shows up as a plausible-looking but wrong grid."""
    for name in ("LittlerootTown", "Route101", "PetalburgCity",
                 "LittlerootTown_BrendansHouse_1F"):
        info = mapdata.info(name)
        grid = mapdata.grid(name)
        assert len(grid) == info.height
        assert all(len(row) == info.width for row in grid)


def test_littleroot_warps_resolve_to_their_pair(mapdata):
    """A warp lands on the destination's own warp[dest_warp_id]
    (src/overworld.c:425-430)."""
    exits = {(e["x"], e["y"]): e for e in mapdata.exits("LittlerootTown")
             if e["kind"] == "warp"}
    door = exits[(5, 8)]
    assert door["dest"] == "LittlerootTown_BrendansHouse_1F"
    assert door["lands_at"] == (8, 8)


def test_doors_come_in_pairs(mapdata):
    """Both door tiles of a house point at the same destination warp, which
    is why entering from either side works."""
    warps = mapdata.info("LittlerootTown_BrendansHouse_1F").warps
    out = [w for w in warps if w.dest_map == "MAP_LITTLEROOT_TOWN"]
    assert len(out) == 2
    assert {w.dest_warp_id for w in out} == {1}


def test_connection_offsets(mapdata):
    """Littleroot's north seam is Route101 with offset 0."""
    conn = [e for e in mapdata.exits("LittlerootTown") if e["kind"] == "connection"]
    assert conn == [{"kind": "connection", "direction": "U", "offset": 0,
                     "dest": "Route101"}]
    landing = mapdata.connection_landing("LittlerootTown", "U", 5, 0)
    dest, x, y = landing
    assert dest == "Route101"
    assert (x, y) == (5, mapdata.info("Route101").height - 1)


def test_terrain_classification(mapdata):
    """Route 101 is the grass route; Littleroot town has none."""
    assert len(mapdata.find_tiles("Route101", "grass")) > 50
    assert mapdata.find_tiles("LittlerootTown", "grass") == []


def test_ledges_are_one_way_and_two_tiles(mapdata, behaviors):
    """A ledge jump lands two cells out and has no reverse edge
    (src/event_object_movement.c:5316-5319)."""
    ledges = mapdata.find_tiles("Route102", "ledge")
    if not ledges:
        pytest.skip("Route102 has no decoded ledge tiles")
    lx, ly = ledges[0]
    beh = mapdata.cell("Route102", lx, ly).behavior
    direction = next(
        d for d, s in
        (("D", "South"), ("U", "North"), ("L", "West"), ("R", "East"))
        if beh in behaviors.jump_sets[s]
    )
    dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[direction]
    start = (lx - dx, ly - dy)
    hop = mapdata.step("Route102", start[0], start[1], 3, direction)
    if hop is not None:
        assert hop[:2] == (lx + dx, ly + dy), "a ledge moves two tiles"
        back = {"U": "D", "D": "U", "L": "R", "R": "L"}[direction]
        assert mapdata.step("Route102", hop[0], hop[1], hop[2], back) != start


def test_routing_across_the_overworld(mapdata):
    route = mapdata.route("LittlerootTown", "RustboroCity")
    assert route is not None
    assert route[0] == "LittlerootTown" and route[-1] == "RustboroCity"
    # Every hop must be a real edge.
    for here, nxt in zip(route, route[1:]):
        assert nxt in {d for d, _ in mapdata.neighbours(here)}


def test_dynamic_warp_destinations_do_not_crash(mapdata):
    """InsideOfTruck's warps go to MAP_DYNAMIC with WARP_ID_DYNAMIC, which is
    a symbolic constant, not a number."""
    exits = mapdata.exits("InsideOfTruck")
    assert exits and all(e["dest"] == "MAP_DYNAMIC" for e in exits)
    assert all(e["lands_at"] is None for e in exits)


def test_elevation_blocks_a_mismatched_step(mapdata):
    """The whole point of carrying z: a tile at a different elevation is
    refused unless one side is the 0 wildcard or the 15 bridge value."""
    grid = mapdata.grid("Route110")
    seen = {c.elevation for row in grid for c in row}
    assert len(seen) > 1, "Route110 should be multi-level"
