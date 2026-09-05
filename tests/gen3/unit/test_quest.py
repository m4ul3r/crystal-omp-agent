"""The story spine: what advances the GAME.

A loop that only grinds is stuck forever -- 232 battles, level 19, still 0/8
badges. These tests pin the decision that fixes that, and the two facts the
spine must never get wrong: the badge order, and where the leader stands.
"""

import pytest

from pokeagent import quest
from pokeagent.quest import SPINE, Quest

pytestmark = pytest.mark.unit


class FakeMon:
    def __init__(self, level=5, hp=20, max_hp=20, nickname="EMBER"):
        self.level = level
        self.hp = hp
        self.max_hp = max_hp
        self.nickname = nickname
        self.is_egg = False


class FakeState:
    def __init__(self, badges=0, party=None):
        self._badges = badges
        self._party = party if party is not None else [FakeMon()]

    def badges(self):
        # The real GameState.badges() returns the NAMES of the badges held.
        return [f"BADGE{i:02}" for i in range(1, self._badges + 1)]

    def party(self):
        return list(self._party)


class FakeDriver:
    def __init__(self, badges=0, party=None, map_name="Route101"):
        self.state = FakeState(badges, party)
        self._map = map_name
        self.nav = None

    def map_name(self):
        return self._map


TRAINERS = {
    "Roxanne": {"party": [{"species": "GEODUDE", "level": 14},
                          {"species": "NOSEPASS", "level": 15}]},
    "Brawly": {"party": [{"species": "MACHOP", "level": 17},
                         {"species": "MAKUHITA", "level": 18}]},
}


def q(driver):
    return Quest(driver, trainers=TRAINERS)


# ---- the spine -----------------------------------------------------------

def test_hoenn_has_eight_badges_not_nine():
    """Tate and Liza share one gym, one badge and one double battle. Counting
    them separately puts Wallace at badge 9."""
    assert len(SPINE) == 8
    assert [row[0] for row in SPINE] == list(range(1, 9))
    assert SPINE[-1][1] == "Wallace"
    assert SPINE[6][1] == "TateAndLiza"


def test_every_gym_names_a_real_map(mapdata):
    for _, _, gym_map, town in SPINE:
        assert gym_map in mapdata.index, f"{gym_map} is not a map"
        assert town in mapdata.index, f"{town} is not a map"


def test_every_leader_is_found_on_their_own_gym_map(mapdata):
    """Matched on the script label out of the map's object_events, so this
    breaks loudly if a leader moves rather than silently walking nowhere."""
    driver = FakeDriver()
    driver.nav = mapdata
    quest_ = q(driver)
    for _, leader, gym_map, _ in SPINE:
        cell = quest_.leader_cell(gym_map, leader)
        assert cell is not None, f"{leader} not found on {gym_map}"
        x, y = cell
        assert x >= 0 and y >= 0


def test_the_double_battle_gym_matches_either_sprite(mapdata):
    driver = FakeDriver()
    driver.nav = mapdata
    assert q(driver).leader_cell("MossdeepCity_Gym", "TateAndLiza") is not None


# ---- the decision --------------------------------------------------------

def test_a_weak_party_is_told_to_train_to_the_leaders_ace():
    obj = q(FakeDriver(party=[FakeMon(level=5)])).next_objective()
    assert obj.kind == "train"
    assert obj.target_level == 15 + quest.LEVEL_MARGIN
    assert obj.leader == "Roxanne"


def test_a_strong_party_elsewhere_is_told_to_travel():
    obj = q(FakeDriver(party=[FakeMon(level=20)])).next_objective()
    assert obj.kind == "travel"
    assert obj.map_name == "RustboroCity_Gym"


def test_a_strong_party_inside_the_gym_fights():
    driver = FakeDriver(party=[FakeMon(level=20)], map_name="RustboroCity_Gym")
    obj = q(driver).next_objective()
    assert obj.kind == "fight_leader"
    assert obj.badge == 1


def test_a_hurt_party_heals_before_the_gym():
    """Walking into a leader at a third HP is how a run loses its lead."""
    driver = FakeDriver(party=[FakeMon(level=20, hp=5, max_hp=50)])
    obj = q(driver).next_objective()
    assert obj.kind == "heal"


def test_levels_outrank_healing():
    """No point healing to full at level 5: training will spend it anyway."""
    driver = FakeDriver(party=[FakeMon(level=5, hp=1, max_hp=50)])
    assert q(driver).next_objective().kind == "train"


def test_the_badge_count_selects_the_next_gym():
    driver = FakeDriver(badges=1, party=[FakeMon(level=30)])
    obj = q(driver).next_objective()
    assert obj.badge == 2 and obj.leader == "Brawly"


def test_all_eight_badges_finishes_the_spine():
    obj = q(FakeDriver(badges=8, party=[FakeMon(level=60)])).next_objective()
    assert obj.kind == "done"
    assert "Elite Four" in obj.detail


def test_a_missing_trainer_table_does_not_invent_a_level():
    """No guide built means no level target -- never a guessed one."""
    driver = FakeDriver(party=[FakeMon(level=5)])
    bare = Quest(driver, trainers={})
    assert bare.leader_level("Roxanne") is None
    assert bare.next_objective().kind in ("travel", "heal", "fight_leader")


def test_an_empty_party_does_not_crash_the_decision():
    driver = FakeDriver(party=[])
    assert q(driver).next_objective().kind == "train"


# ---- reading the badge count --------------------------------------------

def test_the_badge_count_is_read_from_names_not_coerced():
    """GameState.badges() returns names. `int(...)` on that raises TypeError,
    and a bare `except` turned the raise into a permanent 0: the run beat
    Roxanne, FLAG_BADGE01_GET was set, and the loop re-challenged her every
    step for the rest of the night because its own counter said zero."""
    driver = FakeDriver(badges=1, party=[FakeMon(level=18)])
    assert q(driver).badges() == 1


def test_a_held_badge_advances_the_target_to_the_next_leader():
    driver = FakeDriver(badges=1, party=[FakeMon(level=18)])
    obj = q(driver).next_objective()
    assert obj.leader == "Brawly", "badge 1 held means badge 2 is next"


def test_an_integer_badge_count_is_also_accepted():
    """A different adapter may report a count instead of names; both are fine,
    a silent zero is not."""
    driver = FakeDriver(badges=0)
    driver.state.badges = lambda: 3
    assert q(driver).badges() == 3


def test_an_unreadable_save_block_reports_none_rather_than_crashing():
    driver = FakeDriver()

    def boom():
        raise RuntimeError("no save block yet")

    driver.state.badges = boom
    assert q(driver).badges() == 0


def test_a_nonsense_badge_read_does_not_crash_the_loop():
    driver = FakeDriver()
    driver.state.badges = lambda: object()
    assert q(driver).badges() == 0
