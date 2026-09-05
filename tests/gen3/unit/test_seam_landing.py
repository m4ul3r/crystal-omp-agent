"""Cross a map seam where there is room to stand on the other side.

A border is not one door, it is forty. Two cells a few tiles apart land in
different places, and some of those places are pockets: walkable cells with no
walkable neighbours.

Verdanturf's east border and Route 117's west border are both walkable at y=7,
so the nearest crossing to a player standing thereabouts landed on Route 117
(0,7) -- a ONE-CELL pocket. The player arrived unable to move in any direction,
raw d-pad included, with no scene and no dialog to blame. Two rows further down
lands on 698 cells of open road. Measured, not guessed:

    (19,5) -> room 1     (19,8)  -> room 698
    (19,6) -> room 2     (19,9)  -> room 696
    (19,7) -> room 1     (19,16) -> room 4

The rule: rank crossings by how much walkable map they land on. A pocket scores
1 and loses to anything.
"""

import pytest

from pokeagent import nav as navmod

pytestmark = pytest.mark.unit


def nav_with(rooms):
    """A nav whose seam landings are supplied by the test.

    `rooms` maps a border cell (19, y) to the number of walkable cells its
    landing sits in. Crossing Verdanturf's east edge at (19, y) lands on
    Route 117 at (0, y), which is the real geometry -- the connection offset
    is 0 -- so the fake keeps that relationship rather than inventing one.
    """
    nav = navmod.MapData.__new__(navmod.MapData)

    def exit_landing(map_name, edge):
        cell = edge["cross_at"]
        return ("Route117", 0, cell[1]) if cell in rooms else None

    def reachable(dest_map, cell, elevation=None):
        return range(rooms.get((19, cell[1]), 0))

    nav.exit_landing = exit_landing
    nav.reachable = reachable
    return nav


EDGE = {"kind": "connection", "direction": "R", "dest": "Route117"}


def test_landing_room_measures_the_far_side():
    nav = nav_with({(19, 7): 1, (19, 8): 698})
    assert nav._landing_room("VerdanturfTown", EDGE, (19, 8)) == 698
    assert nav._landing_room("VerdanturfTown", EDGE, (19, 7)) == 1


def test_a_pocket_loses_to_open_road():
    """The exact failure: the pocket at y=7 must not be chosen over y=8."""
    rooms = {(19, 5): 1, (19, 6): 2, (19, 7): 1, (19, 8): 698, (19, 9): 696}
    nav = nav_with(rooms)
    chosen = nav._best_crossing("VerdanturfTown", EDGE, sorted(rooms))
    assert chosen == (19, 8)


def test_crossings_are_offered_best_landing_first():
    """`route_legs` is breadth-first over LEGS, so every crossing of one border
    ties on cost and the first offered wins. Border order puts the pocket
    first."""
    rooms = {(19, 5): 1, (19, 7): 1, (19, 8): 698, (19, 9): 696}
    nav = nav_with(rooms)
    edge = dict(EDGE, cross_candidates=sorted(rooms))
    order = [v["cross_at"] for v in nav._crossings("VerdanturfTown", edge)]
    assert order[0] == (19, 8), f"pocket offered before the road: {order}"
    assert order.index((19, 8)) < order.index((19, 7))


def test_an_unreadable_landing_scores_zero_rather_than_crashing():
    """A map that will not decode must not end a journey; it just loses."""
    def boom(*a, **k):
        raise RuntimeError("no layout")

    nav = nav_with({(19, 8): 5})
    nav.reachable = boom
    assert nav._landing_room("VerdanturfTown", EDGE, (19, 8)) == 0


def test_a_crossing_with_no_known_landing_scores_zero():
    nav = nav_with({})
    nav.exit_landing = lambda *a, **k: None
    assert nav._landing_room("VerdanturfTown", EDGE, (19, 8)) == 0


def test_clearing_the_way_cannot_recurse_into_itself():
    """`goto` calls `clear_the_way` when something blocks a route, and
    clearing a rock means walking to it -- which calls `goto`. The two called
    each other until Python gave up, and every journey to Rusturf Tunnel died
    with "maximum recursion depth exceeded" instead of a reason.
    """
    from pokeagent.trek import Driver

    d = object.__new__(Driver)
    d._clearing = True
    assert Driver.clear_the_way(d, (0, 0)) is False, \
        "a re-entrant call must decline immediately"


def test_the_guard_is_released_even_when_smashing_raises():
    """A guard that leaks stays set for the rest of the session and silently
    disables rock clearing everywhere."""
    from pokeagent.trek import Driver

    d = object.__new__(Driver)
    d._clearing = False
    d.field_obstacles = lambda: [(1, 1, "ROCK SMASH")]
    d.map_name = lambda: "M"
    d.pos = lambda: (0, 0)
    d.elevation = lambda: 3
    d._mark_npcs = lambda m: None
    d.nav = type("N", (), {"find_path": staticmethod(lambda *a, **k: None)})()

    def boom(x, y):
        raise RuntimeError("no")

    d.smash_rock = boom
    try:
        Driver.clear_the_way(d, (9, 9))
    except RuntimeError:
        pass
    assert d._clearing is False, "the re-entrancy guard leaked"
