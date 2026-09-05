"""The damage model must not believe a move that cannot deliver its number.

This is the same lesson as `test_effective_power.py`, in the place that
actually picks moves. Fixing it only in `teaching.py` left the battle model
believing SPIT UP was a 100-power attack, and that lost the Elite Four.

Measured in Drake's room, from the battle log:

    T2  SPIT UP | ALTARIA  63->63
    T3  SPIT UP | ALTARIA  63->63
    ... nine consecutive turns, HP unchanged ...
    T10 SPIT UP | ALTARIA  34->161      <- it healed to full, twice

A level-100 PELIPPER lost that fight without landing a hit, because a 100 in
the ROM's table outranked SURF's 95. Once SPIT UP was spent, CUT came out --
power 2, an HM -- for exactly the same reason.

What is pinned here: a conditional-power move is reported as a STATUS move
with zero power and zero damage, so nothing that ranks on damage can ever
prefer it, while ordinary moves are untouched.
"""

import pytest

from pokeagent.tactics import CONDITIONAL_POWER


@pytest.mark.unit
def test_spit_up_is_listed_as_conditional():
    """The exact move that lost the gauntlet."""
    assert "SPIT UP" in CONDITIONAL_POWER


@pytest.mark.unit
def test_swallow_and_friends_are_listed():
    for name in ("SWALLOW", "DREAM EATER", "FOCUS PUNCH"):
        assert name in CONDITIONAL_POWER, name


@pytest.mark.unit
def test_ordinary_attacks_are_not_listed():
    """Over-listing would silently disarm the party."""
    for name in ("SURF", "HYDRO PUMP", "FLY", "OVERHEAT", "AERIAL ACE",
                 "CRUNCH", "TAKE DOWN", "SKY UPPERCUT"):
        assert name not in CONDITIONAL_POWER, name


@pytest.mark.unit
def test_the_analysis_zeroes_power_and_damage():
    """A conditional move must read as status/0/0, whatever the ROM says."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "pokeagent" /
           "tactics.py").read_text()
    # The three places the number leaks into ranking.
    assert re.search(r'"power": 0 if conditional else md\.power', src), \
        "power must be zeroed for conditional moves"
    assert re.search(r'"damage_min": 0 if conditional else lo', src), \
        "damage_min must be zeroed"
    assert re.search(r'"damage_max": 0 if conditional else hi', src), \
        "damage_max must be zeroed"
    assert 'kind = "status"' in src, \
        "a conditional move must be classified as status, not attack"
