"""A refused switch gets one chance, because it can wedge the whole battle.

`switch_to` failing leaves the engine in `sub_802DF88`, the party-menu RETURN
handler (battle_controller_player.c:1521-1532). It waits on
`gMain.callback2 == BattleMainCB2 && !gPaletteFade.active`, and a half-driven
party screen never satisfies that -- so the action menu never comes back, every
later action is refused with "the action menu is still up", and the battle ends
"stuck".

That is what held the run at 5/8 badges: Winona's fight wedged there, while the
same fight driven with an attack-only policy was won in 28 turns.
"""

import pytest

from pokeagent.battle import BattleSession


def _session(broken=False):
    s = BattleSession.__new__(BattleSession)
    s._dead_actions = set()
    s._switch_broken = broken
    s._futile = set()
    s._pp = {}
    return s


def _analysis(party_size=3, moves=()):
    return {
        "party": [{"hp": 20} for _ in range(party_size)],
        "moves": list(moves),
        "wild": True,
        "enemy": {"name": "ALTARIA"},
    }


@pytest.mark.unit
def test_a_healthy_session_will_offer_a_switch():
    s = _session(broken=False)
    s.futile = lambda action: False
    s.tactics = type("T", (), {"_cheapest_heal": staticmethod(lambda a: None)})()
    got = s._live_alternative(_analysis())
    assert got[0] == "switch"


@pytest.mark.unit
def test_a_broken_switch_is_never_offered_again():
    s = _session(broken=True)
    s.futile = lambda action: False
    s.tactics = type("T", (), {"_cheapest_heal": staticmethod(lambda a: None)})()
    got = s._live_alternative(_analysis())
    assert got != "switch" and (not isinstance(got, tuple) or got[0] != "switch")


@pytest.mark.unit
def test_with_no_moves_and_no_switch_it_flees_rather_than_wedge():
    s = _session(broken=True)
    s.futile = lambda action: False
    s.tactics = type("T", (), {"_cheapest_heal": staticmethod(lambda a: None)})()
    assert s._live_alternative(_analysis(party_size=0)) == "flee"


@pytest.mark.unit
def test_an_attack_still_wins_over_a_broken_switch():
    s = _session(broken=True)
    s.futile = lambda action: False
    s.tactics = type("T", (), {"_cheapest_heal": staticmethod(lambda a: None)})()
    got = s._live_alternative(
        _analysis(moves=[{"slot": 2, "pp": 10, "kind": "attack",
                          "damage_max": 30, "power": 40}])
    )
    assert got == ("attack", 2)
