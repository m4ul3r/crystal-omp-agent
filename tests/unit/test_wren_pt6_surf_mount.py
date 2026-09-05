"""Surf mount verification (session claude-wren pt6).

Live failure: crossing the New Bark -> Route 27 seam, `_step('R')`
reported 'blocked' while the avatar had plainly slid onto the water cell
(pos (18,7), terrain water). Trusting wPlayerState alone made the mount
look like a wall, so the crossing was hand-rolled with raw presses. The
mount now also accepts the position proof, and reports 'warp' when the
mount carried us across a map seam.
"""
from types import SimpleNamespace

import pytest

from crystalagent.driver import Driver

pytestmark = pytest.mark.unit

ASK = ["The water is calm.", "Want to SURF?  YES  NO"]


def mount_driver(after_pos, player_state, start=(0, 0, 5, 7), ask=True):
    """Fake whose settle() lands the avatar at `after_pos` (x, y) and
    leaves wPlayerState == `player_state`."""
    d = Driver.__new__(Driver)
    world = {"pos": start, "presses": []}
    d._world = world
    d.pos = lambda: world["pos"]
    d.step_dir = lambda mv, **kw: "blocked"      # turn-in-place at water
    d.press = lambda seq: world["presses"].append(seq)
    d.emu = SimpleNamespace(
        screen_text=lambda: (ASK if ask else ["", ""]),
        read_u8=lambda name: player_state)

    def settle(**kw):
        world["pos"] = after_pos
    d.settle = settle
    return d, world


def test_mount_reported_by_player_state():
    d, _ = mount_driver((0, 0, 6, 7), player_state=4)
    assert d._mount_surf("R") == "moved"


def test_mount_reported_by_position_when_state_byte_lags():
    """The New Bark seam case: state byte still 0, avatar on the water."""
    d, _ = mount_driver((0, 0, 6, 7), player_state=0)
    assert d._mount_surf("R") == "moved"


def test_seam_crossing_during_mount_reports_warp():
    d, _ = mount_driver((0, 1, 1, 7), player_state=4)   # map number changed
    assert d._mount_surf("R") == "warp"


def test_real_wall_still_blocked():
    """No prompt ever appears: a plain wall, not water."""
    d, world = mount_driver((0, 0, 5, 7), player_state=0, ask=False)
    assert d._mount_surf("R") == "blocked"
    assert len(world["presses"]) == 10        # bounded probe, then give up


def test_prompt_answered_but_nothing_moved_is_blocked():
    d, _ = mount_driver((0, 0, 5, 7), player_state=0)
    assert d._mount_surf("R") == "blocked"
