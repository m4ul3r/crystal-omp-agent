"""Never recommend switching to the mon that is already out.

The index test looks sufficient and is not. `state.party()` and the engine's
`gBattlerPartyIndexes` can disagree about WHERE a mon sits: promoting a trainee
reorders `gPlayerParty`, and a battle that started before the swap keeps the
old index. Live, that produced "switch to ROCKY" while ROCKY was the mon taking
the damage -- the driver confirmed the SHIFT, the engine ignored it because
there was nothing to change, and the battle burned six turns before the stall
guard dropped the policy. Every battle where a switch looked good paid it.
"""

import pytest

from pokeagent.tactics import Tactics

pytestmark = pytest.mark.unit


class Mon:
    def __init__(self, nickname, species, level, hp=20, max_hp=20, egg=False):
        self.nickname, self.species, self.level = nickname, species, level
        self.hp, self.max_hp, self.is_egg = hp, max_hp, egg


def check(mon, index, party, me, active_index):
    """Call the predicate the way switch_options does."""
    tactics = object.__new__(Tactics)
    return Tactics._already_on_the_field(
        tactics, mon, index,
        {"party": party, "me": me, "active_party_index": active_index},
    )


def test_the_index_case_still_works():
    rocky, ember = Mon("ROCKY", 304, 11), Mon("EMBER", 255, 30)
    party = [rocky, ember]
    assert check(rocky, 0, party, rocky, 0) is True
    assert check(ember, 1, party, rocky, 0) is False


def test_identity_catches_what_the_index_misses():
    """The bug, exactly: the active mon sits at party index 5 as far as the
    engine is concerned, but `party()` hands it back at 0."""
    rocky = Mon("ROCKY", 304, 11, hp=5)
    party = [rocky, Mon("EMBER", 255, 30)]
    assert check(rocky, 0, party, rocky, 5) is True, \
        "ROCKY is on the field; it cannot also be the answer to being on it"


def test_a_genuine_bench_mon_is_still_offered():
    """The fix must not empty the bench -- switching is how a bad matchup gets
    survived."""
    rocky = Mon("ROCKY", 304, 11, hp=5)
    ember = Mon("EMBER", 255, 30)
    assert check(ember, 1, [rocky, ember], rocky, 5) is False


def test_a_same_species_teammate_is_not_confused_for_the_active_one():
    """Two Arons on a team is legal. Nickname, species and level together
    separate them; any one alone would not."""
    active = Mon("ROCKY", 304, 11)
    twin = Mon("PEBBLE", 304, 11)
    assert check(twin, 1, [active, twin], active, 0) is False


def test_a_same_nickname_different_level_mon_is_distinguished():
    active = Mon("ROCKY", 304, 11)
    other = Mon("ROCKY", 304, 24)
    assert check(other, 1, [active, other], active, 0) is False


def test_when_the_active_mon_has_fainted_nothing_is_excluded():
    """The engine reorders gPlayerParty on a faint, so the stale index starts
    pointing at a HEALTHY benched mon. Excluding it blindly emptied the list at
    the one moment a replacement was actually needed."""
    fainted = Mon("ROCKY", 304, 11, hp=0)
    healthy = Mon("EMBER", 255, 30)
    assert check(healthy, 0, [healthy], fainted, 0) is False


def test_no_active_mon_yet_excludes_nothing():
    """Before the battle mon block is populated there is nothing to compare
    against, and refusing every switch would be worse than offering them."""
    ember = Mon("EMBER", 255, 30)
    assert check(ember, 0, [ember], None, 0) is False
