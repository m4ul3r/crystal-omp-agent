"""Who walks in front, and why it is not the training decision.

Gen 3 gives the lead a full share of every encounter it participates in, so
whoever stands in slot 0 pulls away from the rest for free. Rotation is the
counterweight, and it has to be its own decision.

Conflating it with training cost a run a runaway: rotation was gated behind the
"laggards" list, laggards are measured against the party median, and once the
party was roughly level-matched that list was empty. Nothing rotated. NINJA
went L29 -> L42 in fifty minutes while the other five sat at 27.

Training asks "should we go and grind for someone" -- usually no. Rotation asks
"who should walk in front" -- almost always whoever is behind.
"""

import pytest

from pokeagent.team import Team

pytestmark = pytest.mark.unit


class M:
    def __init__(self, index, level, hp_frac=1.0, alive=True, egg=False):
        self.index, self.level = index, level
        self.hp_frac, self.alive, self.is_egg = hp_frac, alive, egg
        self.label = f"M{index}"


def team_with(members):
    team = object.__new__(Team)
    team._fighters = lambda party: list(party)
    return team, members


def test_the_lowest_level_healthy_mon_is_chosen():
    team, party = team_with([M(0, 42), M(1, 27), M(2, 35)])
    assert team.furthest_behind(party).index == 1


def test_a_fainted_mon_is_not_sent_out_to_be_trained():
    """It would faint again on the first hit and the exp would go nowhere."""
    team, party = team_with([M(0, 42), M(1, 20, hp_frac=0.0, alive=False), M(2, 27)])
    assert team.furthest_behind(party).index == 2


def test_ties_are_broken_by_party_order_so_the_choice_is_stable():
    """A rotation that flips between two equal mons re-opens the party menu
    every cycle and trains neither."""
    team, party = team_with([M(0, 30), M(1, 27), M(2, 27)])
    assert team.furthest_behind(party).index == 1


def test_an_empty_bench_asks_for_nobody():
    team, party = team_with([])
    assert team.furthest_behind(party) is None
    team, party = team_with([M(0, 20, hp_frac=0.0, alive=False)])
    assert team.furthest_behind(party) is None


def test_rotation_and_training_disagree_and_that_is_the_point():
    """The exact party that exposed this. No laggards against the median, so
    training correctly declines -- and rotation still has work to do."""
    levels = [42, 27, 35, 27, 27, 28]
    team, party = team_with([M(i, lv) for i, lv in enumerate(levels)])
    assert team.needs_training(party) == [], "median says the team is fine"
    assert team.furthest_behind(party).level == 27, \
        "and someone still needs to be in front"
