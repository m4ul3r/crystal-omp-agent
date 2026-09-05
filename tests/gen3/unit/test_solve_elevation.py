"""`solve` must carry the player's LEVEL, not just their position.

Victory Road 1F's y=25 row is an elevation-15 bridge flanked by elevation-4
cells, with elevation-3 ground above it. Crossing from the 4 side you keep
level 4, and 4 -> 3 is illegal -- the engine refused (7,24), (8,24) and (9,24)
every single time. A static wall filter cannot express that (it broke B1F,
whose real route changes level through a wildcard tile); carrying `z` can,
because it knows which level you are on when you arrive.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import boulder_solver as bs  # noqa: E402

EMPTY = frozenset()


def _grid(rows):
    """rows[y][x] = elevation; -1 marks a wall."""
    walls, elev = set(), {}
    for y, row in enumerate(rows):
        for x, e in enumerate(row):
            if e < 0:
                walls.add((x, y))
            else:
                elev[(x, y)] = e
    return frozenset(walls), elev


#: The real barrier: e3 ground (y=0), the e15 bridge (y=1) with e4 flanks,
#: and e4 ground below (y=2).
BARRIER = [
    [3, 3, 3],
    [4, 15, 4],
    [4, 4, 4],
]


@pytest.mark.unit
def test_level_blind_search_walks_an_illegal_seam():
    """Without `elev` the solver plans the route the engine refuses."""
    walls, _elev = _grid(BARRIER)
    path = bs.solve(walls, EMPTY, EMPTY, (1, 2), [(1, 0)])
    assert path is not None, "the level-blind model sees a clear path"


@pytest.mark.unit
def test_carrying_level_4_cannot_reach_the_level_3_row():
    walls, elev = _grid(BARRIER)
    path = bs.solve(walls, EMPTY, EMPTY, (1, 2), [(1, 0)],
                    elev=elev, start_z=4)
    assert path is None, "4 -> 3 across a bridge is illegal"


@pytest.mark.unit
def test_a_wildcard_tile_lets_you_change_level():
    """Level 0 is how the game changes level at all -- B1F's real route to
    (30,25) depends on exactly this, which is why a static filter broke it."""
    rows = [
        [3, 3, 3],
        [0, 0, 0],   # a wildcard corridor
        [4, 4, 4],
    ]
    walls, elev = _grid(rows)
    path = bs.solve(walls, EMPTY, EMPTY, (1, 2), [(1, 0)],
                    elev=elev, start_z=4)
    assert path is not None, "stepping onto level 0 must free the level"


@pytest.mark.unit
def test_same_level_is_always_walkable():
    rows = [[3, 3, 3], [3, 3, 3]]
    walls, elev = _grid(rows)
    assert bs.solve(walls, EMPTY, EMPTY, (0, 1), [(2, 0)],
                    elev=elev, start_z=3) is not None


@pytest.mark.unit
def test_bridge_preserves_the_level_it_was_entered_with():
    """Enter the bridge from 3, leave onto 3: legal. The flanking 4s are not."""
    rows = [
        [3, 15, 3],
        [-1, 4, -1],
    ]
    walls, elev = _grid(rows)
    assert bs.solve(walls, EMPTY, EMPTY, (0, 0), [(2, 0)],
                    elev=elev, start_z=3) is not None
    assert bs.solve(walls, EMPTY, EMPTY, (0, 0), [(1, 1)],
                    elev=elev, start_z=3) is None
