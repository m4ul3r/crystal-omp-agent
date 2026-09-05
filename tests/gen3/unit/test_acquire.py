"""How to get every species -- read from the cartridge, not from a wiki.

The dex objective is only actionable if each species has a step attached, and
about a third of the Hoenn dex has no encounter data because you do not find
those, you make them. These pin the answers and, more importantly, the stride
that makes them readable at all.
"""

import pytest

from pokeagent import gamespec
from pokeagent.acquire import Acquisitions

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def acq(emu, names, consts, mapdata):
    from pokeagent import dex as dexmod

    target = dexmod.DexTarget(
        emu, names, consts, mapdata, spec=gamespec.get("sapphire")
    )
    return Acquisitions(emu, names, target), {e.name: e for e in target.entries}


def answer(acq, name):
    resolver, byname = acq
    entry = byname.get(name)
    assert entry is not None, f"{name} is not in the dex dataset"
    return resolver.for_entry(entry)


# ---- the stride, which is the whole ballgame -----------------------------

def test_the_evolution_stride_is_derived_not_transcribed(acq):
    """include/pokemon.h says struct Evolution is three u16s = 6 bytes, and
    the real array is padded to 8: gEvolutionTable is 16480 bytes over 412
    species = 40 each. Transcribing the 6 read every field from the wrong
    offset and answered "Shedinja: evolve REGIROCK". This project has been
    bitten by the identical mistake before, on struct BattleMove."""
    resolver, _ = acq
    per_species, entry = resolver._strides
    assert per_species % 5 == 0, "five evolutions per species"
    assert entry * 5 == per_species
    assert entry >= 6, "an entry cannot be smaller than its three u16s"


def test_a_plain_level_evolution_reads_correctly(acq):
    assert "level 36" in answer(acq, "Sceptile").summary()
    assert "GROVYLE" in answer(acq, "Sceptile").summary()


# ---- the thing the objective actually needed -----------------------------

def test_a_stone_evolution_names_the_stone(acq):
    """The point of reading the ROM: EVO_ITEM carries the item id in param, so
    the answer is "use LEAF STONE on GLOOM" rather than "evolve Gloom"."""
    got = answer(acq, "Vileplume").summary()
    assert "LEAF STONE" in got
    assert "GLOOM" in got


def test_a_branching_stone_line_keeps_both_branches(acq):
    """Gloom becomes Vileplume OR Bellossom depending on the stone, and a
    living dex needs both."""
    resolver, byname = acq
    branches = {
        target for _, _, target in resolver.evolutions_of(
            byname["Gloom"].species
        )
    }
    assert len(branches) == 2


def test_a_static_legendary_carries_its_real_prerequisite(acq):
    """The encounter table says "Desert Ruins". It does not say the door will
    not open without Relicanth, Wailord and a Braille puzzle, and a planner
    that walks there is going to be disappointed."""
    got = answer(acq, "Regirock")
    assert "Desert Ruins" in got.summary()
    assert "Sealed Chamber" in got.note


def test_rayquaza_mentions_the_bike(acq):
    assert "MACH BIKE" in answer(acq, "Rayquaza").note


def test_shedinja_explains_the_spare_slot(acq):
    """Nincada's evolution LEAVES Shedinja behind; it is not the evolution
    target, and it needs a free party slot and a spare ball at the moment."""
    got = answer(acq, "Shedinja").summary()
    assert "NINCADA" in got
    assert "spare ball" in got


def test_a_baby_is_bred_from_the_adult(acq):
    """Babies are not catchable and the evolution arrow points away from them,
    so the inverted table finds nothing -- the answer is the Day Care."""
    got = answer(acq, "Pichu")
    assert got.best().kind == "breed"
    assert "PIKACHU" in got.summary()


def test_a_trade_evolution_is_marked_not_solo(acq):
    got = answer(acq, "Alakazam")
    assert any(s.kind == "trade" for s in got.steps)
    assert got.best().kind == "trade"
    assert got.solo is False, "a solo cartridge cannot trade"


def test_a_wild_catch_outranks_making_one(acq):
    """Where both exist, catching is the cheaper plan."""
    got = answer(acq, "Silcoon")
    assert got.best().kind == "wild"


def test_every_achievable_species_has_a_method_except_the_event_ones(acq):
    """The honest completeness check. Deoxys is distribution-only and cannot
    be obtained on a normal cartridge, which is a fact and not a gap."""
    resolver, _ = acq
    unexplained = set(resolver.unexplained())
    assert unexplained <= {"Deoxys"}, f"no method for {sorted(unexplained)}"
