"""The damage-first policy must never spend a turn on something that is not damage.

This exists because the scored tactics lost the Mossdeep gym twice from a
winning position by choosing SAND-ATTACK and a resisted EMBER while the enemy
sat on 2 HP.
"""
import pytest

from pokeagent.trek import Driver

pytestmark = pytest.mark.unit


def move(power, mult=1.0, pp=5):
    return {"power": power, "effect_mult": mult, "pp": pp}


def test_type_multiplier_beats_raw_power():
    # WING ATTACK 60 x0.5 vs BITE 60 x2 -- the exact choice that was thrown.
    frame = {"moves": [move(60, 0.5), move(60, 2.0)]}
    assert Driver.damage_first(frame) == ("attack", 1)


def test_status_moves_are_never_chosen():
    frame = {"moves": [move(0), move(0), move(20, 1.0)]}
    assert Driver.damage_first(frame) == ("attack", 2)


def test_a_move_with_no_pp_is_not_available():
    frame = {"moves": [move(120, 1.0, pp=0), move(40, 1.0, pp=3)]}
    assert Driver.damage_first(frame) == ("attack", 1)


def test_every_move_dry_takes_struggle():
    # The engine substitutes Struggle for a slot with no PP, so picking one is
    # how the turn gets taken. Declining left the harness cycling switch and
    # flee until the battle timed out, with a healthy bench behind the dry mon.
    assert Driver.damage_first({"moves": [move(60, pp=0), move(0, pp=0)]}) == (
        "attack", 0)


def test_nothing_to_attack_with_hands_the_turn_back():
    # A move with PP but no power is a status move: decline and let the
    # harness switch or use an item. Only a fully dry set means Struggle.
    assert Driver.damage_first({"moves": [move(0, pp=10)]}) is None
    assert Driver.damage_first({"moves": []}) is None
    assert Driver.damage_first({}) is None


def test_missing_multiplier_is_treated_as_neutral():
    frame = {"moves": [{"power": 80, "pp": 5}, move(50, 1.0)]}
    assert Driver.damage_first(frame) == ("attack", 0)
