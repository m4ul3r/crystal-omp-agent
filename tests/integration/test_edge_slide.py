"""Integration: `_slide_edge` / map-edge connection crossing.

Historical bug: `travel` failed the whole leg at map-edge connections
whose planned crossing row was off by one -- Azalea Town's east edge
crosses at the real connection row y=14 while the plan said y=13, and
Route 32 -> Violet at x=8. A unit test with a fake nav cannot reproduce
an off-by-one EDGE BAND against the real ROM geometry; only driving the
emulator across the seam can. The contract asserted here is the one that
mattered live: the MAP ACTUALLY CHANGES.

Forked savestate: claude_saves/wren-well-cleared.state (Kurt's house,
Azalea Town -- one door from the edge under test).
"""

import pytest

pytestmark = pytest.mark.integration


def _travel_tolerating_landing_drift(d, dest):
    """travel() may still raise TravelError after a successful crossing
    when the held-key glide past an edge seam exceeds its modeled
    tolerance (gotcha 14). The edge-crossing contract under test is that
    the map changes; landing-cell drift accounting is a separate knob."""
    from trek import TravelError
    try:
        d.travel(dest)
    except TravelError:
        pass


def test_travel_crosses_azalea_east_edge(fork_driver):
    """Azalea Town -> Route 33 across the east edge (x=39): the crossing
    whose planned row was off by one and used to fail the whole leg."""
    d = fork_driver("wren-well-cleared")
    assert d.take_warp(3, 7), f"door refused: {d.last_warp_reason}"
    assert d.map_name() == "AZALEA_TOWN"
    _travel_tolerating_landing_drift(d, "ROUTE_33")
    assert d.map_name() == "ROUTE_33", \
        f"east-edge crossing failed: on {d.map_name()} {d.pos()[2:]}"


def test_travel_crosses_back_west_edge(fork_driver):
    """Route 33 -> Azalea Town back across the west edge: both bands of
    the same connection must fire, not just the outbound one."""
    d = fork_driver("wren-well-cleared")
    assert d.take_warp(3, 7), d.last_warp_reason
    _travel_tolerating_landing_drift(d, "ROUTE_33")
    assert d.map_name() == "ROUTE_33"
    _travel_tolerating_landing_drift(d, "AZALEA_TOWN")
    assert d.map_name() == "AZALEA_TOWN", \
        f"west-edge crossing failed: on {d.map_name()} {d.pos()[2:]}"
