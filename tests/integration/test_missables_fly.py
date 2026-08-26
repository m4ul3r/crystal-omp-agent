"""Integration: `missables()` both directions -- the FLY regression.

Historical bug: HM02 FLY sat with Chuck's wife in Cianwood from the
Storm Badge onward and NOTHING ever said so -- a whole playthrough
reached Champion with every trip on foot. `missables()` (and the
`missing:` fragment on status()) is the guard; this pins it in BOTH
directions against real event-flag state, because "flag clear" and
"flag set" are different code paths through the live WRAM read.

Forked savestates:
- claude_saves/wren-champion.state -- 8 badges, EVENT_GOT_HM02_FLY CLEAR.
- claude_saves/wren-kanto.state    -- post-Fly, in Kanto, flag SET.
"""

import pytest

pytestmark = pytest.mark.integration


def test_fly_missing_row_present_when_flag_clear(fork_driver):
    """Flag clear: the FLY row must appear, fully citable, and status()
    must carry the fragment that would have saved the playthrough."""
    d = fork_driver("wren-champion")
    rows = [r for r in d.missables() if r["item"] == "HM_FLY"]
    assert rows, "FLY is out there but missables() went blind"
    row = rows[0]
    assert row["have"] is False
    assert row["map"] == "CIANWOOD_CITY"
    assert (row["x"], row["y"]) == (10, 46)
    assert row["event"] == "EVENT_GOT_HM02_FLY"
    assert row["source"] == "maps/CianwoodCity.asm:100"
    assert "FLY(CIANWOOD_CITY 10,46)" in d.status()


def test_fly_absent_and_usable_when_flag_set(fork_driver):
    """Flag set: no FLY row (nothing left to miss) AND the party can
    actually use it -- 'HM obtained' vs 'field move usable' are
    different facts (d.field_moves())."""
    d = fork_driver("wren-kanto")
    assert not any(r["item"] == "HM_FLY" for r in d.missables())
    moves = d.field_moves()
    assert moves["FLY"] == "REED", \
        f"FLY known by nobody: {moves}"
