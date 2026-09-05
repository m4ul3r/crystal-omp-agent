"""Route 119's northern half, which its own bridge used to sever.

The most expensive class of bug this project has: a map model faithful to the
.blk data and still wrong about the engine. It looks like evidence, so it gets
believed -- on the strength of a reading like this one the run concluded SURF
was required for Meteor Falls (it was not) and later spent 8125 attempts
walking at Route 119.

Two rules from src/event_object_movement.c are pinned here:

* elevation belongs in the BFS state. Elevation-15 bridge cells accept any
  level and preserve it, so keying the closed set on (x, y) lets whichever
  wave arrives first shut the bridge against every other level. On Route 119
  the z=3 wave coming down the river closed (21..23, 84..85) against the z=4
  road and cut the map in half at y=82.
* a surfer takes the tile's elevation like anyone else
  (ObjectEventUpdateZCoord:7586-7598). Pinning them to their previous level
  cost the elevation-0 river its wildcard.
"""

import pytest

pytestmark = pytest.mark.integration


def test_the_weather_institute_is_reachable_from_the_south(optional_fork):
    d = optional_fork("live-badge5-surf")
    nav = d.nav
    d._surf_sync()
    assert nav.surfing, "this checkpoint holds SURF; the driver should see it"

    # Crossing Route 118's north seam lands here.
    reach = nav.reachable("Route119", (17, 139))

    # The whole map, not the southern third. 1234 was the severed number.
    assert len(reach) > 2000, (
        f"only {len(reach)} cells reachable from Route 119's south landing -- "
        "the bridge has severed the map again"
    )
    ys = {y for _, y in reach}
    assert min(ys) < 40, (
        f"the fill stops at y={min(ys)}; the Weather Institute is at y=32"
    )

    door = (6, 32)
    assert door in reach or any(
        (door[0] + dx, door[1] + dy) in reach
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
    ), "the Weather Institute door is unreachable on foot from the south"


def test_routing_north_does_not_circumnavigate_hoenn(optional_fork):
    """A 20-leg sea route to the map directly north is a severed map talking.

    When the fill was cut, route_legs still found *a* way -- Mauville,
    Slateport and eleven sea routes around to Fortree. Plans like that are the
    symptom worth failing on: the router was right and the map was wrong.
    """
    d = optional_fork("live-badge5-surf")
    d._surf_sync()
    legs = d.nav.route_legs(
        "Route118", d.pos(), "Route119_WeatherInstitute_2F", max_hops=80)
    assert legs, "no route to the Weather Institute at all"
    hops = [leg["to_map"] for leg in legs]
    assert len(hops) <= 5, f"absurd route north: {' -> '.join(hops)}"
    assert "SlateportCity" not in hops, (
        f"routing north via Slateport means the map is severed: "
        f"{' -> '.join(hops)}"
    )
