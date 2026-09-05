"""Team composition policy: coverage, parity, catch ranking, training policy.

Every number asserted here is derived from the cartridge at runtime -- the type
chart from ``gTypeEffectiveness``, species types from ``gBaseStats``, movesets
from ``gLevelUpLearnsets``. Where a test asserts a concrete multiplier the
comment cites the row of data/type_effectiveness.inc it comes from, so a
failure says whether the harness or the expectation is wrong.

The parity tests exist because of one specific defect: an egg reads 0 HP and 0
level (pokemon.py:96-98), and the predecessor project counted one as a party
member. That made the party minimum 0, every real member a laggard, and the
heal rail loop forever. There are tests here for exactly that shape.
"""

import pytest

from pokeagent.pokemon import Mon
from pokeagent.team import DEFAULT_TOLERANCE, Member, Team

pytestmark = pytest.mark.unit


# -- fixtures ---------------------------------------------------------------

@pytest.fixture(scope="module")
def team(names, consts):
    return Team(names, consts)


@pytest.fixture(scope="module")
def sp(consts):
    return consts.species


@pytest.fixture(scope="module")
def mvs(consts):
    return consts.moves


def mon(species, level=5, *, moves=(), hp=None, max_hp=None, nickname="",
        egg=False):
    """A party ``Mon`` with only the fields the team policy reads.

    Built by hand rather than read from a savestate: composition math must be
    testable without booting a timeline, the same reason
    :class:`pokeagent.tactics.Combatant` can be constructed by hand.
    """
    full = max_hp if max_hp is not None else 20 + level
    return Mon(
        personality=0x1234, ot_id=0x5678, nickname=nickname, ot_name="TESTER",
        language=2, is_bad_egg=False, is_egg=egg, checksum_ok=True,
        species=0 if egg else species, moves=tuple(moves),
        pp=(35,) * len(moves), level=level,
        hp=full if hp is None else hp, max_hp=full, status=0,
    )


def egg():
    """An egg exactly as the engine presents one: the egg flag set, species
    unreadable, 0 level and 0 HP."""
    return Mon(
        personality=1, ot_id=1, nickname="EGG", ot_name="TESTER", language=2,
        is_bad_egg=False, is_egg=True, checksum_ok=True, species=0,
        moves=(), pp=(), level=0, hp=0, max_hp=0, status=0,
    )


def frame(party_rows, *, active_index=0, enemy_level=5, turn=0,
          can_switch=None, me_hp=None, me_max_hp=None):
    """A battle frame with the keys BattleSession.frame() actually produces
    (battle.py:343-399), so the policy is exercised against the real shape."""
    me = next(r for r in party_rows if r["index"] == active_index)
    return {
        "active": True,
        "me": {
            "species": me["species"], "nickname": me["nickname"],
            "level": me["level"],
            "hp": me_hp if me_hp is not None else me["hp"],
            "max_hp": me_max_hp if me_max_hp is not None else me["max_hp"],
            "types": [], "status": [], "stat_stages": [],
            "ability": "-", "party_index": active_index, "moves": [],
        },
        "enemy": {
            "species": "POOCHYENA", "nickname": "", "level": enemy_level,
            "hp": 14, "max_hp": 14, "types": [], "status": [],
            "stat_stages": [], "ability": "-", "party_index": None,
            "moves": [],
        },
        "party": party_rows,
        "bag": {},
        "turn": turn,
        "wild": True,
        "can_switch": (
            can_switch if can_switch is not None
            else [r["index"] for r in party_rows
                  if not r["egg"] and r["hp"] and r["index"] != active_index]
        ),
        "moves": [],
        "outcome": None,
        "menu": "action",
    }


def row(index, nickname, species, level, hp, max_hp, *, egg=False,
        active=False):
    return {
        "index": index, "nickname": nickname, "species": species,
        "level": level, "hp": hp, "max_hp": max_hp, "status": None,
        "egg": egg, "active": active,
    }


# -- the type vocabulary ----------------------------------------------------

def test_type_ids_exclude_the_table_separator(team, consts):
    """TYPE_MYSTERY (include/constants/pokemon.h:110) is the separator that
    introduces the Foresight rows of gTypeEffectiveness, not a type. Counting
    it would put an unreachable 18th type in every denominator."""
    ids = team.type_ids
    assert consts.ns("pokemon.h")["TYPE_MYSTERY"] not in ids
    assert len(ids) == 17, "Gen 3 has seventeen real types"
    assert team.type_name(consts.ns("pokemon.h")["TYPE_WATER"]) == "WATER"


def test_the_super_effective_threshold_comes_from_the_header(team):
    """include/battle.h:64-67 spells TYPE_MUL_SUPER_EFFECTIVE as 20 against a
    TYPE_MUL_NORMAL of 10; nothing here is 2.0 written down. "Resists" stays
    ``< 1.0`` on purpose, so a x0.25 dual resist and a x0 immunity both count.
    """
    assert team.super_effective == 2.0


def test_water_and_flying_cover_six_types(team, sp, mvs):
    """A MUDKIP with WATER GUN plus a TAILLOW with PECK.

    WATER->FIRE/GROUND/ROCK and FLYING->FIGHTING/BUG/GRASS are all x2
    (data/type_effectiveness.inc), so exactly those six types are covered and
    nothing else is.
    """
    party = [
        mon(sp["SPECIES_MUDKIP"], 10,
            moves=(mvs["MOVE_TACKLE"], mvs["MOVE_WATER_GUN"]), nickname="SWAMPY"),
        mon(sp["SPECIES_TAILLOW"], 10, moves=(mvs["MOVE_PECK"],), nickname="SWIFT"),
    ]
    cov = team.coverage(party)
    assert set(cov.covered) == {"FIRE", "GROUND", "ROCK", "FIGHT", "BUG", "GRASS"}
    assert "WATER" in cov.offense_gaps, "WATER GUN is neutral into WATER (x0.5)"
    assert "STEEL" in cov.offense_gaps


def test_a_normal_only_party_cannot_touch_ghost(team, sp, mvs):
    """NORMAL->GHOST is x0 (data/type_effectiveness.inc), so a TACKLE-only
    party has an offensive gap AND a type it literally cannot damage. Those
    are different facts and the report keeps them apart."""
    party = [mon(sp["SPECIES_MUDKIP"], 5,
                 moves=(mvs["MOVE_TACKLE"], mvs["MOVE_GROWL"]), nickname="SWAMPY")]
    cov = team.coverage(party)
    assert cov.covered == (), "NORMAL is super-effective against nothing"
    assert cov.no_effect == ("GHOST",)
    ghost = next(f for f in cov.types if f.type_name == "GHOST")
    assert ghost.best_offense == 0.0
    assert "can damage GHOST at all" in ghost.why


def test_a_status_only_party_has_undefined_offense_not_zero(team, sp, mvs):
    """GROWL has 0 power, so it is not "NORMAL coverage" and it is not an
    immunity either. ``best_offense`` stays None and the reason says so --
    reporting x0 would claim the party is walled by everything."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_GROWL"],))]
    cov = team.coverage(party)
    assert cov.covered == ()
    assert cov.no_effect == (), "no move means no immunity claim"
    assert all(f.best_offense is None for f in cov.types)
    assert "no damaging move at all" in cov.types[0].why


# -- defensive holes --------------------------------------------------------

def test_a_lone_water_is_open_to_grass_and_electric(team, sp, mvs):
    """GRASS->WATER and ELECTRIC->WATER are both x2 and a lone WATER resists
    neither, so both are holes. ICE->WATER is x0.5, so ICE is not."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    cov = team.coverage(party)
    assert set(cov.defense_holes) == {"GRASS", "ELECTR"}
    assert "ICE" not in cov.defense_holes


def test_a_flying_partner_closes_the_grass_hole_but_not_electric(team, sp, mvs):
    """GRASS->FLYING is x0.5, so TAILLOW resists the incoming GRASS the WATER
    starter is weak to and the hole closes. ELECTRIC->FLYING is x2, so adding
    TAILLOW makes the ELECTRIC hole worse, not better -- and ROCK->FLYING x2
    opens a new one, because nothing on the team resists ROCK."""
    party = [
        mon(sp["SPECIES_MUDKIP"], 10, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        mon(sp["SPECIES_TAILLOW"], 10, moves=(mvs["MOVE_PECK"],), nickname="SWIFT"),
    ]
    cov = team.coverage(party)
    assert "GRASS" not in cov.defense_holes
    assert "ELECTR" in cov.defense_holes
    assert "ROCK" in cov.defense_holes
    assert "ICE" not in cov.defense_holes, "SWAMPY resists ICE at x0.5"
    grass = next(f for f in cov.types if f.type_name == "GRASS")
    assert "SWIFT" in grass.resisted_by


def test_gaps_are_exposed_as_type_ids_for_the_catch_planner(team, sp, mvs):
    """The catch planner consumes types, not species, so the gap list has to
    come out as ids it can feed straight back into the chart."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    cov = team.coverage(party)
    holes = cov.hole_type_ids()
    assert all(isinstance(t, int) for t in holes)
    assert {team.type_name(t) for t in holes} == {"GRASS", "ELECTR"}
    assert len(cov.gap_type_ids()) == 17
    assert all(f.why for f in team.gaps(party))
    # The ids round-trip through the ROM chart and back through fact().
    for tid in holes:
        assert team.multiplier(tid, (team.names.base_stats(
            sp["SPECIES_MUDKIP"]).type1,)) == 2.0
        assert cov.fact(tid).defense_hole is True
    assert cov.fact(-1) is None


# -- roundness --------------------------------------------------------------

def test_roundness_is_the_mean_of_the_two_stated_fractions(team, sp, mvs):
    """The score is defined in Coverage's docstring; this pins the arithmetic
    so nobody can quietly reweight it."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    cov = team.coverage(party)
    n = len(team.type_ids)
    assert cov.offense_fraction == len(cov.covered) / n
    assert cov.defense_fraction == 1 - len(cov.defense_holes) / n
    assert cov.roundness == round(
        100 * (cov.offense_fraction + cov.defense_fraction) / 2, 1
    )
    # 0 of 17 covered, 2 of 17 open: mean(0, 15/17) x 100.
    assert cov.roundness == 44.1


def test_roundness_rises_when_a_partner_fills_gaps(team, sp, mvs):
    solo = [mon(sp["SPECIES_MUDKIP"], 10, moves=(mvs["MOVE_WATER_GUN"],))]
    pair = solo + [mon(sp["SPECIES_TAILLOW"], 10, moves=(mvs["MOVE_PECK"],))]
    assert team.roundness(pair) > team.roundness(solo)


def test_an_empty_party_reports_zero_with_a_reason(team):
    cov = team.coverage([])
    assert cov.roundness == 0.0
    assert "no battle-capable members" in cov.why


def test_eggs_are_not_battle_capable_members(team, sp, mvs):
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],)), egg()]
    cov = team.coverage(party)
    assert len(cov.members) == 1


# -- movesets ---------------------------------------------------------------

def test_wild_moveset_matches_the_engines_own_result(team, sp):
    """``GiveBoxMonInitialMoveset`` (src/pokemon_1.c:1915-1935) is reproduced
    exactly, not approximated. Cross-check: the real L5 MUDKIP starter in
    saves/lab.state has precisely TACKLE and GROWL, which is what this
    returns."""
    moves = team.wild_moveset(sp["SPECIES_MUDKIP"], 5)
    assert [team.names.move(m) for m in moves] == ["TACKLE", "GROWL"]


def test_wild_moveset_never_exceeds_the_four_slots(team, sp):
    """The engine shifts slot 0 out when full (ibid.:1934), so a level-100
    learnset must still come back as four moves."""
    moves = team.wild_moveset(sp["SPECIES_MUDKIP"], 100)
    assert len(moves) == 4
    assert len(set(moves)) == 4, "GiveMoveToBoxMon refuses duplicates"


# -- catch ranking ----------------------------------------------------------

def test_recommend_catch_prefers_the_gap_filler_over_the_redundant_one(
    team, sp, mvs
):
    """Party is a lone WATER starter with TACKLE, so its holes are GRASS and
    ELECTRIC and it covers nothing.

    SHROOMISH is GRASS: it resists both open holes (GRASS->GRASS x0.5,
    ELECTRIC->GRASS x0.5) and its ABSORB fills the WATER/GROUND/ROCK gaps.
    WINGULL is WATER/FLYING: its WATER GUN fills the same kind of gap but it
    duplicates the WATER the party already fields and resists neither hole.
    """
    party = [mon(sp["SPECIES_MUDKIP"], 5,
                 moves=(mvs["MOVE_TACKLE"],), nickname="SWAMPY")]
    ranked = team.recommend_catch(
        [(sp["SPECIES_SHROOMISH"], 5), (sp["SPECIES_WINGULL"], 5)], party
    )
    order = [r.species_name for r in ranked]
    assert order == ["SHROOMISH", "WINGULL"]

    shroomish, wingull = ranked
    assert set(shroomish.defense_fill) == {"GRASS", "ELECTR"}
    assert wingull.defense_fill == ()
    assert wingull.redundant_types == ("WATER",)
    assert "duplicates WATER" in wingull.why
    assert shroomish.score > wingull.score


def test_recommend_catch_credits_only_the_moves_the_catch_will_have(team, sp, mvs):
    """ELECTRIKE is the counter-example that justifies reading the learnset:
    ELECTRIC would answer the party's open ELECTRIC weakness, but a wild
    ELECTRIKE's four moves at L12 contain no ELECTRIC attack at all, so it
    gets potential credit and not present credit."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    ranked = team.recommend_catch([(sp["SPECIES_ELECTRIKE"], 12)], party)
    rec = ranked[0]
    assert rec.offense_now == (), "no damaging ELECTRIC move at L12"
    assert "WATER" in rec.offense_potential
    assert rec.defense_fill == ("ELECTR",)


def test_recommend_catch_charges_for_being_underlevelled(team, sp, mvs):
    """The objective is a level FLOOR, so a catch far below the party owes
    training and the score says so."""
    party = [mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],))]
    ranked = team.recommend_catch(
        [{"species": sp["SPECIES_SHROOMISH"], "level": 5},
         {"species": sp["SPECIES_SHROOMISH"], "level": 20}], party
    )
    by_level = {r.level: r for r in ranked}
    assert by_level[5].parity_cost == 15
    assert by_level[20].parity_cost == 0
    assert by_level[20].score > by_level[5].score
    assert "owes 15 level(s)" in by_level[5].why


def test_recommend_catch_takes_the_top_of_a_level_range(team, sp, mvs):
    """The vendored dex dataset gives encounter level RANGES; the top of the
    range is the strongest thing actually catchable there."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    rec = team.recommend_catch(
        [{"species": sp["SPECIES_SHROOMISH"], "levels": (5, 9)}], party
    )[0]
    assert rec.level == 9


def test_recommend_catch_refuses_a_candidate_with_no_level(team, sp, mvs):
    """A bare species id has no moveset, and inventing a level would silently
    change the ranking."""
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    with pytest.raises(ValueError, match="level"):
        team.recommend_catch([sp["SPECIES_SHROOMISH"]], party)


def test_recommend_catch_explains_an_empty_result(team, sp, mvs):
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    assert team.recommend_catch([], party) == []
    assert team.last_catch_reason == "no candidates were supplied"


# -- parity -----------------------------------------------------------------

def test_parity_reports_the_spread_over_the_fighters(team, sp, mvs):
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        mon(sp["SPECIES_ZIGZAGOON"], 8, moves=(mvs["MOVE_TACKLE"],), nickname="ZIGGY"),
        mon(sp["SPECIES_TAILLOW"], 17, moves=(mvs["MOVE_PECK"],), nickname="SWIFT"),
    ]
    par = team.parity(party)
    assert (par["min"], par["max"], par["spread"]) == (8, 20, 12)
    assert par["mean"] == 15.0
    assert par["count"] == 3
    assert [l["nickname"] for l in par["laggards"]] == ["ZIGGY"]
    # The gap is measured against the MEDIAN (17), not the max (20). Using the
    # max let one over-levelled starter mark the whole team as laggards, which
    # never terminated -- see test_training_terminates_with_one_runaway.
    assert par["laggards"][0]["gap"] == 9


def test_an_egg_is_not_a_laggard(team, sp, mvs):
    """The defect this whole file exists to guard: an egg reads 0 HP and 0
    level, so counting it drops the party minimum to 0, makes every real
    member a laggard, and (in the predecessor project) looped the heal rail
    forever."""
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        egg(),
    ]
    par = team.parity(party)
    assert par["min"] == 20 and par["max"] == 20 and par["spread"] == 0
    assert par["laggards"] == []
    assert par["count"] == 1
    assert par["eggs"] == 1
    assert "1 egg(s) excluded" in par["why"]
    assert team.needs_training(party) == []


def test_a_party_of_only_eggs_reports_a_reason(team):
    par = team.parity([egg(), egg()])
    assert par["min"] is None and par["spread"] is None
    assert par["eggs"] == 2
    assert "no non-egg party members" in team.last_parity_reason


def test_a_fainted_member_is_still_counted_for_parity(team, sp, mvs):
    """A fainted mon has a level and needs the same floor; it is only ineligible
    to be switched in, which is a separate question the policy asks."""
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],)),
        mon(sp["SPECIES_ZIGZAGOON"], 9, moves=(mvs["MOVE_TACKLE"],), hp=0,
            nickname="ZIGGY"),
    ]
    par = team.parity(party)
    assert par["min"] == 9
    assert [l["nickname"] for l in par["laggards"]] == ["ZIGGY"]
    assert par["laggards"][0]["alive"] is False


def test_needs_training_is_a_band_not_an_equality(team, sp, mvs):
    """"Around the same level" tolerates DEFAULT_TOLERANCE levels; the member
    exactly at the edge is inside the band, the next one down is not."""
    lead = mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],))
    edge = mon(sp["SPECIES_ZIGZAGOON"], 20 - DEFAULT_TOLERANCE,
               moves=(mvs["MOVE_TACKLE"],), nickname="EDGE")
    over = mon(sp["SPECIES_ZIGZAGOON"], 20 - DEFAULT_TOLERANCE - 1,
               moves=(mvs["MOVE_TACKLE"],), nickname="OVER")
    assert team.needs_training([lead, edge]) == []
    # The band is measured against the MEDIAN, so the party needs enough mons
    # at the top for the median to sit there. With three at L20 the median is
    # 20: EDGE at 17 is exactly on the edge and inside, OVER at 16 is not.
    high = [mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],),
                nickname=f"HIGH{i}") for i in range(3)]
    assert [l["nickname"] for l in team.needs_training(high + [edge])] == []
    assert [l["nickname"] for l in
            team.needs_training(high + [edge, over])] == ["OVER"]
    assert team.needs_training([lead, edge], tolerance=1)[0]["nickname"] == "EDGE"


def test_laggards_come_out_furthest_behind_first(team, sp, mvs):
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],)),
        mon(sp["SPECIES_ZIGZAGOON"], 12, moves=(mvs["MOVE_TACKLE"],), nickname="MID"),
        mon(sp["SPECIES_TAILLOW"], 6, moves=(mvs["MOVE_PECK"],), nickname="LAST"),
    ]
    # Median of [6, 12, 20] is 12: LAST is six under it, MID is exactly on it.
    assert [l["nickname"] for l in team.needs_training(party)] == ["LAST"]


def test_training_terminates_with_one_runaway(team, sp, mvs):
    """The bug this rule exists for.

    A starter is used more than anything else and drifts ahead. Measured
    against the party MAX, a party of 26/26/26/27/27/35 has five members nine
    levels behind, so every one of them is a laggard -- and training them
    raises the max too, so the list never empties. A run spent forty minutes
    grinding level-3 wilds on Route 102 with a level-26 party and a gym it
    could already beat two towns away.

    Against the median the same party is finished, which is the correct
    reading of "all around the same level": five mons ARE, and the sixth is
    ahead rather than behind. The runaway is handled by rotation instead --
    the promotion policy benches it, the others catch up, and the median
    rises on its own.
    """
    levels = [26, 26, 26, 27, 27, 35]
    species = [sp["SPECIES_ZIGZAGOON"]] * 5 + [sp["SPECIES_MUDKIP"]]
    party = [mon(s, lv, moves=(mvs["MOVE_TACKLE"],), nickname=f"M{i}")
             for i, (s, lv) in enumerate(zip(species, levels))]
    assert team.needs_training(party) == [], \
        "one runaway starter must not put the whole team in permanent training"


# -- normalisation ----------------------------------------------------------

def test_frame_party_rows_normalise_the_same_as_mons(team, sp, mvs):
    """The policy is handed battle-frame dicts, not Mon objects, so the two
    have to agree on levels, eggs and indexes."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45),
            row(1, "EGG", "EGG", 0, 0, 0, egg=True)]
    members = team.members(rows)
    assert [m.level for m in members] == [20, 0]
    assert [m.fights for m in members] == [True, False]
    assert members[0].species == sp["SPECIES_MUDKIP"]
    assert team.parity(rows)["min"] == 20


def test_members_is_idempotent(team, sp, mvs):
    party = [mon(sp["SPECIES_MUDKIP"], 5, moves=(mvs["MOVE_TACKLE"],))]
    once = team.members(party)
    assert team.members(once) == once
    assert all(isinstance(m, Member) for m in once)


def test_an_unknown_species_name_raises_rather_than_guessing(team):
    with pytest.raises(ValueError, match="not a species name"):
        team.members([row(0, "X", "NOTAPOKEMON", 5, 10, 10)])


def test_a_frame_move_row_without_an_id_says_where_to_get_one(team):
    """Frame move rows carry names, not ids. Silently skipping them would make
    a full moveset look empty and every coverage number wrong."""
    bad = row(0, "SWAMPY", "MUDKIP", 5, 20, 20)
    bad["moves"] = [{"name": "TACKLE", "pp": 35}]
    with pytest.raises(ValueError, match="state.party"):
        team.members([bad])


# -- the training policy ----------------------------------------------------

def test_the_policy_switches_to_a_safe_laggard(team, sp, mvs):
    """Exp is divided by the number of participants
    (src/battle_script_commands.c:3379-3396), so on turn 0 -- before the lead
    has acted -- the laggard becomes the sole participant and takes all of it.
    """
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 28, 28)]
    policy = team.training_policy(None)
    action = policy(frame(rows, enemy_level=6))
    assert action == ("switch", 1)
    assert "sole participant" in policy.last_why
    # Against the MEDIAN, not the max: the reason string has to quote the same
    # number the decision was made on, or the operator cannot check the work.
    assert "6.0 levels under" in policy.last_why
    assert "median" in policy.last_why


def test_the_policy_refuses_a_weak_laggard(team, sp, mvs):
    """A fainted laggard earns nothing and costs a Centre trip, so below the HP
    floor the policy declines and names the check that failed."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 2, 28)]
    policy = team.training_policy(None)
    assert policy(frame(rows, enemy_level=6)) is None
    assert "below the 50% floor" in policy.last_why
    assert "ZIGGY" in policy.last_why


def test_the_policy_refuses_a_laggard_the_enemy_outclasses(team):
    """The second half of "safe": a laggard four levels under the enemy loses
    the speed tie and the damage race at once."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 28, 28)]
    policy = team.training_policy(None)
    assert policy(frame(rows, enemy_level=20)) is None
    assert "levels under the L20" in policy.last_why


def test_the_policy_leaves_a_healthy_laggard_alone(team):
    """Already the sole participant: switching now would only split the exp."""
    rows = [row(0, "ZIGGY", "ZIGZAGOON", 8, 28, 28, active=True),
            row(1, "SWAMPY", "MUDKIP", 20, 45, 45)]
    policy = team.training_policy(None)
    assert policy(frame(rows, active_index=0, enemy_level=6)) is None
    assert "sole participant" in policy.last_why
    assert "keeps the KO" in policy.last_why


def test_the_policy_anchors_when_the_laggard_is_about_to_faint(team):
    rows = [row(0, "ZIGGY", "ZIGZAGOON", 8, 3, 28, active=True),
            row(1, "SWAMPY", "MUDKIP", 20, 45, 45)]
    policy = team.training_policy(None)
    action = policy(frame(rows, active_index=0, enemy_level=6))
    assert action == ("switch", 1)
    assert "anchoring on SWAMPY" in policy.last_why


def test_the_policy_notes_when_the_exp_will_already_be_split(team):
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 28, 28)]
    policy = team.training_policy(None)
    assert policy(frame(rows, enemy_level=6, turn=3)) == ("switch", 1)
    assert "split with the lead" in policy.last_why


def test_the_policy_never_treats_an_egg_as_a_laggard(team):
    """An egg on the bench reads level 0. Treating it as a laggard would make
    the policy try to switch to a slot the engine will not accept, forever."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "EGG", "EGG", 0, 0, 0, egg=True)]
    policy = team.training_policy(None)
    assert policy(frame(rows, enemy_level=6)) is None
    assert "no laggard" in policy.last_why


def test_the_policy_declines_with_a_reason_when_there_is_no_battle(team):
    policy = team.training_policy(None)
    assert policy({"active": False}) is None
    assert "no battle is active" in policy.last_why


def test_the_policy_only_offers_switchable_slots(team):
    """``can_switch`` is the engine's own answer (battle.py:433-456). A fainted
    laggard is a laggard for parity purposes but not a switch target."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 0, 28)]
    policy = team.training_policy(None)
    assert policy(frame(rows, enemy_level=6, can_switch=[])) is None
    assert "not switchable" in policy.last_why


def test_the_policy_logs_every_decision(team):
    """A battle policy nobody can audit is indistinguishable from a random
    one, so each call keeps its turn, action and reason."""
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 28, 28)]
    policy = team.training_policy(None)
    policy(frame(rows, enemy_level=6, turn=0))
    policy(frame(rows, enemy_level=20, turn=1))
    assert [d.action for d in policy.log] == [("switch", 1), None]
    assert [d.turn for d in policy.log] == [0, 1]
    assert all(d.why for d in policy.log)


def test_the_policy_falls_back_to_the_party_it_was_built_with(team, sp, mvs):
    """battle.py calls the policy with a frame; if that frame ever arrives
    without a party the policy must not silently decide there are no
    laggards."""
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        mon(sp["SPECIES_ZIGZAGOON"], 8, moves=(mvs["MOVE_TACKLE"],), nickname="ZIGGY"),
    ]
    policy = team.training_policy(party)
    rows = [row(0, "SWAMPY", "MUDKIP", 20, 45, 45, active=True),
            row(1, "ZIGGY", "ZIGZAGOON", 8, 28, 28)]
    bare = frame(rows, enemy_level=6)
    bare["party"] = []
    assert policy(bare) == ("switch", 1)


# -- rotation ---------------------------------------------------------------

def test_rotation_names_two_orders_rather_than_choosing_one(team, sp, mvs):
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        mon(sp["SPECIES_ZIGZAGOON"], 8, moves=(mvs["MOVE_TACKLE"],), nickname="ZIGGY"),
        mon(sp["SPECIES_TAILLOW"], 18, moves=(mvs["MOVE_PECK"],), nickname="SWIFT"),
    ]
    rot = team.rotation(party)
    assert rot["training"]["lead"] == 1, "the laggard leads for training"
    assert rot["gym"]["lead"] == 0, "the strongest leads for a gym"
    assert sorted(rot["training"]["order"]) == [0, 1, 2]
    assert rot["gym"]["order"] == [0, 2, 1]
    assert "participants" in rot["training"]["why"]
    assert "surviving" in rot["gym"]["why"]


def test_rotation_puts_eggs_and_fainted_members_last(team, sp, mvs):
    party = [
        egg(),
        mon(sp["SPECIES_ZIGZAGOON"], 8, moves=(mvs["MOVE_TACKLE"],), hp=0,
            nickname="ZIGGY"),
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
    ]
    rot = team.rotation(party)
    assert rot["gym"]["order"][0] == 2
    assert rot["gym"]["order"][-1] == 0, "the egg cannot fight"
    assert rot["training"]["order"][-1] == 0
    assert rot["eggs_last"] == [0]


# -- reporting --------------------------------------------------------------

def test_report_is_plain_text_with_the_numbers_that_matter(team, sp, mvs):
    party = [
        mon(sp["SPECIES_MUDKIP"], 20, moves=(mvs["MOVE_WATER_GUN"],), nickname="SWAMPY"),
        mon(sp["SPECIES_ZIGZAGOON"], 8, moves=(mvs["MOVE_TACKLE"],), nickname="ZIGGY"),
    ]
    text = team.report(party)
    assert "roundness" in text
    assert "defensive holes" in text
    assert "laggard ZIGGY" in text
    assert "rotation training=" in text
