"""The stall watchdog must tell the two freezes apart.

Both look identical from the log -- last line is from before the loop began --
but they need opposite responses: a PINNED run is recoverable by abandoning
the map, a WEDGED one is not ticking the core at all and only a stack helps.
Getting them the wrong way round would either abandon healthy maps or sit on
a dead process, which is the failure this module exists to end.
"""

import json

import pytest

from pokeagent.watchdog import STALL_PINNED, STALL_WEDGED, StallWatch


def _feed(tmp_path, name="default", **fields):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(fields))
    return p


@pytest.mark.unit
def test_movement_is_not_a_stall(tmp_path):
    _feed(tmp_path, map="VictoryRoad_B1F", pos={"x": 9, "y": 9}, frame=100)
    w = StallWatch(feed_dir=tmp_path, idle_s=0.0)
    w._run_once = None
    # First observation only seeds state; nothing is known yet.
    assert w._read() == (("VictoryRoad_B1F", 9, 9), 100)
    assert not w.stalled


@pytest.mark.unit
def test_frames_advancing_without_movement_is_pinned(tmp_path):
    """The exact Victory Road failure: (9,9) facing a wall, frames climbing."""
    w = StallWatch(feed_dir=tmp_path, idle_s=-1.0)
    w._where = ("VictoryRoad_B1F", 9, 9)
    w._frame = 138164696
    _feed(tmp_path, map="VictoryRoad_B1F", pos={"x": 9, "y": 9},
          frame=138183657)
    obs = w._read()
    where, frame = obs
    assert where == w._where and frame != w._frame
    w._flag(STALL_PINNED, where, 999.0, frame)
    assert w.kind == STALL_PINNED
    assert w.stalled
    assert "VictoryRoad_B1F (9,9)" in w.detail


@pytest.mark.unit
def test_frames_frozen_too_is_wedged(tmp_path):
    w = StallWatch(feed_dir=tmp_path, idle_s=-1.0)
    w._flag(STALL_WEDGED, ("Route118", 1, 2), 500.0, 42)
    assert w.kind == STALL_WEDGED
    assert "wedged" in w.detail


@pytest.mark.unit
def test_clear_lets_the_next_map_start_fresh(tmp_path):
    w = StallWatch(feed_dir=tmp_path, idle_s=-1.0)
    w._flag(STALL_PINNED, ("Route118", 1, 2), 500.0, 42)
    assert w.stalled
    w.clear()
    assert not w.stalled and w.detail == ""


@pytest.mark.unit
def test_a_missing_or_torn_feed_is_never_a_stall(tmp_path):
    """A feed that has not been written yet must not read as frozen."""
    w = StallWatch(feed_dir=tmp_path, idle_s=-1.0)
    assert w._read() is None
    (tmp_path / "default.json").write_text("{not json")
    assert w._read() is None
    (tmp_path / "default.json").write_text(json.dumps({"frame": 1}))
    assert w._read() is None  # no map name -> nothing to compare
    assert not w.stalled
