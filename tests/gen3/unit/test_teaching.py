"""Refuse before pressing, and believe only the moveset.

Two HMs sat in the bag for three badges -- CUT from Rustboro, ROCK SMASH from
Mauville -- while `field_moves()` reported all-None, because nothing in the
Gen-3 port could teach them. The road north out of Mauville is held by two
breakable rocks, so that gap was worth a badge.

The guards matter as much as the driving. A machine flow that presses A first
and asks questions later teaches the wrong move to the wrong mon, and there is
no undo without a savestate.
"""

import pytest

from pokeagent.teaching import Teacher

pytestmark = pytest.mark.unit

ROCK_SMASH = 249
CUT = 15
HM06 = 344
HM01 = 339


class Mon:
    def __init__(self, nickname, species, level=30, moves=(), egg=False):
        self.nickname, self.species, self.level = nickname, species, level
        self.moves, self.is_egg = list(moves), egg


class FakeNames:
    def __init__(self, learnable):
        self.learnable = learnable      # {(species, item_id)} that can learn

    def species(self, sid):
        return {1: "MIGHTYENA", 2: "ARON", 3: "NINJASK"}[sid]

    def move(self, mid):
        return {ROCK_SMASH: "ROCK SMASH", CUT: "CUT"}[mid]

    def learns_tm(self, species, item_id):
        return (species, item_id) in self.learnable


def make(party, learnable, bag=(HM06,)):
    t = object.__new__(Teacher)
    t.names = FakeNames(learnable)
    t.last_reason = None
    t.taught_to = None
    t.machine_move = lambda iid: {HM06: (ROCK_SMASH, "ROCK SMASH"),
                                  HM01: (CUT, "CUT")}.get(iid)
    t.pocket_items = lambda pocket=2: [(i, iid, 1) for i, iid in enumerate(bag)]
    t._item_id = lambda name: {"HM06": HM06, "HM01": HM01}.get(str(name).upper())

    class D:
        def __init__(self, p):
            self.state = type("S", (), {"party": staticmethod(lambda: p)})()
    t.d = D(party)
    return t


def test_a_machine_not_in_the_bag_is_refused_before_anything_is_pressed():
    t = make([Mon("MIGHTYENA", 1)], {(1, HM01)}, bag=(HM06,))
    assert t.teach("HM01") is False
    assert "not in the bag" in t.last_reason


def test_an_unknown_machine_is_refused():
    t = make([Mon("MIGHTYENA", 1)], set())
    t._item_id = lambda name: None
    assert t.teach("TM99") is False
    assert "not an item" in t.last_reason


def test_a_species_that_cannot_learn_it_is_refused_by_name():
    """NINJASK cannot learn ROCK SMASH. Saying so beats opening the bag and
    discovering it from a greyed-out list nobody can read."""
    t = make([Mon("NINJA", 3)], set())
    assert t.teach("HM06", "NINJA") is False
    assert "cannot learn" in t.last_reason


def test_a_mon_that_already_knows_it_is_refused():
    t = make([Mon("MIGHTYENA", 1, moves=[ROCK_SMASH])], {(1, HM06)})
    assert t.teach("HM06", "MIGHTYENA") is False
    assert "already knows" in t.last_reason


def test_no_eligible_party_member_is_refused():
    t = make([Mon("NINJA", 3)], set())
    assert t.teach("HM06") is False
    assert "no party member can learn" in t.last_reason


def test_eggs_are_never_candidates():
    """An egg has no moveset to write into."""
    t = make([Mon("EGG", 1, egg=True), Mon("ROCKY", 2)], {(1, HM06), (2, HM06)})
    assert [m.nickname for _i, m in t.candidates(HM06)] == ["ROCKY"]


def test_candidates_skip_those_who_already_know_it():
    party = [Mon("MIGHTYENA", 1, moves=[ROCK_SMASH]), Mon("ROCKY", 2)]
    t = make(party, {(1, HM06), (2, HM06)})
    assert [m.nickname for _i, m in t.candidates(HM06)] == ["ROCKY"]


def test_the_machine_slot_is_a_list_position_not_a_raw_slot():
    """The bag compacts and SORTS its pocket, so the cursor index is the
    position among present items. Using the raw slot number put the cursor on
    a different machine entirely."""
    t = make([Mon("ROCKY", 2)], {(2, HM06)}, bag=(296, 322, 327, 335, HM01, HM06))
    assert t.machine_slot(HM06) == 5
    assert t.machine_slot(HM01) == 4
    assert t.machine_slot(999) is None


def test_knows_reports_the_holder():
    party = [Mon("ROCKY", 2), Mon("MIGHTYENA", 1, moves=[ROCK_SMASH])]
    t = make(party, {(2, HM06)})
    assert t.knows(HM06).nickname == "MIGHTYENA"
    assert t.knows(HM01) is None
