"""The Mach Bike, which is the only door into the Safari Zone's north-west.

Measured on the decoded grid: NW has ZERO aligned crossings with either
neighbour on foot. Southwest's northward corridor (x=7..10) is severed at
y=3,2 by two `MB_MUDDY_SLOPE` tiles, and the x=19..23 corridor is walled at
y=2 -- that pocket is entered FROM the north, so it is an exit. 7 species
(DODUO, DODRIO, GOLDUCK, PINSIR, PSYDUCK, RHYHORN, SEAKING) sat behind it.

`ForcedMovement_MuddySlope` (field_player_avatar.c:494-504) slides you back
and zeroes the acceleration unless `movementDirection == DIR_NORTH` and
`GetPlayerSpeed() > 3`.
"""
import pytest

from pokeagent.trek import Driver


@pytest.mark.unit
def test_only_the_fastest_mach_speed_clears_the_slope_threshold():
    """`sMachBikeSpeeds` is {1,2,4} (bike.c:121) and the slope wants > 3.

    So the bike must be at counter 2 -- nothing slower gets up. If this table
    is ever "simplified" to {1,2,3} the climb silently stops working, because
    3 > 3 is false.
    """
    assert Driver.MACH_BIKE_SPEEDS == (1, 2, 4)
    assert Driver.MACH_BIKE_SPEEDS[2] > 3
    assert Driver.MACH_BIKE_SPEEDS[1] <= 3, \
        "SPEED_FAST must NOT clear the slope, or the run-up is pointless"
    assert Driver.MACH_BIKE_SPEEDS[0] <= 3


@pytest.mark.unit
def test_the_mach_bike_flag_is_bit_one():
    """`PLAYER_AVATAR_FLAG_MACH_BIKE (1 << 1)` -- bit 0 is ON_FOOT."""
    assert Driver.MACH_BIKE_FLAG == 2


class _Slope:
    """The parts of `climb_slope` that do not need an emulator."""

    def __init__(self, moves, maps):
        self._moves, self._maps = list(moves), list(maps)
        self.last_bike_reason = None

    def pos(self):
        return self._moves[0] if len(self._moves) == 1 else self._moves.pop(0)

    def map_name(self):
        return self._maps[0] if len(self._maps) == 1 else self._maps.pop(0)


def _verdict(before, after, here, now):
    """`climb_slope`'s judgement, transcribed."""
    if now != here:
        return True, None
    if after == before:
        return False, "did-not-move"
    if after[1] >= before[1]:
        return False, f"slid back: {before} -> {after}"
    return True, None


@pytest.mark.unit
def test_riding_off_the_top_of_a_map_is_success_not_a_slide():
    """The bug this pins: crossing a seam northward RESETS the coordinates.

    The first working climb went `Southwest (8,8)` -> `Northwest (8,27)`, and
    y went UP because the new map's rows start at its own bottom. Judging it
    by y alone reported "slid back" about a success and refused to bank the
    state that had just proved the whole mechanic.
    """
    ok, why = _verdict((8, 8), (8, 27),
                       "SafariZone_Southwest", "SafariZone_Northwest")
    assert ok is True and why is None


@pytest.mark.unit
def test_a_real_slide_inside_one_map_is_still_a_failure():
    """Same map, pushed south -- the slope refused and must say so."""
    ok, why = _verdict((8, 4), (8, 9),
                       "SafariZone_Southwest", "SafariZone_Southwest")
    assert ok is False
    assert "slid back" in why


@pytest.mark.unit
def test_not_moving_at_all_is_reported_separately():
    """Distinguishable from a slide: a wall, or the bike never mounted."""
    ok, why = _verdict((8, 8), (8, 8),
                       "SafariZone_Southwest", "SafariZone_Southwest")
    assert ok is False
    assert why == "did-not-move"


@pytest.mark.unit
def test_climbing_within_one_map_is_success():
    ok, why = _verdict((8, 8), (8, 1),
                       "SafariZone_Southwest", "SafariZone_Southwest")
    assert ok is True and why is None


@pytest.mark.unit
def test_downhill_is_refused_before_anything_is_pressed():
    """A slope is climbed NORTH only; the other three directions slide.

    Refusing early matters because a half-driven bag or a mounted bike left
    behind eats movement input afterwards (gotcha 7).
    """
    d = Driver.__new__(Driver)
    d.last_bike_reason = None
    for bad in ("D", "L", "R"):
        assert Driver.climb_slope(d, bad) is False
        assert d.last_bike_reason == "not-uphill"
