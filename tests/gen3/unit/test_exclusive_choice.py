"""Route 111's two fossils are one choice, and the dex target must say so.

Taking either fossil sets BOTH hide flags and removes BOTH objects in the same
script (pret/data/maps/Route111/scripts.inc:57-59, 79-81). No `clearflag` for
either exists anywhere in the game, and -- the part that misled a whole
session of planning -- **Sapphire has no Desert Underpass at all**; that map is
an Emerald addition. So one fossil line, two dex slots, can never be
registered on any single save.

Two separate defects are covered here:

1. The exclusion branch was DEAD. It resolved its species through
   `getattr(self.names, "species_id", lambda _: None)`, and `names` has no
   `species_id` method, so the fallback ran every time and the fossil pair was
   never treated as exclusive -- in either direction.
2. Even working, `choice_locked` only fires AFTER a choice is made, so before
   it the target counted both lines. `exclusive_surplus` is the pending-choice
   half, and it returns a COUNT rather than locking a line, because until a
   fossil is taken both are still legitimately routable.
"""

import pytest

from pokeagent.dex import DexTarget


class FakeEvo:
    def __init__(self, to_species):
        self.to_species = to_species


class FakeEvolutions:
    """LILEEP(388)->CRADILY(389), ANORITH(390)->ARMALDO(391), and a 3-long
    starter line so the starter group is shaped like the real one."""

    CHAIN = {388: [389], 390: [391], 1: [2], 2: [3], 4: [5], 5: [6], 7: [8], 8: [9]}

    def evolutions(self, species):
        return [FakeEvo(s) for s in self.CHAIN.get(species, [])]

    def natdex(self, species):
        return species


class FakeConsts:
    species = {"SPECIES_LILEEP": 388, "SPECIES_ANORITH": 390}


def _target(caught, seen=frozenset()):
    t = DexTarget.__new__(DexTarget)
    t.evolutions = FakeEvolutions()
    t.consts = FakeConsts()
    t.names = object()          # deliberately WITHOUT species_id
    t.warnings = []
    t.starters = (1, 4, 7)
    t.dex_flags = lambda state=None: (frozenset(caught), frozenset(seen))
    return t


@pytest.mark.unit
def test_the_fossil_pair_is_recognised_at_all():
    """The regression guard for the dead branch.

    `names` here has no `species_id`, exactly like the real one. If the lookup
    goes back through that attribute, there is no fossil group and the surplus
    silently returns 0.
    """
    t = _target(caught={1})
    groups = t._exclusive_groups(*t.dex_flags())

    sizes = [sorted(len(ln) for ln in lines) for lines, _ in groups]
    assert [2, 2] in sizes, "the Route 111 fossil pair was not modelled"


@pytest.mark.unit
def test_two_slots_are_unreachable_while_the_fossil_choice_is_open():
    t = _target(caught={1})           # starter chosen, no fossil taken
    assert t.exclusive_surplus() == 2


@pytest.mark.unit
def test_taking_a_fossil_locks_the_other_line_and_ends_the_surplus():
    """Once chosen, `choice_locked` owns it and the surplus must not double-count."""
    t = _target(caught={1, 388}, seen={388})

    locked = t.choice_locked()
    # The two unchosen STARTER lines are locked here too, which is correct and
    # not what this test is about -- so assert the fossil half specifically.
    assert {390, 391} <= locked, "the unchosen fossil line was not locked"
    assert 388 not in locked and 389 not in locked, "locked the line we took"
    assert t.exclusive_surplus() == 0


@pytest.mark.unit
def test_merely_SEEING_a_fossil_mon_counts_as_the_choice():
    """The item is what destroys the other fossil, and that happens long
    before anything is registered as caught -- so `seen` has to count."""
    t = _target(caught={1}, seen={390})

    locked = t.choice_locked()
    assert {388, 389} <= locked, "seeing ANORITH did not lock the LILEEP line"
    assert 390 not in locked and 391 not in locked
    assert t.exclusive_surplus() == 0


@pytest.mark.unit
def test_an_unchosen_starter_is_not_locked_but_is_surplus():
    """No starter caught yet: nothing is identified as gone, but six of the
    nine starter slots are unreachable regardless of what the player picks."""
    t = _target(caught=set())

    assert t.choice_locked() == frozenset()
    # 6 from the starters (9 total - the 3 of one line) + 2 from the fossils
    assert t.exclusive_surplus() == 8


@pytest.mark.unit
def test_a_missing_species_constant_warns_instead_of_silently_miscounting():
    t = _target(caught={1})
    t.consts = type("C", (), {"species": {}})()

    t._exclusive_groups(*t.dex_flags())

    assert any("fossil" in w for w in t.warnings)
