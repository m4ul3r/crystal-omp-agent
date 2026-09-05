"""Value-assert every parsed table against the decompilation.

The predecessor project's single most expensive defect class was not crashes
but *confidently wrong numbers*: special type ids off by nine, move accuracy
read against the wrong scale. Its retrospective calls this out as worse than
a crash, because a wrong number silently shapes decisions for whole sessions,
and its answer was a test file that asserts VALUES, not that a parser runs.

This is that file for Sapphire. Every expectation below is quoted from a
specific place in the decomp, cited in the assertion message, so a failure
tells you which file to open.

This port already had one bug of exactly that shape: `struct BattleMove` is
nine declared bytes but a twelve-byte array stride, and transcribing the nine
made TACKLE read as 0 power and SURF as 19% accuracy.
"""

import pytest

pytestmark = pytest.mark.unit


def test_symbols_agree_with_header_comments(symbols):
    """include/global.h annotates the save blocks with their addresses.

    If the linker and the header disagree, one of them is stale and every
    absolute offset in state.py is suspect.
    """
    assert symbols.addr("gSaveBlock1") == 0x02025734, "include/global.h:668"
    assert symbols.addr("gSaveBlock2") == 0x02024EA4, "include/global.h:841"


def test_symbols_land_in_the_expected_regions(symbols):
    """gPlayerParty is COMMON in IWRAM, not EWRAM (ld_script.txt:19-29).

    Assuming otherwise is the classic Gen-3 porting mistake -- the save
    block's playerParty mirror is in EWRAM and only synced at save/load.
    """
    assert symbols["gPlayerParty"].region == "iwram"
    assert symbols["gSaveBlock1"].region == "ewram"
    assert symbols["gBaseStats"].region == "rom"


def test_struct_offsets_match_the_annotations(cstruct):
    sb1 = cstruct.layout("SaveBlock1")
    for field, offset, cite in [
        ("pos", 0x00, "global.h:670"),
        ("location", 0x04, "global.h:671"),
        ("playerPartyCount", 0x234, "global.h:682"),
        ("money", 0x490, "global.h:684"),
        ("bagPocket_Items", 0x560, "global.h:689"),
        ("flags", 0x1220, "global.h flags[FLAGS_COUNT]"),
        ("vars", 0x1340, "global.h vars[VARS_COUNT]"),
    ]:
        assert sb1[field] == offset, f"{field} should be {offset:#x} per {cite}"

    mon = cstruct.layout("Pokemon", "pokemon.h")
    assert mon["box"] == 0x00 and mon["status"] == 0x50, "pokemon.h:155-168"
    assert mon["level"] == 0x54 and mon["hp"] == 0x56
    assert mon["maxHP"] == 0x58


def test_flag_ids_resolve_through_expressions(consts):
    """FLAG_BADGE01_GET is (SYSTEM_FLAGS + 0x07), not a literal.

    A parser that only accepts integer literals drops every badge and every
    trainer flag without saying so.
    """
    f = consts.flags
    assert f["SYSTEM_FLAGS"] == 0x800, "constants/flags.h:779"
    assert f["FLAG_BADGE01_GET"] == 0x807, "constants/flags.h:789"
    assert f["FLAG_BADGE08_GET"] == 0x80E, "constants/flags.h:796"
    assert f["TRAINER_FLAG_START"] == 0x500, "constants/flags.h:773"
    assert consts.vars["VARS_START"] == 0x4000, "constants/vars.h:4"


def test_charmap_latin_block_only(charmap):
    """Decoding must stop before the Hiragana block, which re-maps 01-A0."""
    assert charmap.decode(bytes([0xBB, 0xBC, 0xBD, 0xFF])) == "ABC"
    assert charmap.decode(bytes([0xD5, 0xD6, 0xFF])) == "ab"
    assert charmap.decode(bytes([0xA1, 0xAA, 0xFF])) == "09"
    # 0x02 is 'Á'. The colour constants at the tail of charmap.txt also map
    # RED = 02 and must not shadow it.
    assert charmap.decode(bytes([0x02])) == "\u00c1"
    # Multi-byte tokens: PKMN = 53 54 (charmap.txt).
    assert charmap.decode(bytes([0x53, 0x54, 0xFF])) == "PKMN"


def test_charmap_roundtrip(charmap):
    for text in ("MUDKIP", "Mudkip 09!", "RUBI"):
        assert charmap.decode(charmap.encode(text)) == text


def test_move_table_values(names, consts):
    """Gen-3 move data, from gBattleMoves.

    The stride is DERIVED (symbol size / move count) because the struct is
    nine bytes but the array element is twelve. These specific numbers are
    what caught that.
    """
    m = names.move_data(consts.moves["MOVE_TACKLE"])
    assert (m.power, m.accuracy, m.pp) == (35, 95, 35), "Gen-3 TACKLE"
    surf = names.move_data(consts.moves["MOVE_SURF"])
    assert (surf.power, surf.accuracy, surf.pp) == (95, 100, 15)
    quick = names.move_data(consts.moves["MOVE_QUICK_ATTACK"])
    assert quick.priority == 1, "QUICK ATTACK has +1 priority"
    hydro = names.move_data(consts.moves["MOVE_HYDRO_PUMP"])
    assert (hydro.power, hydro.accuracy) == (120, 80)


def test_derived_strides(names):
    """Counts and strides come out of symbol sizes, and must divide whole."""
    assert names.species_count == 412
    assert names.move_count == 355
    assert names.base_stats_stride == 28, "struct BaseStats, pokemon.h:277-308"
    assert names.move_stride == 12, "9 declared bytes, padded to 12"
    assert names.item_stride == 44, "struct Item, src/item.c:11-28"


def test_base_stats_values(names, consts):
    mud = names.base_stats(consts.species["SPECIES_MUDKIP"])
    assert (mud.hp, mud.attack, mud.defense, mud.speed) == (50, 70, 50, 40)
    assert mud.catch_rate == 45
    pika = names.base_stats(consts.species["SPECIES_PIKACHU"])
    assert pika.catch_rate == 190 and pika.speed == 90


def test_type_chart(names, consts):
    """data/type_effectiveness.inc, multiplier is decimal fixed point /10."""
    types = consts.ns("pokemon.h")
    water, fire = types["TYPE_WATER"], types["TYPE_FIRE"]
    electric, ground = types["TYPE_ELECTRIC"], types["TYPE_GROUND"]
    normal, rock = types["TYPE_NORMAL"], types["TYPE_ROCK"]
    assert names.effectiveness(water, fire) == 2.0
    assert names.effectiveness(electric, ground) == 0.0
    assert names.effectiveness(normal, rock) == 0.5
    # Absent pairs are neutral -- only non-neutral rows are in the table.
    assert names.effectiveness(normal, normal) == 1.0


def test_learnsets(names, consts):
    """(level << 9) | move, terminated by 0xFFFF."""
    mudkip = names.level_up_moves(consts.species["SPECIES_MUDKIP"])
    assert mudkip[0] == (1, consts.moves["MOVE_TACKLE"])
    assert (10, consts.moves["MOVE_WATER_GUN"]) in mudkip


def test_tmhm_learnsets(names, consts):
    """Bit index is itemId - ITEM_TM01_FOCUS_PUNCH."""
    assert names.learns_tm(
        consts.species["SPECIES_MUDKIP"], consts.items["ITEM_HM03_SURF"]
    )
    assert not names.learns_tm(
        consts.species["SPECIES_TREECKO"], consts.items["ITEM_HM03_SURF"]
    )


def test_item_table(names, consts):
    assert names.item_data(consts.items["ITEM_ULTRA_BALL"]).price == 1200
    assert names.item(consts.items["ITEM_POTION"]) == "POTION"


def test_metatile_behaviour_tables(behaviors):
    """Grass/water come from sTileBitAttributes, walls from the predicates."""
    ids = behaviors.ids
    assert behaviors.is_land_encounter(ids["MB_TALL_GRASS"])
    assert behaviors.is_surfable(ids["MB_OCEAN_WATER"])
    assert not behaviors.is_encounter(ids["MB_SHORT_GRASS"]), "short grass has none"
    # src/metatile_behavior.c:1000-1057
    assert ids["MB_IMPASSABLE_EAST"] in behaviors.blocked_sets["East"]
    assert ids["MB_IMPASSABLE_WEST_AND_EAST"] in behaviors.blocked_sets["West"]
    # The four ledges, src/metatile_behavior.c:265-296
    assert behaviors.jump_sets["South"] == {ids["MB_JUMP_SOUTH"]}


def test_starter_table_is_not_assumed(names, emu, consts):
    """src/starter_choose.c:50 -- {TREECKO, TORCHIC, MUDKIP}.

    The predecessor lost a whole leg by assuming a ball position. Read it.
    """
    raw = emu.read("sStarterMons", 6)
    got = [int.from_bytes(raw[i : i + 2], "little") for i in (0, 2, 4)]
    assert got == [
        consts.species["SPECIES_TREECKO"],
        consts.species["SPECIES_TORCHIC"],
        consts.species["SPECIES_MUDKIP"],
    ]


def test_substruct_permutation_is_a_permutation():
    """All 24 orderings, each a permutation of the four slots."""
    from pokeagent.pokemon import SUBSTRUCT_ORDER

    assert len(SUBSTRUCT_ORDER) == 24
    assert len({tuple(o) for o in SUBSTRUCT_ORDER}) == 24
    for order in SUBSTRUCT_ORDER:
        assert sorted(order) == [0, 1, 2, 3]
