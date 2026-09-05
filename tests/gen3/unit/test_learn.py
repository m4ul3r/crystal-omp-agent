"""The move-learn decision.

A learn prompt permanently changes a Pokemon, and it arrives in two places:
inside a battle, and during an EVOLUTION scene. The second one was unguarded.
`advance_scene` presses A whenever a scene stalls, so Combusken's DOUBLE KICK
at L16 took two blind presses and overwrote whichever slot the cursor rested
on -- SCRATCH, the mon's strongest move -- while FOCUS ENERGY, a 0-power
status move, survived. `default_learn` exists precisely to prevent that and
was never consulted.

The policy is pure, so it is asserted directly here rather than through a
prompt fixture.
"""

import pytest

pytestmark = pytest.mark.unit


def prompt(new, current, index=0, nickname="EMBER"):
    """A learn prompt shaped like BattleSession.learn_prompt() returns."""
    return {
        "party_index": index,
        "nickname": nickname,
        "new_move": dict(new),
        "current": [dict(m) for m in current],
    }


def move(slot, name, power, hm=False, type_="NORMAL"):
    return {"slot": slot, "id": slot + 1, "name": name, "power": power,
            "type": type_, "hm": hm}


SCRATCH = move(0, "SCRATCH", 40)
GROWL = move(1, "GROWL", 0)
FOCUS_ENERGY = move(2, "FOCUS ENERGY", 0)
EMBER_MOVE = move(3, "EMBER", 40)

PECK = {"id": 64, "name": "PECK", "power": 35, "type": "FLYING"}
DOUBLE_KICK = {"id": 24, "name": "DOUBLE KICK", "power": 30, "type": "FIGHTING"}


class _Policy:
    """Just enough BattleSession to run the move-learn policy.

    It needs `move_value` (which reads multi-hit and STAB) and the learner's
    types, and nothing else -- no emulator, no ROM. A decision this important
    should be testable without booting a Game Boy, so the two lookups are
    stubbed and the real ranking code runs untouched.
    """

    from pokeagent.battle import BattleSession

    MULTI_HIT_EFFECTS = BattleSession.MULTI_HIT_EFFECTS
    move_value = BattleSession.move_value
    default_learn = BattleSession.default_learn

    def __init__(self, owner_types=()):
        self._types = tuple(owner_types)

    def _learner_types(self, prompt):
        return self._types


@pytest.fixture()
def learn():
    """The policy with a typeless learner, so base power drives the ranking
    unless a test asks for STAB."""
    return _Policy().default_learn


def test_a_damaging_move_replaces_a_status_move(learn):
    """Torchic at L16: SCRATCH/GROWL/FOCUS ENERGY/EMBER, learning PECK.
    GROWL is the lowest-slot status move, so it goes."""
    assert learn(prompt(PECK, [SCRATCH, GROWL, FOCUS_ENERGY, EMBER_MOVE])) == 1


def test_the_regression_double_kick_must_not_eat_scratch(learn):
    """THE BUG. Combusken at L16 learning DOUBLE KICK with
    SCRATCH/PECK/FOCUS ENERGY/EMBER. The blind-A path overwrote slot 0
    (SCRATCH, 40 power); the policy must take FOCUS ENERGY, the 0-power
    status move in slot 2."""
    current = [SCRATCH, move(1, "PECK", 35, type_="FLYING"),
               FOCUS_ENERGY, EMBER_MOVE]
    assert learn(prompt(DOUBLE_KICK, current)) == 2


def test_a_weaker_damaging_move_is_declined_outright(learn):
    """With no status move to give up, a 30-power move must not displace a
    40-power one just because it is new."""
    current = [SCRATCH, move(1, "PECK", 35), move(2, "CUT", 50),
               EMBER_MOVE]
    assert learn(prompt(DOUBLE_KICK, current)) is None


def test_a_stronger_move_replaces_the_weakest_damaging_one(learn):
    current = [move(0, "TACKLE", 35), move(1, "PECK", 35),
               move(2, "SCRATCH", 40), EMBER_MOVE]
    strong = {"id": 53, "name": "FLAMETHROWER", "power": 95, "type": "FIRE"}
    assert learn(prompt(strong, current)) == 0


def test_an_hm_move_is_never_forgotten(learn):
    """teach_hm ate a party member's only Surf in the predecessor project and
    stranded the run."""
    current = [move(0, "SURF", 95, hm=True), move(1, "PECK", 35),
               move(2, "SCRATCH", 40), EMBER_MOVE]
    strong = {"id": 53, "name": "FLAMETHROWER", "power": 95, "type": "FIRE"}
    assert learn(prompt(strong, current)) != 0


def test_an_all_hm_moveset_declines_rather_than_erasing_one(learn):
    current = [move(i, f"HM{i}", 80, hm=True) for i in range(4)]
    assert learn(prompt(DOUBLE_KICK, current)) is None


def test_a_status_move_may_replace_another_status_move(learn):
    current = [SCRATCH, GROWL, FOCUS_ENERGY, EMBER_MOVE]
    swagger = {"id": 207, "name": "SWAGGER", "power": 0, "type": "NORMAL"}
    assert learn(prompt(swagger, current)) == 1


def test_a_status_move_never_displaces_the_last_damaging_move(learn):
    """A moveset that drifts to all-status is how a mon Struggles to death."""
    current = [move(0, "GROWL", 0), move(1, "LEER", 0),
               move(2, "TAIL WHIP", 0), move(3, "SCRATCH", 40)]
    swagger = {"id": 207, "name": "SWAGGER", "power": 0, "type": "NORMAL"}
    slot = learn(prompt(swagger, current))
    assert slot != 3, "the only damaging move must survive"


def test_a_status_move_is_declined_when_nothing_damaging_exists(learn):
    current = [move(i, f"STATUS{i}", 0) for i in range(4)]
    swagger = {"id": 207, "name": "SWAGGER", "power": 0, "type": "NORMAL"}
    assert learn(prompt(swagger, current)) is None


def test_a_short_moveset_is_not_a_decision(learn):
    """Fewer than four moves means the game just adds it; a prompt with an
    empty slot should never reach the policy, but if it does the policy must
    not invent a victim."""
    assert learn(prompt(PECK, [])) is None


# ---- what a move is actually worth ---------------------------------------

def test_a_multi_hit_move_counts_its_hits():
    """DOUBLE KICK is 30 base power and lands twice. Ranking on base power
    alone throws it away for a 35-power single hit, which is what the run
    did before this existed."""
    p = _Policy()
    kick = move(0, "DOUBLE KICK", 30, type_="FIGHT")
    kick["effect"] = 44          # EFFECT_DOUBLE_HIT
    kick["accuracy"] = 100
    assert p.move_value(kick) == pytest.approx(60.0)


def test_same_type_moves_get_the_stab_bonus():
    p = _Policy()
    ember = move(0, "EMBER", 40, type_="FIRE")
    ember["effect"] = 4
    ember["accuracy"] = 100
    assert p.move_value(ember, ("FIRE", "FIGHT")) == pytest.approx(60.0)
    assert p.move_value(ember, ("WATER",)) == pytest.approx(40.0)


def test_stab_and_multi_hit_compound():
    """The real case: Combusken is FIRE/FIGHTING, so DOUBLE KICK is worth 90
    against PECK's 35 -- the opposite of what base power says."""
    p = _Policy()
    mine = ("FIRE", "FIGHT")
    kick = move(0, "DOUBLE KICK", 30, type_="FIGHT")
    kick["effect"], kick["accuracy"] = 44, 100
    peck = move(1, "PECK", 35, type_="FLYING")
    peck["effect"], peck["accuracy"] = 0, 100
    assert p.move_value(kick, mine) == pytest.approx(90.0)
    assert p.move_value(kick, mine) > p.move_value(peck, mine)


def test_accuracy_discounts_a_move():
    p = _Policy()
    shaky = move(0, "SHAKY", 100, type_="NORMAL")
    shaky["effect"], shaky["accuracy"] = 0, 50
    assert p.move_value(shaky) == pytest.approx(50.0)


def test_a_status_move_is_worth_nothing_to_the_ranking():
    p = _Policy()
    growl = move(0, "GROWL", 0, type_="NORMAL")
    growl["effect"], growl["accuracy"] = 0, 100
    assert p.move_value(growl, ("NORMAL",)) == 0.0


def test_the_better_move_survives_even_when_its_base_power_is_lower():
    """With no status move to give up, a 35-power PECK must NOT displace a
    30-power DOUBLE KICK that is really worth 90."""
    p = _Policy(("FIRE", "FIGHT"))
    kick = move(0, "DOUBLE KICK", 30, type_="FIGHT")
    kick["effect"], kick["accuracy"] = 44, 100
    others = [move(1, "SCRATCH", 40), move(2, "CUT", 50), move(3, "EMBER", 40)]
    for m in others:
        m["effect"], m["accuracy"] = 0, 100
    peck = {"id": 64, "name": "PECK", "power": 35, "type": "FLYING",
            "effect": 0, "accuracy": 100}
    slot = p.default_learn(prompt(peck, [kick] + others))
    assert slot != 0, "DOUBLE KICK is the strongest move on that set"
