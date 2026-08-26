"""Integration: `take_warp` on a warp you are standing ON.

Historical bug (AGENTS.md gotcha 15): a warp fires on the step that
ENTERS its tile; arriving on one never re-triggers it. Every door
arrival leaves you standing on a live warp, and legs that then called
the warp primitive burned real turns at the Ilex/Azalea gate, the Union
Cave north mouth (17,3), the Olivine pier, and three ship cabin doors.
`take_warp` must therefore (a) enter from a distance, (b) step OFF and
back ON when called while already standing on the tile, and (c) REFUSE
stale coordinates from another map loudly instead of wandering.

Forked savestate: claude_saves/wren-well-cleared.state (Kurt's house,
Azalea Town -- a door pair with known coordinates in both directions).
"""

import pytest

pytestmark = pytest.mark.integration


def test_take_warp_from_two_cells_away_enters(fork_driver):
    """Called from inside Kurt's house, 4 cells from the (3,7) exit:
    take_warp approaches and ENTERS -- the map changes."""
    d = fork_driver("wren-well-cleared")
    assert d.pos()[2:] == (3, 3)
    assert d.take_warp(3, 7), f"refused: {d.last_warp_reason}"
    assert d.map_name() == "AZALEA_TOWN"


def test_stale_coordinates_refuse_without_wandering(fork_driver):
    """Coordinates belong to a MAP: stale gym coords once routed the walk
    somewhere unrelated (POKE_SEERS_HOUSE). A cell that is not a warp of
    the CURRENT map must be refused with a distinct reason, leaving the
    position and map untouched."""
    d = fork_driver("wren-well-cleared")
    assert d.take_warp(3, 7), d.last_warp_reason      # out to Azalea
    assert d.map_name() == "AZALEA_TOWN"
    before = d.pos()
    ok = d.take_warp(19, 27)   # VERMILION_CITY coords, meaningless here
    assert ok is False
    assert d.last_warp_reason, "stale coords must fail LOUDLY"
    assert "is not a warp" in d.last_warp_reason
    assert d.pos() == before, "take_warp wandered after refusing"
    assert d.map_name() == "AZALEA_TOWN"


def test_called_while_standing_on_the_tile_reenters(fork_driver):
    """Standing on a warp is not entering it (gotcha 15): a door arrival
    lands you ON the exit warp; calling take_warp from the tile itself
    must step OFF, back ON held, and still change the map -- not report
    'you are already there'.

    Was xfail(strict) on the day this lane was written: it caught a live
    `_reenter_warp` bug that 606 unit tests never saw. Sides are tried in
    STEP order (R, L, U, D), and on a south-wall door the horizontal
    step-off cannot fire the warp; the derailed position then made the
    loop `continue` past the axial sides, so U/D were never attempted and
    the player stranded at (0,7). FIXED in trek._reenter_warp by walking
    back to the target with `goto` when the single-step `_axis_move`
    cannot recover, which makes the docstring's promise -- every walkable
    side -- actually true. Keep this test un-xfailed: it is the regression
    guard for that loop."""
    d = fork_driver("wren-well-cleared")
    assert d.take_warp(3, 7), d.last_warp_reason      # out to Azalea
    assert d.goto(9, 8), d.last_goto_reason           # 2 cells from door
    # entering Kurt's house LANDS on its exit warp (3,7): gotcha 15
    assert d.take_warp(9, 5), f"entering house failed: {d.last_warp_reason}"
    assert d.map_name() == "KURTS_HOUSE"
    assert d.pos()[2:] == (3, 7)
    start_map = d.map_name()
    assert d.take_warp(3, 7), f"re-enter refused: {d.last_warp_reason}"
    assert d.map_name() != start_map, \
        f"standing-on-tile call did not fire the warp: {d.map_name()}"
    assert d.map_name() == "AZALEA_TOWN"
