"""Deciding what to catch, and when to throw.

A run that never catches is not building a team: 604 battles in, the party was
one Pokemon with nine uncovered types. These pin the two decisions and the
judgement call that makes the first one usable early.
"""

from types import SimpleNamespace

import pytest

from pokeagent import catching
from pokeagent.catching import BALL_RESERVE, CORE_TEAM_SIZE, Catcher
from pokeagent.tactics import Tactics

pytestmark = pytest.mark.unit


class FakeMon:
    def __init__(self, species=1, level=5, nickname="EMBER"):
        self.species = species
        self.level = level
        self.nickname = nickname
        self.is_egg = False


class FakeRec:
    def __init__(self, score, parity_cost=0, why="because"):
        self.score = score
        self.parity_cost = parity_cost
        self.why = why


class FakeTeam:
    def __init__(self, rec=None):
        self.rec = rec

    def recommend_catch(self, candidates, party):
        return [self.rec] if self.rec else []


class FakeState:
    def __init__(self, balls=10, party=None):
        self._balls = balls
        self._party = party if party is not None else [FakeMon()]

    def bag(self):
        return {"poke_balls": {"POKé BALL": self._balls}} if self._balls else {}

    def party(self):
        return list(self._party)


class FakeNames:
    def species(self, sid):
        return {1: "TORCHIC", 4: "LOTAD"}.get(sid, f"SP{sid}")

    def item_data(self, item_id):
        return SimpleNamespace(price=200)


class FakeBattler:
    species = 4


class FakeBattle:
    def __init__(self, names):
        self.tactics = Tactics.__new__(Tactics)
        self.tactics.names = names
        self.tactics.items = {"ITEM_MASTER_BALL": 1}
        self.tactics._item_ids = {"POKEBALL": 4}

    def battler(self, i):
        return FakeBattler()


class FakeDriver:
    def __init__(self, balls=10, party=None):
        self.state = FakeState(balls, party)
        self.names = FakeNames()
        self.battle = FakeBattle(self.names)

    def outlook(self):
        return None


def frame(species="LOTAD", level=3, hp=20, max_hp=20, wild=True):
    return {
        "wild": wild,
        "enemy": {"species": species, "level": level, "hp": hp,
                  "max_hp": max_hp},
    }


# ---- is it worth a ball --------------------------------------------------

def test_a_trainer_battle_is_never_a_catch():
    c = Catcher(FakeDriver(), FakeTeam(FakeRec(10)))
    plan = c.plan(frame(wild=False))
    assert not plan and "trainer" in plan.reason


def test_the_ball_reserve_is_respected():
    """Story catches (a tutorial, a legendary) must not be starved by route
    filler."""
    c = Catcher(FakeDriver(balls=BALL_RESERVE), FakeTeam(FakeRec(10)))
    plan = c.plan(frame())
    assert not plan and "reserve" in plan.reason


def test_a_species_already_in_the_party_is_skipped():
    party = [FakeMon(species=4, nickname="LOTTAD")]
    c = Catcher(FakeDriver(party=party), FakeTeam(FakeRec(10)))
    plan = c.plan(frame(species="LOTAD"))
    assert not plan and "already have" in plan.reason


def test_a_full_party_does_NOT_stop_catching():
    """The opposite of what this file used to assert, and the reason the run
    caught nothing for hours.

    `GiveMonToPlayer` fills the first empty party slot and, when all six are
    taken, calls `SendMonToPC` (src/pokemon_2.c:964-983). A full party is a
    REDIRECT, not a refusal. The run has carried six mons since Petalburg, so
    the old gate declined every encounter in the game with "party is full"
    while 77 species sat seen-but-never-caught.
    """
    party = [FakeMon(species=i) for i in range(10, 16)]
    c = Catcher(FakeDriver(party=party), FakeTeam(FakeRec(10)))
    plan = c.plan(frame())
    assert plan, "a full party must still catch -- the game boxes it"


def test_catching_stops_only_when_there_is_nowhere_to_put_it():
    party = [FakeMon(species=i) for i in range(10, 16)]
    c = Catcher(FakeDriver(party=party), FakeTeam(FakeRec(10)))
    c.storage_has_room = lambda: False
    plan = c.plan(frame())
    assert not plan and "full" in plan.reason


def test_the_level_penalty_is_ignored_while_the_core_is_being_built():
    """THE judgement call. recommend_catch charges -0.25 a level against the
    training floor, so with a L24 lead every Route 102 wild scored about -5
    and the run caught nothing at all. Training fixes a level gap; nothing
    fixes an empty slot."""
    # The real numbers from the run that exposed this: a L2 WURMPLE scored
    # -2.5 against a L24 party, of which -5.5 was pure level penalty, leaving
    # +3.0 of actual coverage value.
    rec = FakeRec(score=-2.5, parity_cost=22, why="could fill PSYCHC later")
    c = Catcher(FakeDriver(), FakeTeam(rec))
    plan = c.plan(frame())
    assert plan, "a coverage-positive catch must survive the parity penalty"
    assert "parity cost 22 ignored" in plan.reason
    assert plan.score == pytest.approx(3.0)


def test_a_settled_team_judges_on_the_full_parity_aware_score():
    party = [FakeMon(species=i) for i in range(10, 10 + CORE_TEAM_SIZE)]
    rec = FakeRec(score=-5.5, parity_cost=22)
    c = Catcher(FakeDriver(party=party), FakeTeam(rec))
    assert not c.plan(frame()), "past the core, a catch must earn its training"


def test_a_candidate_with_no_coverage_value_is_refused_either_way():
    """Merit exactly zero is not a reason to spend a ball: -5.5 of score
    against 22 levels of parity cost is a mon that adds nothing but training."""
    c = Catcher(FakeDriver(), FakeTeam(FakeRec(score=-5.5, parity_cost=22)))
    assert not c.plan(frame())
    c2 = Catcher(FakeDriver(), FakeTeam(FakeRec(score=0, parity_cost=0)))
    assert not c2.plan(frame())


# ---- is this the turn to throw -------------------------------------------

def test_a_healthy_target_is_weakened_first():
    """Gen 3's catch rate scales with (3*max - 2*cur)/(3*max): throwing at
    full HP wastes balls."""
    c = Catcher(FakeDriver(), FakeTeam(FakeRec(10)))
    inner = lambda f: ("attack", 0)  # noqa: E731
    decide = c.policy(c.plan(frame()), inner=inner)
    assert decide(frame(hp=20, max_hp=20)) == ("attack", 0)


def test_a_weakened_target_gets_the_ball():
    c = Catcher(FakeDriver(), FakeTeam(FakeRec(10)))
    decide = c.policy(c.plan(frame()), inner=lambda f: ("attack", 0))
    action = decide(frame(hp=5, max_hp=20))
    assert action[0] == "ball"
    assert c.thrown == 1


def test_the_ball_goes_in_before_our_own_move_would_kill_it():
    """A fainted wild is gone. If the damage maths says this turn KOs, the
    ball has to go in now even at high HP."""
    driver = FakeDriver()
    driver.outlook = lambda: {"moves": [{"damage_max": 50}]}
    c = Catcher(driver, FakeTeam(FakeRec(10)))
    decide = c.policy(c.plan(frame()), inner=lambda f: ("attack", 0))
    assert decide(frame(hp=18, max_hp=20))[0] == "ball"


def test_no_balls_means_the_inner_policy_still_decides():
    c = Catcher(FakeDriver(balls=0), FakeTeam(FakeRec(10)))
    decide = c.policy(c.plan(frame()), inner=lambda f: ("attack", 3))
    assert decide(frame(hp=1, max_hp=20)) == ("attack", 3)


def test_a_fainted_target_is_not_thrown_at():
    c = Catcher(FakeDriver(), FakeTeam(FakeRec(10)))
    decide = c.policy(c.plan(frame()), inner=lambda f: ("attack", 0))
    assert decide(frame(hp=0, max_hp=20)) == ("attack", 0)


def test_the_throw_threshold_is_a_third_not_a_sliver():
    assert 0.3 <= catching.THROW_BELOW <= 0.4
    assert catching.DANGER_HP < catching.THROW_BELOW
