"""Value-assert the Pokedex-completion objective against its two sources.

Same doctrine as ``test_parser_values.py``: every number here is either the
decompilation's or the vendored dataset's, and the assertion cites where it
came from. That matters more than usual for this module, because a planner
that is confidently wrong sends a session on a two-hour walk to a route that
never held the species.

The live-state tests boot their own emulator on ``saves/lab.state`` rather
than loading a savestate into the session-scoped ``emu`` fixture, which other
tests read as a fresh power-on.
"""

import pytest

from pokeagent import gamespec as _gs

pytestmark = pytest.mark.unit

# The run's starter is GameSpec.starter (the user's requirement is Torchic).
# Deriving it here means changing the spec never breaks these tests.
STARTER_CONST = _gs.get("sapphire").starter
STARTER_NAME = STARTER_CONST.removeprefix("SPECIES_")
_LINES = {
    "TORCHIC": ["COMBUSKEN", "BLAZIKEN"],
    "MUDKIP": ["MARSHTOMP", "SWAMPERT"],
    "TREECKO": ["GROVYLE", "SCEPTILE"],
}
STARTER_EVOS = _LINES[STARTER_NAME]

from pokeagent import dex, paths  # noqa: E402


@pytest.fixture(scope="module")
def target(emu, names, consts, mapdata):
    return dex.DexTarget(emu, names, consts, mapdata, dex_id="sapphire",
                         paired_with="ruby")


@pytest.fixture(scope="module")
def lab(mapdata):
    """A private emulator sitting on ``saves/lab.state``.

    lab.state is the "player has control, party of one" checkpoint, so it is
    the earliest point at which the dex bitfields mean anything.
    """
    from pokeagent.cconst import Constants
    from pokeagent.charmap import Charmap
    from pokeagent.emu import Sapphire
    from pokeagent.names import Names
    from pokeagent.state import GameState
    from pokeagent.symbols import Symbols

    state_path = paths.SAVES_DIR / "lab.state"
    if not state_path.exists():
        pytest.skip(f"{state_path} is absent")

    sym, charmap, consts = Symbols(), Charmap(), Constants()
    emu = Sapphire(state_path=state_path, sym=sym, charmap=charmap)
    names = Names(emu, charmap, consts)
    return (
        dex.DexTarget(emu, names, consts, mapdata, dex_id="sapphire",
                      paired_with="ruby"),
        GameState(emu, names, consts),
    )


# ---- the dataset -------------------------------------------------------------


def test_dataset_is_vendored_and_shaped():
    """data/dex/sapphire.json, from regional-dex-buddy (data/dex/SOURCE.txt).

    202 Hoenn entries, and the top-level exclusives block splits them into
    seven attainable here, seven trade-only, seven trade evolutions.
    """
    raw = dex.load_dataset("sapphire")
    assert raw["game"]["pairedVersion"] == "ruby"
    assert len(raw["dexes"][0]["entries"]) == 202
    exclusives = raw["exclusives"]
    assert len(exclusives["attainable"]) == 7
    assert len(exclusives["tradeOnly"]) == 7
    assert len(exclusives["tradeEvolutions"]) == 7
    assert "sapphire" in dex.dataset_games() and "ruby" in dex.dataset_games()


def test_achievable_set_is_186(target):
    """202 Hoenn entries minus 7 Ruby exclusives, 6 trade evolutions and the
    3 event-only legendaries.

    Two corrections over the dataset, both ROM-grounded:

    * Milotic is NOT a trade evolution on this cartridge. The dataset carries
      the Gen-5 mechanic (trade Feebas holding a Prism Scale); this ROM's own
      gEvolutionTable says EVO_BEAUTY -- an in-cartridge evolution. The ROM
      wins, so Milotic is achievable and the trade bucket holds 6, not 7.
    * Jirachi, Deoxys and Latios never spawn on a Sapphire cartridge without
      an external event: birch_pc.c:94-102 discounts the first two in the
      game's own completion rating, and the roamer is LATIAS
      (include/constants/species.h:1283), leaving Latios behind the Eon
      Ticket. Counting them made 100% structurally impossible.

    202 - 7 - 6 - 3 = 186.
    """
    assert len(target.entries) == 202
    assert len(target.achievable) == 186
    assert len(target.out_of_reach) == 16
    assert len(target.achievable) + len(target.out_of_reach) == 202


def test_out_of_reach_names_and_reasons(target):
    """Each dropped entry carries the reason, so the harness can say WHY."""
    buckets = target.out_of_reach_by_reason()
    assert set(buckets) == {
        dex.OUT_OF_REACH_VERSION,
        dex.OUT_OF_REACH_TRADE_EVOLUTION,
        dex.OUT_OF_REACH_EVENT,
    }
    assert [e.name for e in buckets[dex.OUT_OF_REACH_EVENT]] == [
        "Latios", "Jirachi", "Deoxys",
    ]
    version = buckets[dex.OUT_OF_REACH_VERSION]
    assert [e.name for e in version] == [
        "Seedot", "Nuzleaf", "Shiftry", "Mawile", "Zangoose", "Solrock",
        "Groudon",
    ]
    assert [e.dex for e in version] == \
        dex.load_dataset("sapphire")["exclusives"]["tradeOnly"]
    assert version[0].detail == \
        "exclusive to Ruby; Sapphire can only trade for it"

    trade = buckets[dex.OUT_OF_REACH_TRADE_EVOLUTION]
    # Milotic is deliberately absent: EVO_BEAUTY on this cartridge.
    assert [e.name for e in trade] == [
        "Alakazam", "Golem", "Machamp", "Huntail", "Gorebyss",
        "Kingdra",
    ]
    dataset_claims = dex.load_dataset("sapphire")["exclusives"]["tradeEvolutions"]
    # The dataset's list includes Milotic; the ROM overrides that one entry.
    assert [e.dex for e in trade] == [
        n for n in dataset_claims if target.by_dex[n].name != "Milotic"
    ]
    detail = {e.name: e.detail for e in trade}
    assert detail["Alakazam"] == "only evolves when Kadabra is traded"
    assert detail["Huntail"] == \
        "only evolves when Clamperl is traded while holding a Deep sea tooth"


def test_event_only_species_agree_with_the_engine(target):
    """src/birch_pc.c:94-102 discounts exactly Jirachi and Deoxys when it
    decides whether Birch calls the Hoenn dex complete, so 200 of 202 counts.

    They used to sit INSIDE the achievable set and be surfaced separately,
    which meant every percentage was computed against a target containing
    species this cartridge cannot produce. They are now excluded at the
    partition, with Latios beside them: the Sapphire roamer is LATIAS
    (include/constants/species.h:1283), so Latios exists only behind the Eon
    Ticket event.
    """
    events = target.out_of_reach_by_reason()[dex.OUT_OF_REACH_EVENT]
    assert [e.name for e in events] == ["Latios", "Jirachi", "Deoxys"]
    achievable_names = {e.name for e in target.achievable}
    assert not achievable_names & {"Latios", "Jirachi", "Deoxys"}
    jirachi = target.by_dex[201]
    assert jirachi.event_only
    assert {e.method for e in jirachi.encounters} <= dex.EVENT_METHODS
    assert target.by_dex[202].encounters == ()   # Deoxys has no location at all


def test_sixty_achievable_entries_have_no_catch_location(target):
    """The reason a dex planner needs evolution chains at all: a third of the
    reachable set is only ever obtained by evolving something else."""
    unlocated = [e for e in target.achievable if not e.encounters]
    assert len(unlocated) == 60
    # ...and every one of them is an evolution of something.
    assert all(target.evolutions.pre_evolutions(e.species) for e in unlocated
               if e.dex not in (54, 137, 155, 202))


def test_known_wild_location_from_the_dataset(target):
    """Spot-checks that the dataset survived vendoring intact."""
    treecko = target.by_dex[1]
    assert treecko.rom_name == "TREECKO"
    assert [(e.method, e.area) for e in treecko.encounters] == [
        ("gift", "Route 101")
    ]
    feebas = target.by_dex[140]
    assert [(e.method, e.area, e.min_level, e.max_level)
            for e in feebas.encounters] == [
        ("feebas-tile-fishing", "Route 119", 20, 25)
    ]
    lileep = target.by_dex[133]
    assert lileep.encounters[0].conditions == ("With the Root Fossil",)
    assert lileep.encounters[0].is_fossil


# ---- gEvolutionTable ---------------------------------------------------------


def test_evolution_table_stride_is_derived_not_declared(target):
    """AGENTS.md gotcha 12. ``struct Evolution`` is three u16s
    (include/pokemon.h:380-385), so it reads as six bytes -- but agbcc pads
    the row and the linked array is 40 bytes per species, 8 per entry.
    Transcribing 6 would shift every entry past the first.
    """
    evo = target.evolutions
    assert evo.row_stride == 40
    assert evo.entry_stride == 8
    assert evo.entry_stride != 6
    assert evo.row_stride == evo.entry_stride * evo.EVOS_PER_SPECIES
    assert evo.species_count == 412       # include/global.h:56


def test_evolution_values_from_the_rom(target, consts, names):
    """src/data/pokemon/evolution.h -- Torchic 16, Combusken 36."""
    evo = target.evolutions
    torchic = consts.species["SPECIES_TORCHIC"]
    combusken = consts.species["SPECIES_COMBUSKEN"]
    blaziken = consts.species["SPECIES_BLAZIKEN"]

    (first,) = evo.evolutions(torchic)
    assert (first.method_name, first.level, first.to_species) == \
        ("EVO_LEVEL", 16, combusken)
    (second,) = evo.evolutions(combusken)
    assert (second.method_name, second.level, second.to_species) == \
        ("EVO_LEVEL", 36, blaziken)

    (back,) = evo.pre_evolutions(blaziken)
    assert (back.from_species, back.level) == (combusken, 36)
    assert evo.roots(blaziken) == (torchic,)
    assert evo.chain(torchic) == (torchic, combusken, blaziken)
    assert evo.describe(second) == "raise COMBUSKEN to level 36"


def test_item_and_trade_evolution_methods(target, consts, names):
    """Gloom branches on two stones; Kadabra needs a second player."""
    evo = target.evolutions
    gloom = evo.evolutions(consts.species["SPECIES_GLOOM"])
    assert [(e.method_name, names.item(e.item), names.species(e.to_species))
            for e in gloom] == [
        ("EVO_ITEM", "LEAF STONE", "VILEPLUME"),
        ("EVO_ITEM", "SUN STONE", "BELLOSSOM"),
    ]
    assert evo.describe(gloom[0]) == "use LEAF STONE on GLOOM"

    (kadabra,) = evo.evolutions(consts.species["SPECIES_KADABRA"])
    assert kadabra.method_name == "EVO_TRADE"
    assert kadabra.needs_trade and kadabra.level is None and kadabra.item is None

    # Nincada's two rows share a level and are both EVO_LEVEL_* variants.
    nincada = evo.evolutions(consts.species["SPECIES_NINCADA"])
    assert sorted(e.method_name for e in nincada) == \
        ["EVO_LEVEL_NINJASK", "EVO_LEVEL_SHEDINJA"]
    assert {e.level for e in nincada} == {20}


def test_evo_method_constants_come_from_the_header(target):
    """include/pokemon.h:364-378, all fifteen."""
    methods = target.evolutions.methods
    assert len(methods) == 15
    assert methods["EVO_LEVEL"] == 0x0004
    assert methods["EVO_ITEM"] == 0x0007
    assert methods["EVO_BEAUTY"] == 0x000F


def test_national_and_hoenn_dex_translation(target, consts):
    """gSpeciesToNationalPokedexNum is indexed by ``species - 1``
    (src/pokemon_3.c:444). Getting that bias wrong shifts the whole dex by
    one, which is exactly the class of bug this test exists to catch.

    The Hoenn side doubles as a cross-check on the vendored dataset: every
    one of its 202 ``dex`` numbers must match gSpeciesToHoennPokedexNum.
    """
    evo = target.evolutions
    torchic = consts.species["SPECIES_TORCHIC"]
    assert evo.natdex(torchic) == 255
    assert evo.species_of_natdex(255) == torchic
    assert evo.hoenn_dex(torchic) == 4

    mismatched = [
        (e.dex, e.name, evo.hoenn_dex(e.species))
        for e in target.entries
        if e.species and evo.hoenn_dex(e.species) != e.dex
    ]
    assert mismatched == []


# ---- gWildMonHeaders --------------------------------------------------------


def test_wild_table_is_read_from_the_rom(target, mapdata):
    """98 headers including the 0xFF terminator (src/wild_encounter.c:262),
    every one naming a map the navigator knows."""
    wild = target.wild
    assert wild.header_size == 20        # 2x u8 + 4x pointer, 4-aligned
    assert wild.header_capacity == 98
    assert wild.unnamed_maps == []
    assert len(wild.species) == 112
    assert all(s.map_name in mapdata.index for s in wild.slots)


def test_route_101_encounter_table(target, consts, names):
    """The first grass in the game: Wurmple, Zigzagoon, Poochyena.

    Levels and slot rates come from the ROM, so this is what the engine will
    actually roll -- not what a wiki says.
    """
    slots = target.wild.for_map("Route101")
    assert [(names.species(s.species), s.slot, s.min_level, s.max_level,
             s.slot_chance) for s in slots[:2]] == [
        ("WURMPLE", 0, 2, 2, 20.0),
        ("ZIGZAGOON", 1, 2, 2, 20.0),
    ]
    assert {names.species(s.species) for s in slots} == \
        {"WURMPLE", "ZIGZAGOON", "POOCHYENA"}
    assert all(s.kind == "land" for s in slots)


def test_slot_chances_come_from_the_engine_header(target):
    """ENCOUNTER_CHANCE_* in src/data/wild_encounters.h is cumulative, and
    ChooseWildMonIndex_* compares against exactly those bounds
    (src/wild_encounter.c:144-233). The rod split is read out of the macro
    names rather than retyped.
    """
    chances, rods = dex._encounter_chances()
    assert chances["land"] == (20.0, 20.0, 10.0, 10.0, 10.0, 10.0,
                               5.0, 5.0, 4.0, 4.0, 1.0, 1.0)
    assert sum(chances["land"]) == pytest.approx(100.0)
    assert chances["water"] == (60.0, 30.0, 5.0, 4.0, 1.0)
    assert rods["old_rod"] == (0, 1)
    assert rods["good_rod"] == (2, 3, 4)
    assert rods["super_rod"] == (5, 6, 7, 8, 9)


def test_fishing_and_diving_kinds_are_player_actions(target, consts, names):
    """A slot's kind is what the player must do, not the table's four
    physical arrays: which rod you hold decides which slots exist at all,
    and water on an Underwater map is Dive, not Surf."""
    kinds = {s.kind for s in target.wild.slots}
    assert {"old_rod", "good_rod", "super_rod", "dive"} <= kinds
    assert "fishing" not in kinds

    relicanth = target.wild.for_species(consts.species["SPECIES_RELICANTH"])
    assert {s.kind for s in relicanth} == {"dive"}
    assert {s.map_name for s in relicanth} == {"Underwater1", "Underwater2"}

    # Snorunt is only in the ice room -- the fact that pinned the dataset's
    # "Shoal Cave (B3F)" label to a real map.
    snorunt = target.wild.for_species(consts.species["SPECIES_SNORUNT"])
    assert {s.map_name for s in snorunt} == {"ShoalCave_LowTideIceRoom"}


# ---- dataset areas -> map names ---------------------------------------------


def test_area_to_map_resolves_the_ordinary_cases(target, mapdata):
    assert target.area_to_map("hoenn-route-101-area").maps == ("Route101",)
    assert target.area_to_map("Route 101").maps == ("Route101",)
    assert target.area_to_map("petalburg-woods-area").maps == ("PetalburgWoods",)
    # "Mount Pyre" in the dataset, "MtPyre" in the decomp.
    assert target.area_to_map("mt-pyre-outside").maps == ("MtPyre_Exterior",)
    assert target.area_to_map("mt-pyre-summit").maps == ("MtPyre_Summit",)
    # A floor parenthesis only the display label carries.
    assert target.area_to_map("cave-of-origin-b4f").maps == ("CaveOfOrigin_B4F",)
    assert target.area_to_map("hoenn-victory-road-b2f").maps == ("VictoryRoad_B2F",)
    for slug in ("hoenn-route-101-area", "mt-pyre-summit", "granite-cave-b1f"):
        assert target.area_to_map(slug).exact


def test_underwater_areas_come_from_the_maps_own_connections(target):
    """Every ``Underwater*`` map declares an ``emerge`` connection naming the
    surface route above it, so "Route 124 (underwater)" needs no guessing."""
    assert target.atlas.underwater_by_route["Route124"] == "Underwater1"
    assert target.atlas.underwater_by_route["Route126"] == "Underwater2"
    assert target.area_to_map("hoenn-route-124-underwater").maps == ("Underwater1",)
    assert target.area_to_map("hoenn-route-126-underwater").maps == ("Underwater2",)


def test_unmappable_areas_are_reported_not_guessed(target, mapdata):
    """93 of the dataset's 104 areas pin to exactly one map. The other 11 say
    why, and two of them genuinely have no map at all."""
    slugs = {e.area_slug for x in target.entries for e in x.encounters}
    assert len(slugs) == 104

    inexact = target.unmapped_areas()
    assert len(inexact) == 11
    assert len(slugs) - len(inexact) == 93

    mapless = [a for a in inexact if not a.maps]
    assert {a.slug for a in mapless} == {
        "roaming-hoenn-area", "hoenn-pokecenter-area",
    }
    assert all(a.reason for a in inexact)
    assert "roamer" in target.area_to_map("roaming-hoenn-area").reason

    # A grouped answer is still a real answer: every name is navigable.
    for area in inexact:
        for name in area.maps:
            assert name in mapdata.index


def test_a_shared_label_reports_the_collision(target):
    """Four dataset areas are all called "Shoal Cave", so resolving the
    label rather than a slug must not silently pick one."""
    resolved = target.area_to_map("Shoal Cave")
    assert not resolved.exact
    assert "shared by 4 dataset areas" in resolved.reason
    assert len(resolved.maps) == 7
    assert all(m.startswith("ShoalCave_") for m in resolved.maps)


def test_a_bad_override_fails_loudly(target, monkeypatch):
    """A stale override must blow up here, not produce a plan step naming a
    map the navigator has never heard of."""
    monkeypatch.setitem(dex.AREA_OVERRIDES, "bogus-area", (("NoSuchMap",), ""))
    with pytest.raises(KeyError, match="NoSuchMap"):
        target.atlas.area_to_map("bogus-area")


# ---- the live Pokedex bitfields ---------------------------------------------


def test_dex_flag_width_is_cross_checked(target, cstruct):
    """DEX_FLAGS_NO is defined with a ternary (include/global.h:666) that no
    expression parser here evaluates, so it is computed from
    POKEMON_SLOTS_NUMBER and checked against the parsed struct's own gap.
    """
    pokedex = cstruct.layout("Pokedex")
    assert pokedex["owned"] == 0x10          # include/global.h:775
    assert pokedex["seen"] == 0x44           # include/global.h:776
    assert target.dex_flag_bytes == 52       # ceil(412 / 8)
    assert target.dex_flag_bytes == pokedex["seen"] - pokedex["owned"]
    assert cstruct.layout("SaveBlock2")["pokedex"] == 0x18


def test_progress_on_lab_state(lab):
    """src/pokedex.c:3986-3993 -- the bitfields are indexed by
    ``nationalDexNo - 1``. lab.state is one Mudkip caught and two seen
    (Mudkip plus the Poochyena from Birch's bag), so an off-by-one in either
    direction shows up immediately.
    """
    target, state = lab
    progress = target.progress(state)
    # 186 achievable minus the six species locked by the starter choice --
    # a Mudkip in hand means the Treecko and Torchic lines are gone, and a
    # target that still counts them can never reach 100% -- and minus TWO
    # more for Route 111's fossils. That choice is still OPEN on this save,
    # but only one of the two lines can ever be registered: taking either
    # fossil sets both hide flags and removes both objects in one script
    # (data/maps/Route111/scripts.inc:57-59), nothing ever clears them, and
    # Sapphire has no Desert Underpass to recover the other -- that map is an
    # Emerald addition. So 178 is the most a fresh save can reach.
    assert progress == {
        "caught": 1,
        "seen": 2,
        "achievable": 178,
        "caught_achievable": 1,
        "percent": 0.6,
        "remaining": 177,
    }

    caught, seen = target.dex_flags(state)
    names = target.names
    assert [names.species(target.evolutions.species_of_natdex(n))
            for n in sorted(caught)] == [STARTER_NAME]
    assert [names.species(target.evolutions.species_of_natdex(n))
            for n in sorted(seen)] == [STARTER_NAME, "POOCHYENA"]
    assert "1/178 achievable owned" in target.last_progress_reason
    assert "pending either/or choice" in target.last_progress_reason
    # 179 species are NOT registered, while only 177 slots are fillable, and
    # the two numbers differ ON PURPOSE: both fossil lines are still routable
    # options the planner should offer, so neither is removed from `missing`.
    # Only the COUNT of reachable slots knows that one of them is doomed.
    assert len(target.missing(state)) == 179


def test_owned_species_reads_party_and_boxes(lab, consts):
    """The live party, not gSaveBlock1.playerParty; and the PC box slot count
    is derived from the gap between ``boxes`` and ``boxNames``
    (include/pokemon.h:323-329)."""
    target, state = lab
    from pokeagent import gamespec

    assert target.owned_species(state) == {
        consts.species[gamespec.get("sapphire").starter]
    }
    assert target.warnings == []


def test_starters_are_read_from_the_rom(lab, consts):
    """src/starter_choose.c:50, and AGENTS.md gotcha 14."""
    target, state = lab
    assert target.starters == (
        consts.species["SPECIES_TREECKO"],
        consts.species["SPECIES_TORCHIC"],
        consts.species["SPECIES_MUDKIP"],
    )  # sStarterMons order is TREECKO, TORCHIC, MUDKIP (src/starter_choose.c:50)
    owned = target.owned_species(state)
    assert target.held_starter(owned) == consts.species[STARTER_CONST]


# ---- plan() ------------------------------------------------------------------


def test_plan_covers_every_missing_species_exactly_once(lab):
    target, state = lab
    plan = target.plan(state)
    assert len(plan) == 179
    assert len({s.dex for s in plan}) == 179
    caught, _ = target.dex_flags(state)
    assert all(s.natdex not in caught for s in plan)
    assert "179 of 180 achievable species missing" in target.last_plan_reason


def test_plan_starts_with_what_costs_nothing(lab, consts):
    """A Mudkip in the party makes Marshtomp and Swampert free, so nothing
    that needs travelling can sort above them."""
    target, state = lab
    plan = target.plan(state)
    assert [s.name for s in plan[:2]] == STARTER_EVOS
    assert [s.route for s in plan[:2]] == [dex.ROUTE_EVOLVE, dex.ROUTE_EVOLVE]
    assert [s.group for s in plan[:2]] == [dex.PARTY_GROUP, dex.PARTY_GROUP]
    assert plan[0].source == consts.species[STARTER_CONST]
    assert plan[0].detail.startswith(f"you already own {STARTER_NAME}: raise")
    assert plan[1].detail == (
        f"you already own {STARTER_NAME}: raise {STARTER_NAME} to level 16, "
        f"then raise {STARTER_EVOS[0]} to level 36"
    )
    assert plan[0].cost == 0.0


def test_plan_is_grouped_so_a_session_can_sweep_one_area(lab):
    """Every step for a group is contiguous, and the actionable groups come
    before the blocked rows."""
    target, state = lab
    plan = target.plan(state)
    seen, order = set(), []
    for step in plan:
        if not order or order[-1] != step.group:
            assert step.group not in seen, f"{step.group} is not contiguous"
            seen.add(step.group)
            order.append(step.group)
    assert len(order) == len(seen)
    blocked_from = min(i for i, s in enumerate(plan) if s.blocked)
    assert all(s.blocked for s in plan[blocked_from:])


def test_plan_rows_name_real_maps_and_real_levels(lab, mapdata):
    target, state = lab
    plan = target.plan(state)
    wild = [s for s in plan if s.route == dex.ROUTE_WILD and s.map_name]
    assert wild
    for step in wild:
        assert step.map_name in mapdata.index
        assert 0 < step.min_level <= step.max_level <= 100
        assert 0 < step.chance <= 100

    torkoal = next(s for s in plan if s.name == "TORKOAL")
    assert torkoal.map_name == "FieryPath"
    assert torkoal.method == "land"
    assert torkoal.detail == (
        "walk the grass on FieryPath (L15-15, 10% of encounters)"
    )


def test_plan_resolves_an_evolution_to_a_catchable_ancestor(lab):
    """The whole point of parsing gEvolutionTable: Metagross has no catch
    location, so the step has to say where Beldum comes from and what to do
    with it afterwards."""
    target, state = lab
    plan = target.plan(state)
    metagross = next(s for s in plan if s.name == "METAGROSS")
    assert metagross.route == dex.ROUTE_EVOLVE
    assert metagross.map_name == "MossdeepCity_StevensHouse"
    assert metagross.detail == (
        "Gift at MossdeepCity_StevensHouse (L5), then raise BELDUM to "
        "level 20, then raise METANG to level 45"
    )
    assert metagross.blocked is None


def test_plan_breeds_a_baby_form_via_the_rom_evolution_graph(lab, consts):
    """Azurill has no catch location and no pre-evolution. The only handle on
    it is that it *evolves into* Marill, which is caught by surfing -- so the
    breeding parent is derived from gEvolutionTable, not from a table of baby
    forms living in this repo."""
    target, state = lab
    azurill = next(s for s in target.plan(state) if s.name == "AZURILL")
    assert azurill.route == dex.ROUTE_BREED
    assert azurill.source == consts.species["SPECIES_MARILL"]
    assert "breed MARILL at the Route 117 day care" in azurill.detail
    assert azurill.map_name  # you have to catch the Marill somewhere first
    assert {s.name for s in target.plan(state) if s.route == dex.ROUTE_BREED} == \
        {"AZURILL", "IGGLYBUFF", "PICHU"}


def test_plan_excludes_the_two_starters_you_did_not_take(lab):
    """Birch's bag opens once, and the unchosen lines are GONE, not blocked.

    They used to appear in the plan as blocked rows, which kept the target at
    a number the save could never reach -- a permanent 96%. They are now
    choice_locked: out of the plan, out of the denominator, and visible
    through choice_locked() for anyone auditing why six species vanished.
    """
    target, state = lab
    plan = {s.name: s for s in target.plan(state)}
    held = target.held_starter(target.owned_species(state))
    held_name = target.by_species[held].name.upper()
    lines = {
        "TORCHIC": ("TORCHIC", "COMBUSKEN", "BLAZIKEN"),
        "TREECKO": ("TREECKO", "GROVYLE", "SCEPTILE"),
        "MUDKIP": ("MUDKIP", "MARSHTOMP", "SWAMPERT"),
    }
    assert held_name not in plan, "a caught species must not be planned for"
    locked = target.choice_locked(state)
    locked_names = {
        target.by_natdex[n].name.upper() for n in locked
        if n in target.by_natdex
    }
    for line, members in lines.items():
        for name in members:
            if line == held_name:
                assert name not in locked_names
                if name in plan:
                    assert plan[name].blocked is None, f"{name} should be reachable"
            else:
                assert name in locked_names, name
                assert name not in plan, (
                    f"{name} is unobtainable on this save and must not be planned"
                )


def test_plan_reports_what_it_cannot_reach(lab):
    """What cannot exist on this cartridge is out of the PLAN entirely --
    the plan is an action list, and there is no action that produces a
    Jirachi. The exclusions stay auditable in out_of_reach_by_reason().
    """
    target, state = lab
    plan = {s.name: s for s in target.plan(state)}
    assert "JIRACHI" not in plan and "DEOXYS" not in plan
    events = target.out_of_reach_by_reason()[dex.OUT_OF_REACH_EVENT]
    assert [e.name for e in events] == ["Latios", "Jirachi", "Deoxys"]
    # Real but conditional acquisitions still carry their information:
    assert plan["LILEEP"].route == dex.ROUTE_FOSSIL
    assert plan["LILEEP"].blocked == "With the Root Fossil"
    assert plan["LATIAS"].route == dex.ROUTE_ROAM


def test_routes_exposes_every_alternative(lab, consts):
    """For auditing a plan row that looks wrong: all routes, cheapest first,
    with the cost that decided it."""
    target, _ = lab
    routes = target.routes(consts.species["SPECIES_TORKOAL"])
    assert len(routes) > 1
    assert all(a.cost <= b.cost for a, b in zip(routes, routes[1:])
               if (a.blocked is None) == (b.blocked is None))
    assert routes[0].map_name == "FieryPath"
    with pytest.raises(KeyError, match="regional dex"):
        target.routes(consts.species["SPECIES_BULBASAUR"])


def test_sweep_folds_the_plan_by_group(lab):
    target, state = lab
    sweep = target.sweep(state)
    plan = target.plan(state)
    assert sum(len(v) for v in sweep.values()) == len(plan)
    assert list(sweep) == list(dict.fromkeys(s.group for s in plan))


def test_dex_target_refuses_to_guess_a_game(emu, names, consts, mapdata):
    with pytest.raises(ValueError, match="needs a dex_id"):
        dex.DexTarget(emu, names, consts, mapdata)
