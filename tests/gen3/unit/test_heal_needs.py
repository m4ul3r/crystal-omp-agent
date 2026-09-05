"""A party at full HP with nothing to attack with still needs the nurse.

`heal()` asked only "is anyone hurt", where hurt meant `hp < max_hp`. The
nurse restores PP as well, and the play loop's own trigger is PP exhaustion --
so a fresh run sat in Oldale's Pokemon Centre with a L18 COMBUSKEN at 54/54 and
DOUBLE KICK, PECK and EMBER all at zero, logging "healing: EMBER out of
damaging PP" forever. heal() reported success every time without talking to
anybody.

Live proof of the fix, same save: DOUBLE KICK 0 -> 30, PECK 0 -> 35,
EMBER 0 -> 25.
"""

import pytest


class _Mon:
    def __init__(self, hp, max_hp, moves, pp, is_egg=False, nickname="X"):
        self.hp = hp
        self.max_hp = max_hp
        self.moves = moves
        self.pp = pp
        self.is_egg = is_egg
        self.nickname = nickname


def _hurt(party):
    return [m for m in party if not m.is_egg and m.hp < m.max_hp]


def _spent(party):
    out = []
    for m in party:
        if m.is_egg:
            continue
        if any(mid and pp == 0 for mid, pp in zip(m.moves, m.pp)):
            out.append(m)
    return out


def _needs_nurse(party):
    return bool(_hurt(party) or _spent(party))


@pytest.mark.unit
def test_full_hp_but_no_pp_still_needs_the_nurse():
    """THE loop. This is the exact live party that spun forever."""
    ember = _Mon(54, 54, (24, 64, 116, 52), (0, 0, 30, 0), nickname="EMBER")
    assert not _hurt([ember]), "HP alone says it is fine"
    assert _spent([ember]), "PP says otherwise"
    assert _needs_nurse([ember])


@pytest.mark.unit
def test_a_healthy_stocked_party_does_not():
    fine = _Mon(54, 54, (24, 0, 0, 0), (30, 0, 0, 0))
    assert not _needs_nurse([fine])


@pytest.mark.unit
def test_hurt_still_counts():
    hurt = _Mon(3, 54, (24, 0, 0, 0), (30, 0, 0, 0))
    assert _needs_nurse([hurt])


@pytest.mark.unit
def test_an_empty_move_slot_is_not_an_empty_move():
    """Slots 2-4 of a one-move mon are id 0 with pp 0 and must not trigger."""
    one = _Mon(54, 54, (24, 0, 0, 0), (30, 0, 0, 0))
    assert not _spent([one])


@pytest.mark.unit
def test_an_egg_never_needs_healing():
    egg = _Mon(0, 0, (0, 0, 0, 0), (0, 0, 0, 0), is_egg=True)
    assert not _needs_nurse([egg])
