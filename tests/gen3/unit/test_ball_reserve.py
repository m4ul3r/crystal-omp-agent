"""The ball reserve must never refuse a species the Pokedex has not caught.

The reserve was checked BEFORE the dex override, so holding exactly
BALL_RESERVE balls made `balls <= RESERVE` true and declined EVERY catch --
dex-new included. The run sat at 3 NET BALLs and 38/114 caught, visiting maps
with five new species each and refusing all of them, while the reserve it was
protecting had nothing left to protect for.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pokeagent import catching  # noqa: E402


class _Catcher:
    """Only the branches `plan` touches before it decides."""

    def __init__(self, balls, caught):
        self._balls = balls
        self._caught = set(caught)
        self.last_reason = None

    # --- the surface `plan` uses --------------------------------------
    def storage_has_room(self):
        return True

    def balls_available(self):
        return self._balls

    def dex_caught(self, species):
        return species in self._caught

    def already_own(self, species):
        return False

    def _no(self, why):
        self.last_reason = why
        return catching.CatchPlan(False, why, None, 0.0)

    def _enemy_species_id(self):
        return 0          # forces a decline for the non-dex-new path

    def _party_rows(self):
        return []

    plan = catching.Catcher.plan


def _frame(species):
    return {"wild": True,
            "enemy": {"species": species, "level": 27, "hp": 40, "max_hp": 40}}


@pytest.mark.unit
def test_reserve_does_not_refuse_a_new_species():
    """Exactly RESERVE balls in the bag, and the species is new."""
    c = _Catcher(balls=catching.BALL_RESERVE, caught={"ODDISH"})
    plan = c.plan(_frame("CORPHISH"))
    assert plan.wanted is True, f"refused a dex-new catch: {plan.reason}"
    assert "new to the Pokedex" in plan.reason


@pytest.mark.unit
def test_no_balls_still_refuses():
    c = _Catcher(balls=0, caught=set())
    plan = c.plan(_frame("CORPHISH"))
    assert plan.wanted is False
    assert "no balls" in plan.reason


@pytest.mark.unit
def test_reserve_still_applies_to_an_already_caught_species():
    """The reserve is for choosing between catches, and that still works."""
    c = _Catcher(balls=catching.BALL_RESERVE, caught={"CORPHISH"})
    plan = c.plan(_frame("CORPHISH"))
    assert plan.wanted is False
    assert "reserve" in plan.reason


@pytest.mark.unit
def test_one_ball_is_enough_for_a_new_species():
    c = _Catcher(balls=1, caught=set())
    plan = c.plan(_frame("SURSKIT"))
    assert plan.wanted is True
