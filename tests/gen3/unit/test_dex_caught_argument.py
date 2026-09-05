"""`dex_caught(species)` must answer about the species it was ASKED about.

It used to ignore the argument and read `_enemy_species_id()` instead. Inside
`plan()` the two agree -- the caller passes the enemy's own name -- so it was
right by luck rather than by construction. Asked about anything else it returned
a confident True, which is the "unknown, fall through to the old rules" default
wearing a species name, and it reads as "we already have one".
"""

import pytest

from pokeagent.catching import Catcher

pytestmark = pytest.mark.unit


class _Entry:
    def __init__(self, name, natdex):
        self.name = name
        self.natdex = natdex


class _Evolutions:
    def natdex(self, sid):
        return {263: 288}.get(sid)


class _Target:
    entries = [
        _Entry("NATU", 177),
        _Entry("WOBBUFFET", 202),
        _Entry("DODUO", 84),
        _Entry("PIKACHU", 25),
    ]
    evolutions = _Evolutions()

    def __init__(self, caught):
        self._caught = set(caught)

    def dex_flags(self, _state):
        return self._caught, set()


class _Driver:
    """`dex_caught` reads `self.d.state` to pass into `dex_flags`."""

    state = object()


def _catcher(caught, enemy_id=None):
    c = object.__new__(Catcher)
    c.d = _Driver()
    c.last_reason = ""
    c._dex = _Target(caught)
    c._dex_target = lambda: c._dex
    c._enemy_species_id = lambda: enemy_id
    return c


def test_it_answers_about_the_named_species():
    c = _catcher(caught={177, 202})
    assert c.dex_caught("NATU") is True
    assert c.dex_caught("WOBBUFFET") is True
    assert c.dex_caught("DODUO") is False
    assert c.dex_caught("PIKACHU") is False


def test_the_enemy_on_the_field_does_not_colour_the_answer():
    """A caught NATU standing there must not make DODUO look caught."""
    c = _catcher(caught={177}, enemy_id=999)
    assert c.dex_caught("DODUO") is False


def test_an_unknown_name_still_falls_back_to_the_enemy():
    """The old default is preserved where it is actually appropriate."""
    c = _catcher(caught={288}, enemy_id=263)   # 263 -> natdex 288, caught
    assert c.dex_caught("NOT-A-POKEMON") is True

    c2 = _catcher(caught=set(), enemy_id=263)  # same species, NOT caught
    assert c2.dex_caught("NOT-A-POKEMON") is False


def test_no_name_and_no_enemy_is_treated_as_already_owned():
    """Refusing to throw is the safe default when nothing can be resolved."""
    c = _catcher(caught=set(), enemy_id=None)
    assert c.dex_caught("NOT-A-POKEMON") is True


def test_names_are_matched_case_and_space_insensitively():
    c = _catcher(caught={84})
    assert c.dex_caught("doduo") is True
    assert c.dex_caught("  DODUO  ") is True
