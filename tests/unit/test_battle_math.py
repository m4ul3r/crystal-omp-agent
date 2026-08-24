"""Battle math against the game's own type chart and move table shapes."""
from pathlib import Path

import pytest

from crystalagent.battle import Battle, BattleData, _parse_matchups, _parse_types

pytestmark = pytest.mark.unit

POKE = Path(__file__).resolve().parents[2].parent


@pytest.fixture(scope="module")
def chart():
    if not (POKE / "data/types/type_matchups.asm").exists():
        pytest.skip("pokecrystal checkout not found")
    bd = BattleData.__new__(BattleData)
    bd.types = _parse_types(POKE / "constants/type_constants.asm")
    bd.matchups = _parse_matchups(
        POKE / "data/types/type_matchups.asm", bd.types)
    return bd


def mv(power, etype, acc=100):
    return {"power": power, "type": etype, "accuracy": acc}


class FakeBattle:
    """Just the surface Battle.best_move touches."""

    def __init__(self, data, slots, my_types, enemy_types, disabled=None):
        self.data = data
        self.slots = slots          # [(move_id, pp), ...]
        self.my_types = my_types
        self.enemy_types = enemy_types
        self.disabled = disabled

    def me(self):
        return {"moves": self.slots, "types": self.my_types}

    def enemy(self):
        return {"types": self.enemy_types}

    def _disabled_move_id(self):
        return self.disabled


class FakeData:
    """dict of move records plus the real type-chart lookup."""

    def __init__(self, chart, moves):
        self.chart = chart
        self.moves = moves

    def effectiveness(self, atk_type, def_types):
        return self.chart.effectiveness(atk_type, def_types)


def _fake_data(chart):
    T = chart.types
    return FakeData(chart, {
        1: mv(60, T["NORMAL"]),
        2: mv(100, T["NORMAL"], acc=50),
        3: mv(40, T["NORMAL"]),
        4: mv(90, T["WATER"]),
        5: mv(0, T["NORMAL"]),                      # status move
        6: mv(100, T["NORMAL"], acc=0),             # accuracy 0 -> score 0
        7: mv(50, T["GHOST"]),
    })


def test_pp_zero_slot_excluded(chart):
    fb = FakeBattle(_fake_data(chart), [(3, 0), (1, 20)],
                    [chart.types["NORMAL"]], [chart.types["ROCK"]])
    assert Battle.best_move(fb) == 1


def test_disabled_slot_excluded(chart):
    fb = FakeBattle(_fake_data(chart), [(2, 20), (3, 20)],
                    [chart.types["NORMAL"]], [chart.types["ROCK"]],
                    disabled=2)
    assert Battle.best_move(fb) == 1


def test_all_dry_returns_none(chart):
    fb = FakeBattle(_fake_data(chart), [(3, 0)], [], [])
    assert Battle.best_move(fb) is None


def test_accuracy_weighted_ranking(chart):
    # 60 * 1.00 beats 100 * 0.50
    fb = FakeBattle(_fake_data(chart), [(1, 20), (2, 20)],
                    [chart.types["NORMAL"]], [chart.types["ROCK"]])
    assert Battle.best_move(fb) == 0


def test_stab_multiplies_score(chart):
    # equal power, different type: only the STABed water move's score
    # doubles into the rock enemy -- it must win
    T = chart.types
    data = FakeData(chart, {1: mv(80, T["NORMAL"]),
                            2: mv(80, T["WATER"])})
    fb = FakeBattle(data, [(1, 10), (2, 10)], [T["WATER"]], [T["ROCK"]])
    assert Battle.best_move(fb) == 1


def test_status_move_scores_one_point(chart):
    # lone usable move is a status move: still picked over nothing
    fb = FakeBattle(_fake_data(chart), [(5, 10)],
                    [chart.types["NORMAL"]], [chart.types["ROCK"]])
    assert Battle.best_move(fb) == 0
    # status (1.0) beats an accuracy-0 attack (score 0)
    fb = FakeBattle(_fake_data(chart), [(6, 10), (5, 10)],
                    [chart.types["NORMAL"]], [chart.types["ROCK"]])
    assert Battle.best_move(fb) == 1


def test_immune_move_never_selected(chart):
    # Ghost 50-power vs Normal enemy scores 0: the plain 40 wins
    fb = FakeBattle(_fake_data(chart), [(7, 10), (3, 10)],
                    [chart.types["NORMAL"]], [chart.types["NORMAL"]])
    assert Battle.best_move(fb) == 1
