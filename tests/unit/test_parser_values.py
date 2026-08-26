"""Every parser is pinned to a VALUE, not to "it returned something".

The harness has shipped three separate parser bugs that all looked fine
under structural tests and all lied to the model instead of failing:

- type ids off by 9, because ``const_next 19`` was ignored, so every
  special-type matchup silently read 1.0 (FAINT ATTACK rated x1 into a
  PSYCHIC);
- accuracy read as ``min(byte, 100)``, so every move above ~39% reported
  a flat 100% and a 50% DYNAMICPUNCH looked perfectly reliable;
- ``select_label`` reporting success off a cursor glyph.

The first two were parsers. A test that only checks "ids are distinct" or
"accuracy is in 1..100" passes for all three. So each assertion below
names a value and cites the disassembly line that proves it; if the
disassembly changes, these fail loudly rather than the harness drifting.
"""
import pytest

from crystalagent import paths, state
from crystalagent.asmconst import (parse_const_defs, parse_defs,
                                   parse_ratio_table)
from crystalagent.battle import BattleData, _parse_types
from crystalagent.charmap import Charmap
from crystalagent.names import Names
from crystalagent.nav import MapData
from crystalagent.symfile import Symbols
from crystalagent.tactics import (acc_percent, effective_accuracy,
                                 parse_badge_boosts, parse_effects,
                                 parse_species_tmhm, parse_species_types,
                                 parse_tmhm_moves)
import trek

pytestmark = pytest.mark.unit

REPO = paths.REPO_ROOT
BATTLE_CONSTANTS = REPO / "constants/battle_constants.asm"


@pytest.fixture(scope="module")
def sym():
    return Symbols(paths.SYM)


@pytest.fixture(scope="module")
def bdata(sym):
    return BattleData(REPO, sym, paths.ROM)


@pytest.fixture(scope="module")
def names(sym):
    return Names(paths.ROM, sym, Charmap(paths.CHARMAP), paths.MAP_CONSTANTS)


# -- the shared .asm walkers ---------------------------------------------

def test_status_bits_are_the_games_bits():
    """constants/battle_constants.asm:162-167: SLP is the low 3 bits as a
    turn counter, then PSN=3, BRN=4, FRZ=5, PAR=6."""
    c = parse_const_defs(BATTLE_CONSTANTS)
    assert (c["PSN"], c["BRN"], c["FRZ"], c["PAR"]) == (3, 4, 5, 6)
    assert parse_defs(BATTLE_CONSTANTS)["SLP_MASK"] == 0b111


def test_state_status_decode_uses_those_bits():
    """_status must decode with the engine's masks, because the same byte
    is what a cure item's mask is tested against."""
    assert state._STATUS_BITS == [(0x08, "PSN"), (0x10, "BRN"),
                                  (0x20, "FRZ"), (0x40, "PAR")]
    assert state.SLP_MASK == 0x07
    assert state._status(0x40) == ["PAR"]
    assert state._status(0x03) == ["SLP:3"]
    assert state._status(0x00) == []


def test_stage_bounds_and_confusion_bit():
    """BASE_STAT_LEVEL 7 / MAX_STAT_LEVEL 13 (ibid.:10-11) and
    SUBSTATUS_CONFUSED as bit 7 of SubStatus3 (ibid.:186-195)."""
    d = parse_defs(BATTLE_CONSTANTS)
    assert (d["BASE_STAT_LEVEL"], d["MAX_STAT_LEVEL"]) == (7, 13)
    assert parse_const_defs(BATTLE_CONSTANTS)["SUBSTATUS_CONFUSED"] == 7


def test_type_ids_are_the_games_ids():
    """constants/type_constants.asm: the const_next 19 jump puts FIRE at 20
    and DARK at 27."""
    t = _parse_types(REPO / "constants/type_constants.asm")
    assert (t["NORMAL"], t["STEEL"], t["CURSE_TYPE"]) == (0, 9, 19)
    assert (t["FIRE"], t["PSYCHIC"], t["DARK"]) == (20, 24, 27)


def test_accuracy_multiplier_table_matches_the_data_file():
    """data/battle/accuracy_multipliers.asm:5-17, 13 rows, 7th neutral."""
    tab = parse_ratio_table(
        REPO / "data/battle/accuracy_multipliers.asm",
        "AccuracyLevelMultipliers")
    assert len(tab) == 13
    assert tab[0] == (33, 100)      # stage 1: -6
    assert tab[6] == (1, 1)         # stage 7: neutral
    assert tab[8] == (166, 100)     # stage 9: +2


def test_effective_accuracy_matches_checkhit_arithmetic():
    """CheckHit.StatModifiers (effect_commands.asm:1758): two table passes,
    the evasion one indexed 14 - stage. Two MINIMIZEs (evasion stage 9)
    turn a listed 100% into 60%."""
    tab = parse_ratio_table(
        REPO / "data/battle/accuracy_multipliers.asm",
        "AccuracyLevelMultipliers")
    assert acc_percent(effective_accuracy(255, 7, 7, tab)) == 100
    assert acc_percent(effective_accuracy(255, 7, 9, tab)) == 60
    # +2 accuracy does NOT exactly cancel +2 evasion: the engine truncates
    # after each pass (255 * 166/100 = 423, then 423 * 60/100 = 253), so
    # the honest answer is 99%, not a tidy 100%.
    assert effective_accuracy(255, 9, 9, tab) == 253
    assert acc_percent(effective_accuracy(255, 9, 9, tab)) == 99
    # floored at 1, never zero: a 33%-accurate move at -6/+6 still exists
    assert effective_accuracy(1, 1, 13, tab) == 1


# -- ROM tables ----------------------------------------------------------

def test_rom_accuracy_decodes_to_the_real_percentages(bdata, names):
    """`percent` is "* $ff / 100" (macros/data.asm:23), so these are the
    listed percentages from data/moves/moves.asm: IRON TAIL 75,
    DYNAMICPUNCH 50, TACKLE 95, HYPER BEAM 90. The old min(byte, 100)
    reported all four as 100."""
    ids = {n: i for i, n in names.moves.items()}
    expect = {"IRON TAIL": 75, "DYNAMICPUNCH": 50, "TACKLE": 95,
              "HYPER BEAM": 90, "FAINT ATTACK": 100}
    for move, pct in expect.items():
        assert bdata.moves[ids[move]]["accuracy"] == pct, move
    # the raw byte is kept too: the stage math needs it, and 75% is 191
    assert bdata.moves[ids["IRON TAIL"]]["accuracy_raw"] == 191


def test_move_power_and_type_come_from_the_rom(bdata, names):
    ids = {n: i for i, n in names.moves.items()}
    types = _parse_types(REPO / "constants/type_constants.asm")
    tackle = bdata.moves[ids["TACKLE"]]
    assert tackle["power"] == 35 and tackle["type"] == types["NORMAL"]
    surf = bdata.moves[ids["SURF"]]
    assert surf["power"] == 95 and surf["type"] == types["WATER"]


def test_base_pps_come_from_the_rom(sym, names):
    """data/moves/moves.asm: TACKLE 35 PP, HYPER BEAM 5 PP."""
    pps = trek._load_move_base_pps(paths.ROM, sym)
    ids = {n: i for i, n in names.moves.items()}
    assert pps[ids["TACKLE"] - 1] == 35
    assert pps[ids["HYPER BEAM"] - 1] == 5


def test_effectiveness_uses_real_ids_and_dedupes_mono_types(bdata):
    """DARK->PSYCHIC is 2x; a mono-WATER mon stores WATER twice and must
    NOT be squared (CheckTypeMatchup applies a row once)."""
    t = bdata.types
    assert bdata.effectiveness(t["DARK"], [t["PSYCHIC"], t["PSYCHIC"]]) == 2.0
    assert bdata.effectiveness(t["WATER"], [t["WATER"], t["WATER"]]) == 0.5
    assert bdata.effectiveness(t["NORMAL"], [t["GHOST"], t["GHOST"]]) == 0.0


def test_heal_table_prices_and_cure_masks_come_from_the_rom(sym, names):
    """The cheapest cure for paralysis must be PARLYZ HEAL (¥200), not
    FULL HEAL (¥600) or FULL RESTORE (¥3000) -- prices and cure masks are
    the ROM's own tables."""
    table = trek._load_heal_table(paths.ROM, sym, names)
    par = 1 << parse_const_defs(BATTLE_CONSTANTS)["PAR"]
    assert table["PARLYZHEAL"]["cures"] == par
    assert table["PARLYZHEAL"]["price"] == 200
    assert table["POTION"]["hp"] == 20
    assert table["REVIVE"]["revives"] is True
    assert table["ANTIDOTE"]["cures"] == \
        1 << parse_const_defs(BATTLE_CONSTANTS)["PSN"]


def test_name_tables_start_where_the_rom_says(names):
    """Ids are 1-based: item 1 is MASTER BALL (item_constants.asm:9),
    move 1 is POUND, species 1 is BULBASAUR."""
    assert names.items[1] == "MASTER BALL"
    assert names.moves[1] == "POUND"
    assert names.species[1] == "BULBASAUR"
    assert names.species[160] == "FERALIGATR"


def test_charmap_decodes_both_cursor_glyphs():
    """constants/charmap.asm: $ec is the static-menu cursor, $ed the
    battle/scrolling one. Reading only one of them is how a menu reader
    goes blind (AGENTS.md gotcha 1)."""
    cm = Charmap(paths.CHARMAP)
    assert cm.decode(bytes([0xEC])) == "▷"
    assert cm.decode(bytes([0xED])) == "▶"


# -- disassembly data files ---------------------------------------------

def test_move_effect_ids_match_the_constants_file():
    """constants/move_effect_constants.asm: EFFECT_NORMAL_HIT is 0 and
    EFFECT_ALWAYS_HIT is 17 -- the one that ignores evasion."""
    e = parse_effects(REPO)
    assert e["EFFECT_NORMAL_HIT"] == 0
    assert e["EFFECT_ALWAYS_HIT"] == 17


def test_faint_attack_really_carries_the_always_hit_effect(bdata, names):
    ids = {n: i for i, n in names.moves.items()}
    assert bdata.moves[ids["FAINT ATTACK"]]["effect"] == \
        parse_effects(REPO)["EFFECT_ALWAYS_HIT"]


def test_badge_boost_order_is_badge_bit_order():
    """data/types/badge_type_boosts.asm, walked by DoBadgeTypeBoosts
    (engine/battle/misc.asm:147): 8 Johto + 8 Kanto entries, ZEPHYR
    (bit 0) boosting FLYING."""
    order = parse_badge_boosts(REPO)
    assert len(order) == 16
    assert order[0] == "FLYING"
    assert "PSYCHIC" in order and "PSYCHIC_TYPE" not in order


def test_species_types_come_from_base_stats():
    """data/pokemon/base_stats/feraligatr.asm:6 -- keyed by name AND by
    dex number, because the frame carries one or the other."""
    st = parse_species_types(REPO)
    assert st["FERALIGATR"] == ["WATER", "WATER"]
    assert st[160] == ["WATER", "WATER"]
    assert st["TOGETIC"] == ["NORMAL", "FLYING"]


def test_tmhm_learnsets_and_tm_numbering_come_from_the_disassembly():
    """feraligatr.asm:20 is its `tmhm` line, and TM numbering comes from
    the add_tm ORDER in constants/item_constants.asm -- item ids cannot be
    used (a plain `const ITEM_C3` sits between TM04 and TM05), and
    data/moves/tmhm_moves.asm has no literal list to read (it is an rgbds
    `for` loop over TM##_MOVE)."""
    tms = parse_tmhm_moves(REPO)
    assert tms["TM01"] == "DYNAMICPUNCH"
    assert tms["TM05"] == "ROAR"          # TM04 -> ROLLOUT, ITEM_C3 skipped
    assert tms["TM24"] == "DRAGONBREATH"  # RBY's TM24 was THUNDERBOLT
    assert tms["HM01"] == "CUT" and tms["HM07"] == "WATERFALL"
    assert len(tms) == 57                 # NUM_TMS 50 + NUM_HMS 7
    learn = parse_species_tmhm(REPO)
    assert "IRON_TAIL" in learn["FERALIGATR"]
    assert "SURF" in learn["FERALIGATR"]
    assert "ZAP_CANNON" not in learn["FERALIGATR"]
    assert learn[160] == learn["FERALIGATR"]     # keyed both ways


def test_warps_are_parsed_with_their_destination_and_index():
    """maps/WillsRoom.asm warp_events: the room's exit lands on the Indigo
    Plateau Pokecenter's 4th warp, and its two north tiles both lead to
    Koga. Routing across maps is built out of exactly this."""
    md = MapData(REPO)
    warps = md.warps["WILLS_ROOM"]
    assert warps[(5, 17)] == ("INDIGO_PLATEAU_POKECENTER_1F", 4)
    assert warps[(4, 2)] == ("KOGAS_ROOM", 1)
    assert warps[(5, 2)] == ("KOGAS_ROOM", 2)
