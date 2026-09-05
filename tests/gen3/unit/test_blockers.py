"""Answer "why can't I get there" in seconds instead of an hour.

Every gate this project hit cost 30-60 minutes of hand diagnosis, and four of
the five had the same shape: an OBJECT standing on the only approach to a door,
with the pathfinder telling the truth and being disbelieved.

* Slateport museum -- three Team Aqua grunts on both door approaches.
* Mauville gym -- Wally and his uncle on the only approach.
* Route 111 -- two breakable rocks in the only corridor.
* Route 117 -- a seam landing in a one-cell pocket.

These reconstruct those exact geometries and assert the diagnostic names them.
"""

import pytest

from pokeagent.blockers import Blockers, Blocker

pytestmark = pytest.mark.unit


class Cell:
    def __init__(self, passable=True):
        self.passable = passable


class FakeNav:
    def __init__(self, walkable, objects):
        self.walkable, self.objects = walkable, objects

    def cell(self, map_name, x, y):
        return Cell((x, y) in self.walkable)

    def info(self, map_name):
        return type("I", (), {"objects": self.objects})()


def make(walkable, objects, live=None, field_moves=None, here="Map"):
    d = type("D", (), {})()
    d.nav = FakeNav(set(walkable), objects)
    # `to_warp` also asks the chokepoint question, which needs a pathfinder and
    # an elevation. These fakes are about DOOR approaches, so the route is
    # always open and the chokepoint half finds nothing.
    d.nav.blocked = {}
    d.nav.find_path = lambda *a, **k: ["D"]
    d.elevation = lambda: 3
    d._mark_npcs = lambda m: None
    d.map_name = lambda: here
    d.pos = lambda: (0, 0)
    d.live_npcs = lambda: [dict(o, player=False)
                           for o in (live if live is not None else objects)]
    d.field_moves = lambda: field_moves or {}
    return Blockers(d)


def test_the_museum_shape_three_grunts_on_both_door_approaches():
    """Doors at (30,26)/(31,26) walled on three sides; grunts on (30,27),
    (31,27). 1171 failed warp attempts before anyone rendered it."""
    walkable = {(29, 27), (30, 27), (31, 27), (32, 27)}
    grunts = [{"x": 30, "y": 27, "script": "EvilTeamGrunt2",
               "flag": "FLAG_HIDE_EVIL_TEAM_SLATEPORT"}]
    b = make(walkable, grunts)
    found = b.to_warp((30, 26))
    assert len(found) == 1
    assert found[0].kind == "object"
    assert "EvilTeamGrunt2" in found[0].detail
    assert found[0].clears == "setflag FLAG_HIDE_EVIL_TEAM_SLATEPORT"


def test_the_gym_shape_names_the_flag_that_moves_wally():
    walkable = {(8, 6)}
    wally = [{"x": 8, "y": 6, "script": "MauvilleCity_EventScript_Wally",
              "flag": "FLAG_HIDE_WALLY_MAUVILLE"}]
    b = make(walkable, wally)
    found = b.to_warp((8, 5))
    assert found and found[0].clears == "setflag FLAG_HIDE_WALLY_MAUVILLE"


def test_a_breakable_rock_is_named_with_the_hm_that_clears_it():
    rocks = [{"x": 18, "y": 101, "script": "S_BreakableRock", "flag": "0"}]
    b = make({(18, 101)}, rocks)
    found = b.obstacles_on("Map")
    assert [f.clears for f in found] == ["ROCK SMASH"]
    assert found[0].kind == "obstacle"


def test_a_cleared_rock_stops_being_reported():
    """The map file still lists it; the live object list does not. Believing
    the file would keep smashing a rock that is already gone."""
    rocks = [{"x": 18, "y": 101, "script": "S_BreakableRock"}]
    b = make({(18, 101)}, rocks, live=[])
    assert b.obstacles_on("Map") == []


def test_a_door_with_a_free_approach_has_no_blockers():
    """The expensive failure mode is the false positive: reporting a blocker
    on a door that opens fine sends the loop off solving nothing."""
    walkable = {(30, 27), (31, 27)}
    b = make(walkable, [{"x": 30, "y": 27, "script": "SomeNPC"}])
    assert b.to_warp((31, 26)) == []


def test_a_door_walled_on_every_side_says_so():
    b = make(set(), [])
    found = b.to_warp((5, 5))
    assert found and found[0].kind == "no-seam"
    assert "every neighbour is wall" in found[0].detail


def test_the_explanation_says_whether_anyone_knows_the_hm():
    """The Route 111 case: knowing it is ROCK SMASH is only half the answer
    when `field_moves()` has been all-None for three badges."""
    rocks = [{"x": 18, "y": 101, "script": "S_BreakableRock"}]
    b = make({(18, 101), (18, 102)}, rocks, field_moves={"ROCK SMASH": None})
    text = b.explain(warp=(18, 100))
    assert "ROCK SMASH" in text and "NOBODY KNOWS IT" in text

    b2 = make({(18, 101), (18, 102)}, rocks,
              field_moves={"ROCK SMASH": "MIGHTYENA"})
    assert "known by MIGHTYENA" in b2.explain(warp=(18, 100))


def test_finding_nothing_is_stated_not_implied():
    """An empty list and 'no blockers found' mean very different things at
    three in the morning."""
    b = make({(1, 1)}, [])
    assert "no blockers found" in b.explain(warp=(1, 0))


class PathNav(FakeNav):
    """A nav whose pathfinder honours a blocked set, so chokepoints matter."""

    def __init__(self, walkable, objects, corridor):
        super().__init__(walkable, objects)
        self.blocked = {}
        self.corridor = set(corridor)

    def find_path(self, map_name, start, goal, elevation=None):
        blocked = self.blocked.get(map_name, set())
        # The only route runs through `corridor`; anything blocking a corridor
        # cell severs it.
        return None if self.corridor & blocked else ["D"]


def choke(objects, corridor, marked):
    d = type("D", (), {})()
    d.nav = PathNav({(0, 0)}, objects, corridor)
    d.map_name = lambda: "Route112"
    d.pos = lambda: (0, 0)
    d.elevation = lambda: 3
    d.live_npcs = lambda: [dict(o, player=False) for o in objects]
    d.field_moves = lambda: {}
    d._mark_npcs = lambda m: d.nav.blocked.__setitem__(m, set(marked))
    return Blockers(d)


def test_a_chokepoint_object_is_named_even_though_it_is_not_on_a_door():
    """The false negative that cost 615 retries: two Team Magma grunts stood
    in a CORRIDOR on the way to the cable car, not on its door, so the
    door-approach check found nothing and said so."""
    grunts = [
        {"x": 26, "y": 30, "script": "Route112_EventScript_150513",
         "flag": "FLAG_HIDE_GRUNTS_BLOCKING_CABLE_CAR"},
        {"x": 27, "y": 30, "script": "Route112_EventScript_15051C",
         "flag": "FLAG_HIDE_GRUNTS_BLOCKING_CABLE_CAR"},
    ]
    b = choke(grunts, corridor={(26, 30)}, marked={(26, 30), (27, 30)})
    found = b.chokepoints((28, 28))
    assert [f.cell for f in found] == [(26, 30)], \
        "only the object actually severing the route should be named"
    assert found[0].clears == "setflag FLAG_HIDE_GRUNTS_BLOCKING_CABLE_CAR"


def test_objects_that_are_not_in_the_way_are_not_blamed():
    """Naming every nearby object is as useless as naming none."""
    objects = [{"x": 5, "y": 5, "script": "Bystander", "flag": "0"}]
    b = choke(objects, corridor={(9, 9)}, marked={(5, 5)})
    assert b.chokepoints((28, 28)) == []


def test_no_route_even_without_objects_is_not_an_object_problem():
    """If the map has no path at all, blaming an object sends the loop off to
    clear something that will not help."""
    b = choke([], corridor=set(), marked=set())
    b.d.nav.find_path = lambda *a, **k: None
    assert b.chokepoints((28, 28)) == []
