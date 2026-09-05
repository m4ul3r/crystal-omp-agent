"""A push that seals the last door must be refused.

Sokoban pushes cannot be undone. A floor whose only exit has just been blocked
can be reset only through a door it can no longer reach, which is terminal
without an Escape Rope -- Victory Road B1F stranded a run at (4,10) exactly
that way, after which all seven of its doors were unreachable and every later
attempt failed instantly.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import boulder_solver as bs  # noqa: E402


class _FakeDriver:
    """Only `warp_cells` is consulted, and that is stubbed out."""


@pytest.fixture(autouse=True)
def _stub_doors(monkeypatch):
    holder = {}

    def fake_warp_cells(d):
        return holder["doors"]

    monkeypatch.setattr(bs, "warp_cells", fake_warp_cells)
    return holder


def _corridor_walls(width, height, open_cells):
    return {(x, y) for x in range(width) for y in range(height)
            if (x, y) not in open_cells}


@pytest.mark.unit
def test_push_that_plugs_the_only_corridor_is_refused(_stub_doors):
    #  row 1 is a straight corridor: door at (1,1), boulder at (3,1),
    #  player at (2,1) pushing RIGHT would put the boulder at (4,1) -- fine.
    #  Pushing LEFT from (4,1) puts it on (2,1) and seals the door.
    open_cells = {(x, 1) for x in range(1, 6)}
    walls = _corridor_walls(7, 3, open_cells)
    _stub_doors["doors"] = frozenset({(1, 1)})

    # Pushing the boulder at (3,1) leftwards from (4,1) lands it on (2,1),
    # between the player and the only door.
    ok = bs._push_keeps_a_door(_FakeDriver(), "M", walls,
                               frozenset({(3, 1)}), frozenset(),
                               (4, 1), (-1, 0))
    assert ok is False


@pytest.mark.unit
def test_push_away_from_the_door_is_allowed(_stub_doors):
    open_cells = {(x, 1) for x in range(1, 6)}
    walls = _corridor_walls(7, 3, open_cells)
    _stub_doors["doors"] = frozenset({(1, 1)})

    # Player at (2,1) shoving the boulder at (3,1) further right: the door
    # behind the player stays reachable.
    ok = bs._push_keeps_a_door(_FakeDriver(), "M", walls,
                               frozenset({(3, 1)}), frozenset(),
                               (2, 1), (1, 0))
    assert ok is True


@pytest.mark.unit
def test_no_doors_at_all_never_blocks_a_push(_stub_doors):
    open_cells = {(x, 1) for x in range(1, 6)}
    walls = _corridor_walls(7, 3, open_cells)
    _stub_doors["doors"] = frozenset()
    assert bs._push_keeps_a_door(_FakeDriver(), "M", walls,
                                 frozenset({(3, 1)}), frozenset(),
                                 (4, 1), (-1, 0)) is True


@pytest.mark.unit
def test_push_that_severs_the_target_is_refused(_stub_doors):
    """A recoverable floor is not enough -- the ROUTE has to survive.

    Victory Road B1F shoved (4,7) west to (3,7): a door stayed reachable, so
    the old guard allowed it, and (30,25) promptly became unreachable. An
    83-move plan died on its first move.
    """
    #   door at (1,1); corridor row 1; target at (5,1); boulder at (3,1).
    #   Pushing it RIGHT from (2,1) lands it on (4,1), between us and (5,1),
    #   while the door behind stays perfectly reachable.
    open_cells = {(x, 1) for x in range(1, 6)}
    walls = _corridor_walls(7, 3, open_cells)
    _stub_doors["doors"] = frozenset({(1, 1)})

    without_target = bs._push_keeps_a_door(
        _FakeDriver(), "M", walls, frozenset({(3, 1)}), frozenset(),
        (2, 1), (1, 0))
    with_target = bs._push_keeps_a_door(
        _FakeDriver(), "M", walls, frozenset({(3, 1)}), frozenset(),
        (2, 1), (1, 0), target=(5, 1))

    assert without_target is True, "the door check alone sees no problem"
    assert with_target is False, "the target is now behind the boulder"
