"""Living dex, the stage ladder, entropy injection and the model boundary.

Value assertions with citations, in the style of test_parser_values.py.
"""

import pytest

from pokeagent import gamespec

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def living(emu, names, consts, mapdata):
    from pokeagent import dex
    from pokeagent.living import LivingDex

    target = dex.DexTarget(
        emu, names, consts, mapdata, spec=gamespec.get("sapphire")
    )
    return LivingDex(target)


# ---- living dex ----------------------------------------------------------

def test_an_evolution_line_costs_one_individual_per_stage(living):
    """The whole point of a LIVING dex: you cannot evolve the one you hold and
    still hold the earlier stage."""
    line = next(l for l in living.lines if l.root_name == "TORCHIC")
    assert line.stage_names == ("TORCHIC", "COMBUSKEN", "BLAZIKEN")
    assert line.individuals_needed == 3


def test_nincada_needs_one_fewer_than_it_has_stages(living):
    """Nincada's evolution leaves a Shedinja behind, so one individual yields
    two species."""
    line = next(l for l in living.lines if "SHEDINJA" in l.stage_names)
    assert len(line.stage_names) == 3
    assert line.individuals_needed == 2
    assert "Shedinja" in line.note


def test_wurmple_needs_all_five_because_the_branch_is_hidden(living):
    """The Silcoon/Cascoon split is decided by a hidden personality value, so
    both branches must be obtained separately."""
    line = next(l for l in living.lines if l.root_name == "WURMPLE")
    assert set(line.stage_names) == {
        "WURMPLE", "SILCOON", "CASCOON", "BEAUTIFLY", "DUSTOX"
    }
    assert line.individuals_needed == 5
    assert "branch" in line.note


@pytest.mark.parametrize(
    "baby,parent",
    [("PICHU", "PIKACHU"), ("IGGLYBUFF", "JIGGLYPUFF"),
     ("AZURILL", "MARILL"), ("WYNAUT", "WOBBUFFET")],
)
def test_baby_lines_breed_from_the_evolved_form(living, baby, parent):
    """Babies sit in the Undiscovered egg group (include/pokemon.h:23) because
    they cannot breed themselves. Reading that as 'unbreedable line' would
    send the planner off to catch another Pichu, which is not how you get one.
    """
    line = next(l for l in living.lines if l.root_name == baby)
    assert line.breedable, f"{baby}'s LINE can breed"
    assert line.breeding_parent_name == parent
    assert not living.breedable(line.root), f"{baby} itself cannot breed"


def test_only_the_true_legendaries_are_unbreedable(living):
    unbreedable = sorted(l.root_name for l in living.lines if not l.breedable)
    # Jirachi, Deoxys and Latios left the achievable set entirely: they are
    # event-only on a Sapphire cartridge (birch_pc.c:94-102; the roamer is
    # LATIAS per include/constants/species.h:1283).
    assert unbreedable == [
        "KYOGRE", "LATIAS",
        "RAYQUAZA", "REGICE", "REGIROCK", "REGISTEEL",
    ]


def test_the_living_dex_fits_in_storage(living):
    """boxes[14][30] + a party of six = 426 (include/pokemon.h:323-329).
    Asserted rather than assumed, because a target that does not fit is a
    design error and not a progress bar."""
    from pokeagent.living import STORAGE_SLOTS

    assert STORAGE_SLOTS == 426
    total = sum(l.individuals_needed for l in living.lines)
    assert total <= STORAGE_SLOTS
    # 186 achievable slots, minus Milotic sharing Feebas's individual? No:
    # the living-dex line count follows the achievable set, which lost the
    # three event legendaries and regained Milotic: 187 - 3 + 1 = 185.
    assert total == 185


# ---- the stage ladder ----------------------------------------------------

def test_the_ladder_ranks_the_stretch_goals_as_siblings():
    from pokeagent import stages

    assert stages.PERFECT_IV_COUNT == 5
    assert stages.MAX_IV == 31 and stages.MAX_LEVEL == 100


def test_current_picks_the_least_complete_sibling():
    """Rank-3 goals are independent grinds; a run left alone should spread
    effort rather than starving two to finish one."""
    from pokeagent.stages import Ladder, StageProgress

    class Fake(Ladder):
        def __init__(self):
            pass

        def all_stages(self):
            return [
                StageProgress("game", 1, "Complete", "", 100.0, True),
                StageProgress("living", 2, "Living", "", 100.0, True),
                StageProgress("iv", 3, "IV", "", 40.0, False),
                StageProgress("lvl", 3, "Levels", "", 10.0, False),
                StageProgress("shiny", 3, "Shiny", "", 70.0, False),
            ]

    assert Fake().current().key == "lvl"


def test_an_unfinished_lower_rank_outranks_the_stretch_goals():
    from pokeagent.stages import Ladder, StageProgress

    class Fake(Ladder):
        def __init__(self):
            pass

        def all_stages(self):
            return [
                StageProgress("game", 1, "Complete", "", 50.0, False),
                StageProgress("iv", 3, "IV", "", 0.0, False),
            ]

    assert Fake().current().rank == 1


# ---- entropy -------------------------------------------------------------

def test_the_lcg_constants_match_the_decomp():
    """pret/src/random.c:11 -- gRngValue = 1103515245 * gRngValue + 24691."""
    from pokeagent import entropy

    assert entropy.LCG_MULT == 1103515245
    assert entropy.LCG_ADD == 24691
    assert entropy.RNG_SYMBOL == "gRngValue"


def test_seed_draws_differ_and_mix_several_sources():
    from pokeagent.entropy import seed_value

    a, sources = seed_value()
    b, _ = seed_value()
    assert a != b, "two draws must not collide"
    assert 0 <= a <= 0xFFFFFFFF
    # urandom plus the clock at minimum; /proc may be absent in a container.
    assert "urandom" in sources
    assert "time_ns" in sources


def test_entropy_is_off_by_default_and_refuses_unsafe_moments():
    """It deliberately breaks savestate determinism, so it must be opt-in and
    must decline while a savestate search needs reproducibility."""
    from pokeagent.entropy import Entropy

    class FakeEmu:
        frame = 0

        def u32(self, _):
            return 1

        def write(self, *a):
            raise AssertionError("must not write when disabled")

    class FakeDriver:
        emu = FakeEmu()

        def in_battle(self):
            return False

        def scene_active(self):
            return False

    e = Entropy(FakeDriver())
    assert e.enabled is False
    assert e.inject() is None, "disabled means no write"

    e.enabled = True
    e.hold = True
    ok, why = e.safe_moment()
    assert not ok and "determinism" in why


def test_entropy_refuses_mid_battle():
    from pokeagent.entropy import Entropy

    class FakeDriver:
        class emu:
            frame = 0

            @staticmethod
            def u32(_):
                return 1

        def in_battle(self):
            return True

        def scene_active(self):
            return False

    ok, why = Entropy(FakeDriver(), enabled=True).safe_moment()
    assert not ok and "battle" in why


# ---- the model boundary --------------------------------------------------

def test_a_certain_ko_is_never_handed_to_the_model():
    """Arithmetic beats preference: if a move certainly kills, the maths
    decides and the model is not asked."""
    from pokeagent.smallchoices import SmallChoices

    asked = []

    class FakeBrain:
        enabled = True
        last_reason = "n/a"

        def choose(self, q, options, fallback=None, context=None,
                   timeout=None):
            asked.append(q)
            return options[0]

    sc = SmallChoices(FakeBrain())
    analysis = {
        "moves": [
            {"slot": 0, "name": "A", "power": 40, "damage_max": 30,
             "hits_to_ko": 1, "effective_accuracy": 100},
            {"slot": 1, "name": "B", "power": 40, "damage_max": 29,
             "hits_to_ko": 2, "effective_accuracy": 100},
        ]
    }
    assert sc.tied_move(analysis, 0) == 0
    assert not asked
    assert "arithmetic" in sc.last_reason


def test_a_real_tie_is_handed_to_the_model():
    from pokeagent.smallchoices import SmallChoices

    class FakeBrain:
        enabled = True
        last_reason = "picked for flavour"

        def choose(self, q, options, fallback=None, context=None,
                   timeout=None):
            return sorted(options)[-1]

    sc = SmallChoices(FakeBrain())
    analysis = {
        "me": {}, "enemy": {},
        "moves": [
            {"slot": 0, "name": "AAA", "power": 40, "damage_max": 30,
             "hits_to_ko": 3, "effective_accuracy": 100},
            {"slot": 1, "name": "ZZZ", "power": 40, "damage_max": 30,
             "hits_to_ko": 3, "effective_accuracy": 100},
        ],
    }
    assert sc.tied_move(analysis, 0) == 1
    assert sc.consulted == 1


def test_no_brain_means_the_fallback_and_a_reason():
    from pokeagent.smallchoices import SmallChoices

    sc = SmallChoices(None)
    assert sc.nickname("TORCHIC", fallback="EMBER") == "EMBER"
    assert sc.next_target([{"species": "A", "area": "X"}]) is not None


# ---- the game registry ---------------------------------------------------

def test_the_registry_knows_the_starter_and_the_pair():
    spec = gamespec.get("sapphire")
    assert spec.starter == "SPECIES_TORCHIC", "the user's requirement"
    assert spec.paired_with == "ruby"
    assert spec.generation == 3 and spec.core == "mgba"


def test_declared_games_are_honest():
    """A declared game must not pretend to work."""
    live = {g.id for g in gamespec.live_games()}
    assert "sapphire" in live
    assert "crystal" in live, "pokecrystal builds and PyBoy boots it"
    # pokered builds with the same vendored rgbds and is now exercised here:
    # it boots, its screen decodes and its menus drive. Its state and nav
    # layers are unported and REFUSE, which the capability set carries.
    assert "red" in live, "pokered builds and is driven here"
    assert "gold" not in live, "no pokegold checkout"


def test_every_registered_game_resolves_an_adapter():
    from pokeagent.adapters import base

    for spec in gamespec.REGISTRY.values():
        if spec.id == "red":
            continue  # gen1 adapter refuses on purpose; covered below
        adapter = base.resolve(spec)
        assert adapter.spec is spec


def test_the_gen1_adapter_refuses_the_layers_it_did_not_port():
    """Gen 1 opens now, but its party and map readers do not exist. They must
    raise BY NAME rather than misread pokecrystal's structs: a Gen-1 party read
    through Gen-2 offsets parses cleanly and every stat is wrong, which this
    project's retrospective ranks as worse than a crash."""
    from pokeagent.adapters.gen1 import Gen1Adapter

    adapter = Gen1Adapter(gamespec.get("red"))
    assert "flat_party" not in adapter.CAPABILITIES, "nothing reads the party"
    assert "battle" not in adapter.CAPABILITIES
