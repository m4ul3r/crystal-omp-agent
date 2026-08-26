"""Type/damage tactics (session claude-wren pt9).

The type ids and the matchup chart come from the real disassembly through the
harness's own parsers, so these tests fail if the chart is ever misread. Only
the ROM `Moves` table and the live WRAM are faked.
"""

import re
from types import SimpleNamespace

import pytest

from crystalagent.battle import _parse_matchups, _parse_types
from crystalagent.paths import REPO_ROOT
from crystalagent.tactics import (MAX_DAMAGE, Tactics, damage_span,
                                  parse_trainer_items, read_side)

pytestmark = pytest.mark.unit

TYPES = _parse_types(REPO_ROOT / "constants/type_constants.asm")
MATCHUPS = _parse_matchups(REPO_ROOT / "data/types/type_matchups.asm", TYPES)

# id -> (effect_name, power, type, accuracy); effect ids are resolved from the
# real constants file by _bdata() below.
MOVES = {
    1: ("EFFECT_NORMAL_HIT", 100, "STEEL", 75),     # IRON TAIL
    2: ("EFFECT_NORMAL_HIT", 60, "DRAGON", 100),    # DRAGONBREATH
    3: ("EFFECT_STATIC_DAMAGE", 40, "DRAGON", 100),  # DRAGON RAGE
    4: ("EFFECT_PARALYZE", 0, "ELECTRIC", 100),     # THUNDER WAVE
    5: ("EFFECT_LEVEL_DAMAGE", 1, "NORMAL", 100),   # SEISMIC TOSS
    6: ("EFFECT_NORMAL_HIT", 95, "WATER", 100),     # SURF
    7: ("EFFECT_SELFDESTRUCT", 200, "NORMAL", 100),  # EXPLOSION
    8: ("EFFECT_NORMAL_HIT", 95, "ICE", 100),        # ICE BEAM
    9: ("EFFECT_ALWAYS_HIT", 60, "DARK", 100),       # FAINT ATTACK
    10: ("EFFECT_NORMAL_HIT", 60, "FLYING", 100),    # WING ATTACK
}
NAMES = {1: "IRON TAIL", 2: "DRAGONBREATH", 3: "DRAGON RAGE",
         4: "THUNDER WAVE", 5: "SEISMIC TOSS", 6: "SURF", 7: "EXPLOSION",
         8: "ICE BEAM", 9: "FAINT ATTACK", 10: "WING ATTACK"}


def _bdata():
    from crystalagent.tactics import parse_effects
    effects = parse_effects(REPO_ROOT)
    moves = {mid: {"effect": effects[eff], "power": pw,
                   "type": TYPES[ty], "accuracy": acc}
             for mid, (eff, pw, ty, acc) in MOVES.items()}
    b = SimpleNamespace(types=TYPES, matchups=MATCHUPS, moves=moves)
    b.effectiveness = lambda atk, dfn: _eff(atk, dfn)
    return b


def _eff(atk, dfn):
    m = 1.0
    for t in dict.fromkeys(dfn):
        m *= MATCHUPS.get((atk, t), 1.0)
    return m


def _tactics(badges=(), heal_table=None):
    names = SimpleNamespace(moves=NAMES, items={}, species={})
    return Tactics(_bdata(), names, REPO_ROOT, badge_types=badges,
                   heal_table=heal_table)


# Prices and cure masks as the ROM lists them (see test_parser_values.py:
# PARLYZ HEAL 200 / PAR only, FULL HEAL 600 / everything).
HEAL_TABLE = {
    "POTION": {"name": "POTION", "hp": 20, "cures": 0, "revives": False,
               "price": 300},
    "PARLYZHEAL": {"name": "PARLYZ HEAL", "hp": 0, "cures": 0x40,
                   "revives": False, "price": 200},
    "FULLHEAL": {"name": "FULL HEAL", "hp": 0, "cures": 0xFF,
                 "revives": False, "price": 600},
}


def _mon(level=50, hp=100, max_hp=100, types=("DRAGON", "FLYING"),
         attack=100, defense=100, speed=100, spatk=100, spdef=100,
         moves=(1, 2, 3, 4), acc_level=7, eva_level=7, sub3=0):
    t = [TYPES[x] for x in types]
    return {"level": level, "hp": hp, "max_hp": max_hp, "types": t,
            "type1": t[0], "type2": t[1], "attack": attack,
            "defense": defense, "speed": speed, "spatk": spatk,
            "spdef": spdef, "moves": list(moves), "status": 0,
            "acc_level": acc_level, "eva_level": eva_level, "sub3": sub3}


def t_analysis(t, me, foe):
    """A `read()`-shaped analysis without an emulator."""
    mine, threats = t.my_moves(me, foe), t.enemy_threats(me, foe)
    best = mine[0] if mine else None
    worst = threats[0] if threats else None
    return {"me": me, "enemy": foe, "moves": mine, "threats": threats,
            "faster": me["speed"] > foe["speed"],
            "my_best": best, "their_best": worst,
            "i_can_ko": bool(best and best["ko_certain"]),
            "i_die_next_turn": bool(worst and worst["min"] >= me["hp"])}


# -- the type ids must be the GAME's ids ---------------------------------

def test_type_ids_match_the_games_real_ids():
    """`const_next 19` jumps the unused block, so FIRE is 20 and DARK is 27.
    A parser that ignores it shifts every SPECIAL type down by 9, and since
    ROM move types and the WRAM type bytes are REAL ids, every special-type
    matchup lookup then misses the table and reads as a flat 1.0. Live cost:
    FAINT ATTACK was rated x1 into EXEGGUTOR when DARK is 2x on PSYCHIC."""
    assert TYPES["NORMAL"] == 0 and TYPES["STEEL"] == 9
    assert TYPES["FIRE"] == 20 and TYPES["WATER"] == 21
    assert TYPES["PSYCHIC"] == 24 and TYPES["ICE"] == 25
    assert TYPES["DRAGON"] == 26 and TYPES["DARK"] == 27


def test_the_chart_answers_for_real_ids():
    assert MATCHUPS[(27, 24)] == 2.0       # DARK -> PSYCHIC
    assert MATCHUPS[(25, 26)] == 2.0       # ICE -> DRAGON
    assert MATCHUPS[(0, 8)] == 0.0         # NORMAL -> GHOST
    assert MATCHUPS[(21, 5)] == 2.0        # WATER -> ROCK


def test_psychic_is_reachable_under_both_spellings():
    """The engine's constant is PSYCHIC_TYPE; callers write PSYCHIC."""
    assert TYPES["PSYCHIC"] == TYPES["PSYCHIC_TYPE"]


# -- the physical/special split is per TYPE in Gen 2 --------------------

def test_steel_is_physical_and_dragon_is_special():
    t = _tactics()
    assert t.category(TYPES["STEEL"]) == "physical"
    assert t.category(TYPES["DRAGON"]) == "special"
    assert t.category(TYPES["ROCK"]) == "physical"
    assert t.category(TYPES["WATER"]) == "special"


def test_iron_tail_reads_defense_and_dragonbreath_reads_spdef():
    """A wall with huge Defense but paper Sp.Def must flip which move wins --
    proving the split is honoured rather than base power being compared."""
    t = _tactics()
    me = _mon()
    wall = _mon(types=("NORMAL", "NORMAL"), defense=400, spdef=20)
    iron = t.outlook(1, me, wall)
    breath = t.outlook(2, me, wall)
    assert iron["category"] == "physical" and breath["category"] == "special"
    assert breath["max"] > iron["max"]


# -- the formula ---------------------------------------------------------

def test_stab_is_one_and_a_half_and_type_multiplier_stacks():
    plain = damage_span(50, 100, 100, 100, stab=False, mult=1.0)
    stab = damage_span(50, 100, 100, 100, stab=True, mult=1.0)
    doubled = damage_span(50, 100, 100, 100, stab=False, mult=2.0)
    assert stab[1] == plain[1] + plain[1] // 2
    assert doubled[1] == plain[1] * 2


# -- accuracy after the accuracy/evasion STAGES -------------------------
# (the 0-255 decode itself is pinned by value in test_parser_values.py)

def test_evasion_stages_cut_the_accuracy_a_move_is_reported_at():
    """Two MINIMIZEs (evasion stage 9) turn a listed-100% move into a real
    60%, exactly as CheckHit computes it (effect_commands.asm:1758). Koga's
    Muk blanking two "100%" attacks in a row is what the old, stage-blind
    number looked like live."""
    t = _tactics()
    me = _mon()
    slippery = _mon(types=("NORMAL", "NORMAL"), eva_level=9)
    v = t.outlook(2, me, slippery)               # DRAGONBREATH, listed 100
    assert v["accuracy"] == 100
    assert v["effective_accuracy"] == 60
    # at neutral stages the two numbers agree
    assert t.outlook(2, me, _mon(types=("NORMAL", "NORMAL")))[
        "effective_accuracy"] == 100


def test_never_miss_moves_ignore_the_evasion_stack():
    t = _tactics()
    me = _mon()
    slippery = _mon(types=("NORMAL", "NORMAL"), eva_level=13)
    faint = t.outlook(9, me, slippery)           # FAINT ATTACK
    assert faint["never_misses"] is True
    assert faint["effective_accuracy"] == 100


def test_a_minimize_stack_flips_which_move_scores_best():
    """DRAGONBREATH out-damages FAINT ATTACK here, but against +3 evasion
    the unmissable move is worth more expected damage."""
    t = _tactics()
    me = _mon(moves=(2, 9))
    plain = _mon(types=("NORMAL", "NORMAL"), hp=300, spdef=60, defense=60)
    assert t.my_moves(me, plain)[0]["move"] == "DRAGONBREATH"
    slippery = _mon(types=("NORMAL", "NORMAL"), hp=300, spdef=60,
                    defense=60, eva_level=10)
    assert t.my_moves(me, slippery)[0]["move"] == "FAINT ATTACK"


def test_explain_shows_both_the_effective_and_the_listed_accuracy():
    t = _tactics()
    me = _mon(moves=(2,))
    slippery = _mon(types=("NORMAL", "NORMAL"), eva_level=9)
    text = t.explain(t_analysis(t, me, slippery))
    assert "acc  60 (listed 100)" in text


# -- status costs TURNS, and the model must be told ---------------------

def test_paralysis_and_confusion_report_the_turns_they_cost():
    """25% full paralysis (effect_commands.asm:323) compounded with a 50%
    confusion self-hit (ibid.:494); sleep and freeze cost the whole turn."""
    t = _tactics()
    par = t.status_bits["PAR"]
    assert t.turn_loss({"status": 0}) == 0
    assert t.turn_loss({"status": par}) == 0.25
    assert t.turn_loss({"status": 0, "confused": True}) == 0.5
    assert t.turn_loss({"status": par, "confused": True}) == 0.625
    assert t.turn_loss({"status": 2}) == 1.0                  # SLP:2
    assert t.turn_loss({"status": t.status_bits["FRZ"]}) == 1.0
    # PSN and BRN cost HP, not turns
    assert t.turn_loss({"status": t.status_bits["PSN"]}) == 0


def test_a_turn_eating_status_is_cured_from_the_bag():
    """PAR throws away a quarter of my turns; with nothing lethal incoming
    and no kill available, the cheapest cure in the bag is the play. The
    item is named from the ROM's heal table, so it is PARLYZ HEAL (¥200)
    and not FULL HEAL (¥600)."""
    t = _tactics(heal_table=HEAL_TABLE)
    me = _mon(hp=90, moves=(2,))
    me["status"] = t.status_bits["PAR"]
    foe = _mon(types=("NORMAL", "NORMAL"), hp=300, defense=200, spdef=200,
               attack=5, spatk=5, moves=(10,))
    analysis = t_analysis(t, me, foe)
    frame = {"bag": {"PARLYZ HEAL": 2, "FULL HEAL": 1, "POTION": 3}}
    action, why = t.recommend(analysis, frame)
    assert action == ("item", "PARLYZ HEAL"), why
    assert "25% of my turns" in why


def test_a_status_with_no_cure_in_the_bag_just_attacks():
    t = _tactics(heal_table=HEAL_TABLE)
    me = _mon(hp=90, moves=(2,))
    me["status"] = t.status_bits["PAR"]
    foe = _mon(types=("NORMAL", "NORMAL"), hp=300, defense=200, spdef=200,
               attack=5, spatk=5, moves=(10,))
    action, _ = t.recommend(t_analysis(t, me, foe), {"bag": {"POTION": 3}})
    assert action == ("attack", 0)


def test_a_lethal_threat_outranks_curing_the_status():
    """A cure is a lost turn: if their best move kills me this turn, it is
    not the play."""
    t = _tactics(heal_table=HEAL_TABLE)
    me = _mon(hp=10, moves=(2,))
    me["status"] = t.status_bits["PAR"]
    killer = _mon(types=("NORMAL", "NORMAL"), hp=300, attack=300,
                  moves=(10,))
    action, _ = t.recommend(t_analysis(t, me, killer),
                            {"bag": {"PARLYZ HEAL": 2}})
    assert action != ("item", "PARLYZ HEAL")


def test_never_miss_moves_are_flagged_and_preferred():
    """FAINT ATTACK is EFFECT_ALWAYS_HIT (data/moves/moves.asm:201): it
    ignores accuracy AND evasion, the only answer to a MINIMIZE stack."""
    t = _tactics()
    me = _mon(moves=(9, 1))                 # FAINT ATTACK + IRON TAIL
    foe = _mon(types=("NORMAL", "NORMAL"), hp=20, defense=60)
    faint = t.outlook(9, me, foe)
    iron = t.outlook(1, me, foe)
    assert faint["never_misses"] is True
    assert "never misses" in faint["note"]
    assert iron["never_misses"] is False
    # both kill; the unmissable one is chosen even though IRON TAIL is bigger
    assert faint["ko_certain"] and iron["ko_certain"]
    action, why = t.recommend(t_analysis(t, me, foe))
    assert action == ("attack", 0), why
    assert "unmissable" in why


def test_a_listed_100_percent_kill_beats_a_75_percent_kill():
    """Will's Jynx: WING ATTACK and IRON TAIL both one-shot, so the bigger
    number is worth nothing and the 25% miss is worth everything."""
    t = _tactics()
    me = _mon(moves=(1, 10))                # IRON TAIL(75) + WING ATTACK(100)
    foe = _mon(types=("NORMAL", "NORMAL"), hp=30, defense=60)
    action, why = t.recommend(t_analysis(t, me, foe))
    assert action == ("attack", 1), why     # the WING ATTACK slot
    assert "100% acc" in why


def test_spread_is_85_to_100_percent():
    lo, hi = damage_span(50, 100, 100, 100, stab=False, mult=1.0)
    assert lo == hi * 217 // 255
    assert 0.84 < lo / hi < 0.86


def test_badge_boost_adds_an_eighth_and_only_for_me():
    """DoBadgeTypeBoosts returns early on the enemy's turn (hBattleTurn)."""
    t = _tactics(badges=(TYPES["DRAGON"],))
    me, foe = _mon(), _mon(types=("NORMAL", "NORMAL"))
    mine = t.outlook(2, me, foe)                      # my badge-boosted turn
    theirs = t.outlook(2, me, foe, boosted=False)     # enemy's turn
    assert mine["max"] > theirs["max"]


def test_damage_is_capped():
    lo, hi = damage_span(100, 250, 999, 1, stab=True, mult=4.0)
    assert hi == MAX_DAMAGE and lo <= MAX_DAMAGE


def test_zero_power_and_zero_multiplier_deal_nothing():
    assert damage_span(50, 0, 100, 100, stab=True, mult=2.0) == (0, 0)
    assert damage_span(50, 100, 100, 100, stab=True, mult=0.0) == (0, 0)


# -- fixed damage is not thrown away ------------------------------------

def test_dragon_rage_is_flat_40_regardless_of_stats():
    t = _tactics()
    weak = t.outlook(3, _mon(level=5, attack=1, spatk=1),
                     _mon(types=("NORMAL", "NORMAL")))
    strong = t.outlook(3, _mon(level=100, attack=999, spatk=999),
                       _mon(types=("NORMAL", "NORMAL")))
    assert weak["kind"] == "fixed" and weak["min"] == weak["max"] == 40
    assert strong["min"] == strong["max"] == 40


def test_seismic_toss_scales_with_level_not_power():
    t = _tactics()
    v = t.outlook(5, _mon(level=56), _mon(types=("NORMAL", "NORMAL")))
    assert v["min"] == v["max"] == 56


def test_a_status_move_is_reported_not_dropped():
    """The old best-move picker discarded every power-0 move, which is how
    THUNDER WAVE and DRAGON RAGE became invisible to policies."""
    t = _tactics()
    v = t.outlook(4, _mon(), _mon(types=("NORMAL", "NORMAL")))
    assert v["kind"] == "status" and v["max"] == 0
    assert v["move"] == "THUNDER WAVE"
    ids = [m["id"] for m in t.my_moves(_mon(), _mon(types=("NORMAL",
                                                           "NORMAL")))]
    assert 4 in ids


# -- immunity beats everything -----------------------------------------

def test_immunity_zeroes_even_fixed_damage():
    """NORMAL does not touch GHOST, so SEISMIC TOSS's level damage is 0."""
    t = _tactics()
    ghost = _mon(types=("GHOST", "GHOST"))
    v = t.outlook(5, _mon(), ghost)
    assert v["kind"] == "immune" and v["max"] == 0
    assert "GHOST" in v["note"]


def test_mono_type_defender_is_not_squared():
    """A mono-WATER mon stores WATER twice; the engine applies each row once."""
    t = _tactics()
    water = _mon(types=("WATER", "WATER"))
    assert t.outlook(6, _mon(types=("WATER", "WATER")), water)["mult"] == 0.5


# -- ranking and threats -----------------------------------------------

def test_a_certain_ko_outranks_bigger_average_damage():
    t = _tactics()
    me = _mon()
    nearly_dead = _mon(types=("NORMAL", "NORMAL"), hp=30, defense=50,
                       spdef=500)
    ranked = t.my_moves(me, nearly_dead)
    assert ranked[0]["ko_certain"] is True


def test_an_empty_move_is_never_recommended():
    t = _tactics()
    me, foe = _mon(), _mon(types=("NORMAL", "NORMAL"))
    ranked = t.my_moves(me, foe, pp={1: 0, 2: 5, 3: 5, 4: 5})
    assert ranked[0]["id"] != 1


def test_threats_are_sorted_worst_first_and_flag_lethality():
    t = _tactics()
    me = _mon(hp=20, defense=50, spdef=50)
    foe = _mon(types=("NORMAL", "NORMAL"), attack=300, spatk=10,
               moves=(1, 4))
    threats = t.enemy_threats(me, foe)
    assert threats[0]["max"] >= threats[-1]["max"]
    assert threats[0]["min"] >= me["hp"]


def test_explosion_is_flagged_as_a_user_faint():
    t = _tactics()
    v = t.outlook(7, _mon(), _mon(types=("NORMAL", "NORMAL")))
    assert "user faints" in v["note"]


# -- live WRAM reads ---------------------------------------------------

class FakeEmu:
    """Only the two calls read_side makes: sym lookup and banked read."""

    def __init__(self, values):
        self.values = values
        self.sym = {name: (0, i) for i, name in enumerate(values)}

    def read(self, addr, n):
        _, i = addr
        name = list(self.values)[i]
        v = self.values[name]
        return bytes(v) if isinstance(v, (list, tuple)) else bytes([v])


def test_read_side_decodes_big_endian_and_drops_empty_move_slots():
    vals = {"wBattleMonLevel": 56, "wBattleMonStatus": 0,
            "wBattleMonHP": [0, 211], "wBattleMonMaxHP": [1, 0],
            "wBattleMonAttack": [0, 150], "wBattleMonDefense": [0, 140],
            "wBattleMonSpeed": [0, 130], "wBattleMonSpclAtk": [0, 160],
            "wBattleMonSpclDef": [0, 120], "wBattleMonType1": 26,
            "wBattleMonType2": 2, "wBattleMonMoves": [17, 225, 86, 0],
            "wBattleMonPP": [35, 20, 20, 0],
            # accuracy/evasion stages and SubStatus3 (confusion) --
            # CheckHit reads these live, nothing else in WRAM carries them
            "wPlayerAccLevel": 7, "wPlayerEvaLevel": 9,
            "wPlayerSubStatus3": 0x80}
    side = read_side(FakeEmu(vals), "me")
    assert side["hp"] == 211 and side["max_hp"] == 256
    assert side["moves"] == [17, 225, 86]          # empty 4th slot dropped
    assert side["pp"] == [35, 20, 20]
    assert side["slots"] == [(0, 17, 35), (1, 225, 20), (2, 86, 20)]
    assert side["types"] == [26, 2]
    assert side["acc_level"] == 7 and side["eva_level"] == 9
    assert side["sub3"] == 0x80


# -- the recommendation ------------------------------------------------

def test_a_certain_ko_is_taken_over_a_bigger_but_uncertain_hit():
    """IRON TAIL hits harder but only 75% of the time; if the weaker,
    100%-accurate move already kills, accuracy wins."""
    t = _tactics()
    me = _mon(attack=200, spatk=200)
    dying = _mon(types=("NORMAL", "NORMAL"), hp=45, defense=100, spdef=100)
    analysis = {"me": me, "enemy": dying, "faster": True,
                "moves": t.my_moves(me, dying),
                "threats": t.enemy_threats(me, dying),
                "their_best": None, "i_can_ko": True}
    (kind, slot), why = t.recommend(analysis)
    assert kind == "attack"
    assert t.my_moves(me, dying)[0]["ko_certain"]
    assert "KOs now" in why


def test_a_doomed_mon_attacks_instead_of_switching_and_names_the_free_entry():
    """BATTLE.md §9, now enforced by recommend(): a faint lets the
    replacement enter FREE; a voluntary switch concedes the hit. So when
    ICE BEAM kills on its minimum roll and nothing KOs first, the doomed
    DRAGON/FLYING mon spends its last turns on damage -- and the reason
    says who arrives free and that they resist what killed me."""
    t = _tactics()
    me = _mon(hp=15, types=("DRAGON", "FLYING"))
    foe = _mon(types=("ICE", "ICE"), attack=300, spatk=300, moves=(8,))
    analysis = t_analysis(t, me, foe)
    assert analysis["their_best"]["mult"] == 4.0
    assert analysis["their_best"]["min"] >= me["hp"]
    frame = {"can_switch": [1, 2], "party": [
        {"index": 0, "nickname": "BROOK", "species": "DRAGONITE",
         "hp": 15, "max_hp": 200},
        {"index": 1, "nickname": "GATOR", "species": "FERALIGATR",
         "hp": 290, "max_hp": 293},
        {"index": 2, "nickname": "SNAG", "species": "SUDOWOODO",
         "hp": 130, "max_hp": 130}]}
    action, why = t.recommend(analysis, frame)
    assert action[0] == "attack", why          # never a voluntary switch
    assert "doomed" in why and "FREE" in why
    assert "GATOR" in why and "x0.5" in why    # the resisting successor


def test_the_sacrifice_line_prefers_fixed_damage_over_resisted_stab():
    """RIPTIDE vs Lance's L50 Dragonite ace (live, both clears): STAB Surf
    was resisted to ~20-24 while EFFECT_STATIC_DAMAGE Dragon Rage is a flat
    40 (data/moves/moves.asm), so the doomed ranking must take the fixed
    move, name BROOK's free entry, and count whether BROOK finishes the
    chipped HP."""
    t = _tactics()
    me = _mon(level=38, hp=30, max_hp=118, types=("WATER", "WATER"),
              speed=95, attack=90, spatk=90, moves=(6, 3))
    foe = _mon(level=50, hp=122, max_hp=162, types=("DRAGON", "FLYING"),
               speed=110, attack=140, spatk=140, defense=120, spdef=120,
               moves=(2,))
    analysis = t_analysis(t, me, foe)
    by_name = {v["move"]: v for v in analysis["moves"]}
    surf, rage = by_name["SURF"], by_name["DRAGON RAGE"]
    assert surf["mult"] == 0.5 and rage["kind"] == "fixed"
    assert rage["max"] > surf["max"]
    assert analysis["their_best"]["min"] >= me["hp"]      # doomed
    frame = {"can_switch": [1], "party": [
        {"index": 0, "nickname": "RIPTIDE", "species": "FERALIGATR"},
        {"index": 1, "nickname": "BROOK", "species": "FERALIGATR",
         "level": 42, "hp": 150, "max_hp": 152,
         "attack": 110, "defense": 100, "speed": 98,
         "spatk": 105, "spdef": 100, "moves": [8]}]}
    action, why = t.recommend(analysis, frame)
    assert action == ("attack", 1), why        # DRAGON RAGE's slot
    assert "doomed" in why and "DRAGON RAGE" in why
    assert "BROOK" in why and "FREE" in why
    assert "finish" in why                     # the successor assessment


def test_outspeeding_a_certain_ko_means_not_doomed():
    """§8 speed-rule guard: if I outspeed and certainly KO, the enemy's
    scariest move never resolves -- no sacrifice line, just the kill."""
    t = _tactics()
    me = _mon(hp=10, speed=200, moves=(10,))     # WING ATTACK certainly KOs
    foe = _mon(hp=20, max_hp=20, types=("GRASS", "GRASS"), defense=5,
               attack=300, speed=50, moves=(2,))
    threat = t.outlook(2, foe, me)
    assert threat["min"] >= me["hp"]             # looks lethal
    ko = t.outlook(10, me, foe)
    assert ko["ko_certain"] and me["speed"] > foe["speed"]
    action, why = t.recommend(t_analysis(t, me, foe))
    assert action[0] == "attack" and "KOs now" in why, why
    assert "doomed" not in why



def test_against_a_healer_ace_it_bursts_instead_of_chipping():
    """Koga healed his Crobat 10 -> 26 mid-fight: AI_TryItem gates items
    behind .IsHighestLevel (engine/battle/ai/items.asm:167) and heal items
    fire at half HP (.HealItem, ibid.:346). So when the class carries an
    HP restorer (data/trainers/attributes.asm) and the front mon IS the
    highest-level one, prefer fewer hits-to-KO over bigger expected chip:
    IRON TAIL lands less per roll but needs 2 hits where Surf needs 3."""
    t = _tactics()
    me = _mon(level=50, hp=200, max_hp=200, types=("WATER", "WATER"),
              attack=160, spatk=180, defense=100, spdef=100, moves=(6, 1))
    foe = _mon(level=50, hp=200, max_hp=200, types=("NORMAL", "NORMAL"),
               attack=40, defense=50, spdef=100, moves=(4,))
    surf, iron = t.my_moves(me, foe)
    assert surf["hits_to_ko"] == 3 and iron["hits_to_ko"] == 2
    assert t._score(surf) > t._score(iron)       # chip-greedy pick is SURF
    analysis = t_analysis(t, me, foe)
    assert t.recommend(analysis)[0] == ("attack", surf["slot"])
    items = parse_trainer_items(REPO_ROOT)
    koga_id = next(c for c, v in items.items() if v["class"] == "KOGA")
    analysis["trainer"] = {
        "class": koga_id, "class_name": "KOGA",
        "items": items[koga_id]["items"],
        "source": f"data/trainers/attributes.asm:{items[koga_id]['line']}",
        "enemy_levels": [foe["level"]]}
    action, why = t.recommend(analysis)
    assert action == ("attack", iron["slot"]), why
    assert "burst" in why and "FULL RESTORE" in why and "healed" in why


def test_expects_heal_flags_only_the_hp_healers_ace():
    """attributes.asm: Koga (:90) and Champion (:96) carry FULL_HEAL +
    FULL_RESTORE; Will (:66) MAX_POTION; Chuck (:42) FULL_HEAL only -- no
    chip to erase, so no burst bias."""
    t = _tactics()
    items = parse_trainer_items(REPO_ROOT)

    def analysis_for(class_name, level, levels):
        cls = next(c for c, v in items.items() if v["class"] == class_name)
        return {"trainer": {"class": cls, "class_name": class_name,
                            "items": items[cls]["items"],
                            "source": f"data/trainers/attributes.asm:"
                                      f"{items[cls]['line']}",
                            "enemy_levels": levels},
                "enemy": {"level": level}}

    hit = t.expects_heal(analysis_for("KOGA", 44, [40, 44]))
    assert hit and hit["heal_items"] == ["FULL RESTORE"]
    koga_id = next(c for c, v in items.items() if v["class"] == "KOGA")
    assert hit["source"] == (f"data/trainers/attributes.asm:"
                             f"{items[koga_id]['line']}")
    assert not t.expects_heal(analysis_for("KOGA", 40, [40, 44]))
    assert t.expects_heal(analysis_for("WILL", 40, [38, 40]))
    assert not t.expects_heal(analysis_for("CHUCK", 42, [42]))
    assert not t.expects_heal({"enemy": {"level": 30}})          # wild
    tr = dict(analysis_for("KOGA", 44, [44])["trainer"])
    assert not t.expects_heal({"trainer": tr})                   # no levels


def test_parse_trainer_items_reads_the_rom_table_with_provenance():
    """Every `db X, Y ; items` row of data/trainers/attributes.asm, keyed by
    the trainerclass id constants/trainer_constants.asm assigns in the same
    order (TRAINER_NONE has no entry); line numbers point at the db row."""
    items = parse_trainer_items(REPO_ROOT)
    by_name = {v["class"]: v for v in items.values()}
    assert by_name["FALKNER"] == {"class": "FALKNER", "items": [],
                                  "line": 6}
    assert by_name["KOGA"]["items"] == ["FULL_HEAL", "FULL_RESTORE"]
    assert by_name["KOGA"]["line"] == 90
    assert by_name["CHAMPION"] == {"class": "CHAMPION",
                                   "items": ["FULL_HEAL", "FULL_RESTORE"],
                                   "line": 96}
    assert by_name["RED"]["items"] == ["FULL_RESTORE", "FULL_RESTORE"]
    consts = re.findall(r"^\ttrainerclass (\w+)",
                        (REPO_ROOT / "constants/trainer_constants.asm")
                        .read_text(), re.M)
    assert set(by_name) == {c for c in consts if c != "TRAINER_NONE"}


def test_switch_scoring_reads_types_from_the_base_stats_data():
    """No types in the frame: they come from data/pokemon/base_stats."""
    t = _tactics()
    assert t.species_types["DRAGONITE"] == ["DRAGON", "FLYING"]
    assert t.species_types["SUDOWOODO"] == ["ROCK", "ROCK"]
    me = _mon(hp=10)
    foe = _mon(types=("STEEL", "STEEL"), attack=300, moves=(1,))
    opts = t.switch_options(
        t_analysis(t, me, foe),
        {"can_switch": [1, 2], "party": [
            {"index": 1, "nickname": "GATOR", "species": "FERALIGATR",
             "hp": 290, "max_hp": 293},
            {"index": 2, "nickname": "SNAG", "species": "SUDOWOODO",
             "hp": 130, "max_hp": 130}]})
    by_name = {o["nickname"]: o for o in opts}
    # STEEL is resisted by STEEL/WATER-ish bulk: FERALIGATR takes 0.5x
    assert by_name["GATOR"]["incoming_mult"] == 0.5
    assert by_name["SNAG"]["incoming_mult"] == 2.0     # STEEL beats ROCK
    assert opts[0]["nickname"] == "GATOR"


def test_bag_names_match_however_they_are_spelled():
    """observe()/the frame hand back 'FULLRESTORE'; a policy writes
    'FULL RESTORE'. Both must find the item."""
    t = _tactics()
    me = _mon(hp=20, max_hp=200, defense=500, spdef=500)
    foe = _mon(types=("NORMAL", "NORMAL"), attack=10, spatk=10, moves=(1,))
    for spelling in ("FULLRESTORE", "FULL RESTORE", "Full Restore"):
        action, _ = t.recommend(t_analysis(t, me, foe), {"bag": {spelling: 1}})
        assert action == ("item", "FULL RESTORE"), spelling


def test_it_heals_when_hurt_and_nothing_lethal_is_incoming():
    t = _tactics()
    me = _mon(hp=20, max_hp=200, defense=500, spdef=500)
    foe = _mon(types=("NORMAL", "NORMAL"), attack=10, spatk=10, moves=(1,))
    analysis = t_analysis(t, me, foe)
    action, why = t.recommend(analysis, {"bag": {"FULL RESTORE": 2}})
    assert action == ("item", "FULL RESTORE"), why


def test_it_prefers_a_full_restore_over_a_plain_potion():
    t = _tactics()
    me = _mon(hp=20, max_hp=200, defense=500, spdef=500)
    foe = _mon(types=("NORMAL", "NORMAL"), attack=10, spatk=10, moves=(1,))
    action, _ = t.recommend(t_analysis(t, me, foe),
                            {"bag": {"POTION": 5, "MAX POTION": 1}})
    assert action == ("item", "MAX POTION")


def test_a_wall_of_immunities_makes_it_use_a_status_move_not_flee():
    """Everything I have is NORMAL against a GHOST except THUNDER WAVE."""
    t = _tactics()
    me = _mon(moves=(5, 4))                 # SEISMIC TOSS + THUNDER WAVE
    ghost = _mon(types=("GHOST", "GHOST"))
    action, why = t.recommend(t_analysis(t, me, ghost))
    assert action == ("attack", 1), why     # the THUNDER WAVE slot
    assert "no damaging move connects" in why


def test_it_flees_only_when_nothing_at_all_connects():
    t = _tactics()
    me = _mon(moves=(5,))                   # SEISMIC TOSS alone
    ghost = _mon(types=("GHOST", "GHOST"))
    action, why = t.recommend(t_analysis(t, me, ghost))
    assert action == "flee" and "nothing" in why


def test_the_reason_names_the_multiplier_and_hits_to_ko():
    t = _tactics()
    me, foe = _mon(), _mon(types=("NORMAL", "NORMAL"), hp=400, defense=200,
                           spdef=200)
    _, why = t.recommend(t_analysis(t, me, foe))
    assert "x" in why and "hit(s) to KO" in why

def test_explain_prints_one_row_per_move_and_the_threats():
    t = _tactics()
    me, foe = _mon(), _mon(types=("NORMAL", "NORMAL"), moves=(1, 4))
    analysis = {
        "me": me, "enemy": foe, "faster": True,
        "moves": t.my_moves(me, foe), "threats": t.enemy_threats(me, foe),
    }
    text = t.explain(analysis)
    assert "IRON TAIL" in text and "DRAGONBREATH" in text
    assert "I move first" in text
    assert text.count("<-") == 2                    # both enemy moves shown
